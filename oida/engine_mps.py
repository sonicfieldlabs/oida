from __future__ import annotations

import inspect
import logging
import os
import re
import sys
import threading
import time
from akousma.resource_admission import admitted
from oida.operation_control import checkpoint

from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from oida.config import HF_INSTRUCT_ID, HF_THINKING_ID, OidaConfig
from oida.engine_base import EngineResult, EngineUnavailable, MossEngine
from oida.recipes import GenerationSettings
from oida import stage_timing
from oida.pass_provenance import WeightHashCache, pass_receipt, weight_inventory
from oida.input_binding import input_array_receipt, binding_for_receipt, verify_input_binding, unknown_binding

LOGGER = logging.getLogger(__name__)

_PINNED_HF_REVISIONS = {
    HF_INSTRUCT_ID: "6907a499dc0e87cc77c8ae0fe23fd0eb5476a02d",
    HF_THINKING_ID: "0099773e141bd410bc698c03c9a029e7c2ec8169",
}
_COMMIT_REVISION_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_HF_REPO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


class MpsMossEngine(MossEngine):
    profile = "mac-mps"

    def __init__(self, config: OidaConfig) -> None:
        self.config = config
        self._models: dict[str, object] = {}
        self._weight_provenance: dict[str, dict] = {}
        self._loaded_revisions: dict[str, str | None] = {}
        self._processors: dict[str, object] = {}
        self._model_overrides: dict[str, str] = {}
        self._load_receipts: dict[str, dict] = {}
        data_dir = getattr(config, "data_dir", None)
        self._weight_cache = WeightHashCache(
            Path(data_dir) / "cache" / "weight-inventory.json" if data_dir else None
        )
        self._lock = threading.Lock()
        if config.moss_audio_repo:
            src = config.moss_audio_repo
            if str(src) not in sys.path:
                sys.path.insert(0, str(src))

    def _device(self) -> str:
        import torch

        if torch.cuda.is_available():
            return "cuda:0"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def _model_id(self, settings: GenerationSettings) -> str:
        return self.model_id_for_kind(settings.model_kind)

    def model_id_for_kind(self, model_kind: str) -> str:
        from oida.engine_base import selected_model
        override = selected_model() or self._model_overrides.get(model_kind)
        if override:
            return override
        return (
            self.config.thinking_model
            if model_kind in {"thinking", "music", "targeted_relisten"}
            else self.config.instruct_model
        )

    def set_model(self, model_kind: str, model_id: str) -> None:
        if model_kind not in {"instruct", "thinking", "transcription", "music", "targeted_relisten"}:
            raise ValueError(f"unknown model kind: {model_kind}")
        self._model_overrides[model_kind] = model_id

    def ensure_ready(self) -> None:
        """Raise EngineUnavailable if this engine could not serve a pass.

        Cheap: it imports the MOSS modules and touches no weights, so a caller
        can ask "would you work?" without paying for a ten-gigabyte load.
        """
        self._moss_modules()

    def _moss_modules(self):
        try:
            from src.audio_io import load_audio
            from src.modeling_moss_audio import MossAudioModel
            from src.processing_moss_audio import MossAudioProcessor
        except Exception as exc:
            repo = self.config.moss_audio_repo
            where = f"looked in {repo}" if repo else "no MOSS-Audio repo is configured"
            raise EngineUnavailable(
                "official MOSS-Audio repo/dependencies are unavailable "
                f"({where}; {type(exc).__name__}: {exc}). Set OIDA_MOSS_AUDIO_REPO "
                "(legacy HMM_/AEAR_ accepted) and install the moss extras."
            ) from exc
        return load_audio, MossAudioModel, MossAudioProcessor

    def _load_pair(self, model_id: str) -> tuple[object, object]:
        if model_id in self._models:
            return self._models[model_id], self._processors[model_id]
        load_audio, MossAudioModel, MossAudioProcessor = self._moss_modules()

        _adapt_moss_generation(MossAudioModel)

        if self.config.resident_mode == "single":
            self._clear_loaded_models(except_model=None)

        model_source, revision = self._resolve_model_source(model_id)
        if revision is not None:
            # Resolve one pinned snapshot before either model or processor loading;
            # the existing explicit Hub policy above controls network permission.
            from huggingface_hub import snapshot_download
            model_source = snapshot_download(repo_id=model_source, revision=revision,
                local_files_only=self.config.hf_hub_offline)
        loading = time.perf_counter()
        verification: dict = {}
        weights = weight_inventory(
            Path(model_source), cache=self._weight_cache, verification=verification
        )
        inventory_ms = round((time.perf_counter() - loading) * 1000)
        # Transformers 5 loads checkpoint tensors on a thread pool by default.
        # On macOS/Python 3.13 this can deadlock inside safetensors' PyO3
        # initialization while holding the GIL, leaving the owner unable even
        # to answer health requests. Keep this MPS owner on the synchronous
        # checkpoint path unless the operator explicitly overrides it.
        os.environ.setdefault("HF_DEACTIVATE_ASYNC_LOAD", "1")
        model = MossAudioModel.from_pretrained(
            model_source,
            dtype="auto",
            device_map=self._device(),
            revision=revision,
            trust_remote_code=False,
            use_safetensors=True,
        )
        model.eval()
        _adapt_moss_whisper_layers(model)
        processor = _load_moss_processor(MossAudioProcessor, model_source, revision=revision)
        # Only locally resolved weight bytes are claimed. Remote cache resolution
        # is deliberately unknown here; an immutable revision is a separate fact.
        if weights.get("status") == "known":
            # Attribute the resident model to the same on-disk inventory that
            # surrounded loading; never relabel a resident model after a file edit.
            # With the cache this re-reads each file's identity on disk, not its bytes:
            # a write during loading changes the identity and forces a fresh hash.
            if weight_inventory(Path(model_source), cache=self._weight_cache) != weights:
                raise EngineUnavailable("Weights changed during model loading")
        self._weight_provenance[model_id] = weights
        self._load_receipts[model_id] = {
            "load_ms": round((time.perf_counter() - loading) * 1000),
            "inventory_ms": inventory_ms,
            "weights_verification": verification or None,
        }
        self._loaded_revisions[model_id] = revision or getattr(getattr(model, "config", None), "_commit_hash", None)
        self._models[model_id] = model
        self._processors[model_id] = processor
        return model, processor

    def _resolve_model_source(self, model_id: str) -> tuple[str, str | None]:
        path = Path(model_id).expanduser()
        if path.exists():
            return str(path), None
        if path.is_absolute() or model_id.startswith((".", "~")):
            raise EngineUnavailable(f"MOSS-Audio local model path is not available: {path}")
        if self.config.hf_hub_offline:
            raise EngineUnavailable("HF_HUB_OFFLINE is set; refusing Hugging Face model lookup.")
        if not self.config.allow_hf_hub:
            raise EngineUnavailable(
                "Hugging Face model lookup is disabled by default. Download weights into ./weights or set OIDA_ALLOW_HF_HUB=1."
            )

        source = model_id
        revision = _PINNED_HF_REVISIONS.get(source)
        if revision is None and "@" in model_id:
            source, revision = model_id.rsplit("@", 1)
        if (
            _HF_REPO_ID_RE.fullmatch(source) is None
            or revision is None
            or _COMMIT_REVISION_RE.fullmatch(revision) is None
        ):
            raise EngineUnavailable(
                "Remote MOSS models require an immutable commit revision: use repository@<40-character-commit>."
            )
        LOGGER.warning(
            "Hugging Face hub lookup explicitly enabled; '%s' at commit %.12s may be downloaded from the network.",
            source,
            revision,
        )
        return source, revision.lower()

    def _kind_of_loaded(self, model_id: str, requested_kind: str) -> tuple[str, str]:
        """The kind of the model actually loaded, and how that was established.

        Found by qualifying MOSS-Audio-4B-Thinking for the first time (G7). A
        request that selects the model by id rather than by kind leaves
        ``settings.model_kind`` at its default of "instruct", and the receipt then
        recorded the right weights beside the wrong kind — a record asserting that
        a listening used the instruct model when it used the thinking one.

        The weights are the ground truth here, so the kind is derived from the
        model actually loaded and the requested kind is kept beside it when the
        two disagree. Nothing is silently corrected: a disagreement is recorded,
        because it usually means a caller asked for something it did not get.
        """
        thinking = str(getattr(self.config, "thinking_model", "") or "")
        instruct = str(getattr(self.config, "instruct_model", "") or "")
        if thinking and str(model_id) == thinking:
            actual = "thinking"
        elif instruct and str(model_id) == instruct:
            actual = "instruct"
        else:
            # An override or an unknown path: the request is the only thing that
            # says what this was meant to be, and it says so as a request.
            return requested_kind, "requested; the loaded model is not a configured pair member"
        if actual == requested_kind:
            return actual, "loaded_model"
        return actual, f"loaded_model; the request asked for {requested_kind!r}"

    def _input_receipt(self, model_id, model_kind, raw_audio, rate):
        kind, kind_basis = self._kind_of_loaded(model_id, model_kind)
        return pass_receipt(model=model_id, provider="local-moss", model_kind=kind,
            model_kind_basis=kind_basis,
            revision=self._loaded_revisions.get(model_id),
            revision_basis="loaded_model" if self._loaded_revisions.get(model_id) else "unknown",
            weights=self._weight_provenance.get(model_id),
            effective_input=input_array_receipt(raw_audio, rate))

    def prepare_input_binding(self, audio_path: str, model_kind: str = "instruct") -> dict:
        # Preparation never loads weights or changes residency. The same loader
        # and receipt constructor are used by generate under this model lock.
        with self._locked_until_deadline():
            model_id = self.model_id_for_kind(model_kind)
            if model_id not in self._models or model_id not in self._processors:
                return unknown_binding("Selected local model is not loaded")
            try:
                from src.audio_io import load_audio
            except ImportError:
                return unknown_binding("Loaded adapter audio preprocessing is unavailable")
            rate = int(self._processors[model_id].config.mel_sr)
            raw_audio = load_audio(str(Path(audio_path)), sample_rate=rate)
            return binding_for_receipt(self._input_receipt(model_id, model_kind, raw_audio, rate))

    @contextmanager
    def _locked_until_deadline(self):
        """Hold the model lock, waiting no longer than the caller's deadline.

        Found at runtime on 24 September: a listening whose deadline was six seconds away
        waited 31 s here, behind another listening, because preparation took this lock
        without a bound before generate's own bounded wait was ever reached.
        """
        from oida.operation_control import DeadlineReached, current_deadline

        deadline = current_deadline()
        if deadline is None:
            self._lock.acquire()
        elif deadline - time.time() <= 0 or not self._lock.acquire(timeout=deadline - time.time()):
            raise DeadlineReached(
                "operation deadline reached while waiting for the audio model; nothing was generated"
            )
        try:
            yield
        finally:
            self._lock.release()

    @admitted("oida", checkpoint=lambda *a, **k: checkpoint())
    def prewarm(self, model_kind: str = "instruct") -> None:
        model_id = self.model_id_for_kind(model_kind)
        with self._lock:
            self._load_pair(model_id)

    def runtime_status(self) -> dict[str, object]:
        device = None
        try:
            device = self._device()
        except Exception:
            pass
        return {
            "profile": self.profile,
            # list() snapshots the keys so a concurrent _load_pair cannot
            # mutate the dict mid-iteration (without blocking on the load lock)
            "loaded_models": [Path(model_id).name for model_id in list(self._models)],
            "device": device,
            "thinking_budget_supported": False,
            "assignments": {
                "instruct": Path(self.model_id_for_kind("instruct")).name,
                "thinking": Path(self.model_id_for_kind("thinking")).name,
                "transcription": Path(self.model_id_for_kind("transcription")).name,
                "music": Path(self.model_id_for_kind("music")).name,
                "targeted_relisten": Path(self.model_id_for_kind("targeted_relisten")).name,
            },
        }

    def _clear_loaded_models(self, except_model: str | None) -> None:
        if except_model is not None and set(self._models) == {except_model}:
            return
        self._models = {key: value for key, value in self._models.items() if key == except_model}
        self._processors = {key: value for key, value in self._processors.items() if key == except_model}
        self._weight_provenance = {key: value for key, value in self._weight_provenance.items() if key == except_model}
        self._loaded_revisions = {key: value for key, value in self._loaded_revisions.items() if key == except_model}
        self._load_receipts = {key: value for key, value in self._load_receipts.items() if key == except_model}
        try:
            import gc
            import torch

            gc.collect()
            if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                torch.mps.empty_cache()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    @admitted("oida", checkpoint=lambda *a, **k: checkpoint())
    def generate(
        self,
        audio_path: str,
        prompt: str,
        settings: GenerationSettings,
        thinking_budget: int | None = None,
    ) -> EngineResult:
        if thinking_budget is not None:
            if thinking_budget < 0:
                raise ValueError("thinking_budget must be greater than or equal to zero")
            raise EngineUnavailable(
                "thinking budgets are not supported by the embedded Transformers runtime; "
                "omit the budget or use SGLang with its configured logit processor"
            )
        try:
            import torch
            from src.audio_io import load_audio
        except Exception as exc:
            raise EngineUnavailable("MOSS-Audio runtime dependencies are unavailable") from exc

        from oida.operation_control import DeadlineReached, current_deadline

        model_id = self._model_id(settings)
        deadline = current_deadline()
        # Serialize model load/evict + generate. FastAPI dispatches the sync endpoint
        # handlers across a worker thread pool, so without this lock two concurrent
        # requests could evict a model out from under an in-flight inference (resident
        # mode "single") or run parallel generate() calls on the one MPS device.
        # Waiting for it is timed and, under a caller's deadline, bounded.
        waiting = time.perf_counter()
        if deadline is None:
            self._lock.acquire()
        elif deadline - time.time() <= 0 or not self._lock.acquire(timeout=deadline - time.time()):
            raise DeadlineReached(
                "operation deadline reached while waiting for the audio model; nothing was generated"
            )
        wait_ms = round((time.perf_counter() - waiting) * 1000)
        try:
            cold = model_id not in self._models
            loading = time.perf_counter()
            model, processor = self._load_pair(model_id)
            load_ms = round((time.perf_counter() - loading) * 1000)
            start = time.perf_counter()
            raw_audio = load_audio(str(Path(audio_path)), sample_rate=processor.config.mel_sr)
            rate = int(processor.config.mel_sr)
            provenance = self._input_receipt(model_id, settings.model_kind, raw_audio, rate)
            binding = verify_input_binding(provenance, requested_kind=settings.model_kind)
            provenance["input_binding_id"] = binding["binding_id"]
            inputs = processor(text=prompt, audios=[raw_audio], return_tensors="pt")
            inputs = inputs.to(model.device)
            if inputs.get("audio_data") is not None:
                inputs["audio_data"] = inputs["audio_data"].to(model.dtype)
            inputs["audio_input_mask"] = inputs["input_ids"] == processor.audio_token_id
            do_sample = settings.temperature > 0
            generation_kwargs = {
                "max_new_tokens": settings.max_new_tokens,
                "do_sample": do_sample,
                "num_beams": 1,
                "pad_token_id": processor.tokenizer.eos_token_id,
                "use_cache": True,
                "remove_invalid_values": True,
                "renormalize_logits": True,
            }
            if do_sample:
                generation_kwargs.update(
                    {
                        "temperature": settings.temperature,
                        "top_p": settings.top_p,
                        "top_k": settings.top_k,
                    }
                )
            if deadline is not None:
                remaining = deadline - time.time()
                if remaining < 1:
                    raise DeadlineReached(
                        "operation deadline reached before generation; nothing was generated"
                    )
                # transformers stops generating once this much wall time has passed.
                generation_kwargs["max_time"] = remaining
            memory_at_start = host_memory()
            generating = time.perf_counter()
            with torch.no_grad():
                out = model.generate(
                    **inputs,
                    **generation_kwargs,
                )
            new_ids = out[0, inputs["input_ids"].shape[1] :]
            generated = int(new_ids.shape[0])
            eos = processor.tokenizer.eos_token_id
            if generated and int(new_ids[-1]) == eos:
                stop = "eos"
            elif generated >= settings.max_new_tokens:
                stop = "max_new_tokens"
            elif deadline is not None and time.time() >= deadline - 0.25:
                stop = "deadline"
            else:
                stop = "other"
            loaded = self._load_receipts.get(model_id) or {}
            provenance["generation"] = {
                "engine_wait_ms": wait_ms,
                # A cold pass loaded its model first; the load is not generation time.
                "cold_load": cold,
                "load_ms": load_ms if cold else 0,
                **({"inventory_ms": loaded.get("inventory_ms")} if cold else {}),
                "preprocess_ms": round((generating - start) * 1000),
                "generate_ms": round((time.perf_counter() - generating) * 1000),
                "new_tokens": generated,
                "max_new_tokens": settings.max_new_tokens,
                "stop_reason": stop,
                "deadline_bound": deadline is not None,
                **(
                    {"host_memory": {"start": memory_at_start, "end": host_memory()}}
                    if memory_at_start
                    else {}
                ),
            }
            if cold and loaded.get("weights_verification"):
                provenance["weights_verification"] = loaded["weights_verification"]
            if cold:
                stage_timing.record("model_load", load_ms)
            stage_timing.record("generate", provenance["generation"]["generate_ms"])
            if stop == "deadline":
                raise DeadlineReached(
                    "operation deadline reached during generation; the partial output was discarded"
                )
            text = _safe_decode(processor, new_ids)
            wall_ms = round((time.perf_counter() - start) * 1000)
        finally:
            self._lock.release()
        reasoning_trace, answer = split_reasoning(text)
        return EngineResult(
            text=answer,
            model=model_id,
            profile=self.profile,
            settings=settings,
            reasoning_trace=reasoning_trace,
            wall_ms=wall_ms,
            pass_provenance=[provenance],
        )


def host_memory() -> dict | None:
    """The machine's memory state around a pass, for reading a slow one afterwards.

    A pass on 24 September ran at 0.6 tokens/s and nothing retained could say why. That
    evening this Mac had 38.7 GB of 39.9 GB of swap in use, and a second, long-running
    MOSS process held 25 GB; the Testing owner's own model was mostly compressed. Swap,
    available memory and the kernel's pressure level (1 normal, 2 warning, 4 critical)
    are cheap to read and name no content.
    """
    try:
        import psutil

        virtual, swap = psutil.virtual_memory(), psutil.swap_memory()
        value = {
            "available_mb": round(virtual.available / 2**20),
            "swap_used_mb": round(swap.used / 2**20),
        }
    except Exception:
        return None
    if sys.platform == "darwin":
        try:
            import subprocess

            level = subprocess.run(
                ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                capture_output=True, text=True, timeout=1,
            ).stdout.strip()
            if level.isdigit():
                value["pressure_level"] = int(level)
        except (OSError, subprocess.SubprocessError):
            pass
    return value


def _safe_decode(processor, token_ids) -> str:
    """Decode generated ids, tolerating ids outside the text vocabulary.

    On some inputs MOSS emits audio/special ids the base tokenizer cannot map;
    convert_ids_to_tokens then yields None entries and the plain decode joins
    them into a TypeError. Filter those out instead of failing the listen.
    """
    try:
        return processor.decode(token_ids, skip_special_tokens=True)
    except TypeError:
        tokenizer = getattr(processor, "_base_tokenizer", None) or getattr(processor, "tokenizer", None)
        if tokenizer is None:
            raise
        ids = token_ids.tolist() if hasattr(token_ids, "tolist") else list(token_ids)
        tokens = tokenizer.convert_ids_to_tokens(ids, skip_special_tokens=True)
        return tokenizer.convert_tokens_to_string([token for token in tokens if isinstance(token, str)]).strip()


def _adapt_whisper_encoder_layer(layer) -> None:
    """Accept MOSS-Audio's removed, always-null ``layer_head_mask`` argument."""
    forward = layer.forward
    if getattr(forward, "_oida_moss_compat", False):
        return
    if "layer_head_mask" in inspect.signature(forward).parameters:
        return

    @wraps(forward)
    def compatible_forward(*args, layer_head_mask=None, **kwargs):
        if layer_head_mask is not None:
            raise ValueError("Transformers 5 no longer supports Whisper layer head masks")
        result = forward(*args, **kwargs)
        return result if isinstance(result, (tuple, list)) else (result,)

    compatible_forward._oida_moss_compat = True  # type: ignore[attr-defined]
    layer.forward = compatible_forward


def _adapt_moss_whisper_layers(model) -> None:
    audio_encoder = getattr(model, "audio_encoder", None)
    for layer in getattr(audio_encoder, "layers", ()):
        _adapt_whisper_encoder_layer(layer)


def _adapt_moss_generation(model_cls: type) -> None:
    """Keep one-shot audio inputs out of cached Transformers 5 decode steps."""
    prepare = model_cls.prepare_inputs_for_generation
    if getattr(prepare, "_oida_moss_compat", False):
        return

    @wraps(prepare)
    def compatible_prepare(self, input_ids, *args, **kwargs):
        model_inputs = prepare(self, input_ids, *args, **kwargs)
        audio_input_mask = kwargs.get("audio_input_mask")
        if (
            audio_input_mask is not None
            and input_ids is not None
            and input_ids.shape[-1] > audio_input_mask.shape[-1]
        ):
            model_inputs.pop("inputs_embeds", None)
            model_inputs["input_ids"] = input_ids[:, -1:]
            position_ids = model_inputs.get("position_ids")
            if position_ids is not None:
                model_inputs["position_ids"] = position_ids[:, -1:]
            model_inputs["audio_data"] = None
            model_inputs["audio_input_mask"] = None
            model_inputs["audio_data_seqlens"] = None
        return model_inputs

    compatible_prepare._oida_moss_compat = True  # type: ignore[attr-defined]
    model_cls.prepare_inputs_for_generation = compatible_prepare


def _load_moss_processor(processor_cls: type, model_id: str, *, revision: str | None):
    """Load the standard tokenizer without executing checkpoint Python code."""
    from transformers import Qwen2Tokenizer

    tokenizer = Qwen2Tokenizer.from_pretrained(model_id, revision=revision)
    return processor_cls(tokenizer, enable_time_marker=True)


def split_reasoning(text: str) -> tuple[str | None, str]:
    start_tag = "<think>"
    end_tag = "</think>"
    if start_tag in text and end_tag in text:
        start = text.index(start_tag) + len(start_tag)
        end = text.index(end_tag)
        return text[start:end].strip(), text[end + len(end_tag) :].strip()
    return None, text.strip()
