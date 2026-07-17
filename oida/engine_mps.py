from __future__ import annotations

import inspect
import logging
import re
import sys
import threading
import time
from functools import wraps
from pathlib import Path

from oida.config import HF_INSTRUCT_ID, HF_THINKING_ID, OidaConfig
from oida.engine_base import EngineResult, EngineUnavailable, MossEngine
from oida.recipes import GenerationSettings

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
        self._processors: dict[str, object] = {}
        self._model_overrides: dict[str, str] = {}
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
        override = self._model_overrides.get(model_kind)
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

    def _load_pair(self, model_id: str) -> tuple[object, object]:
        if model_id in self._models:
            return self._models[model_id], self._processors[model_id]
        try:
            from src.audio_io import load_audio  # noqa: F401
            from src.modeling_moss_audio import MossAudioModel
            from src.processing_moss_audio import MossAudioProcessor
        except Exception as exc:
            raise EngineUnavailable(
                "official MOSS-Audio repo/dependencies are unavailable; set OIDA_MOSS_AUDIO_REPO (legacy HMM_/AEAR_ accepted) and install MOSS extras"
            ) from exc

        _adapt_moss_generation(MossAudioModel)

        if self.config.resident_mode == "single":
            self._clear_loaded_models(except_model=None)

        model_source, revision = self._resolve_model_source(model_id)
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

        model_id = self._model_id(settings)
        # Serialize model load/evict + generate. FastAPI dispatches the sync endpoint
        # handlers across a worker thread pool, so without this lock two concurrent
        # requests could evict a model out from under an in-flight inference (resident
        # mode "single") or run parallel generate() calls on the one MPS device.
        with self._lock:
            model, processor = self._load_pair(model_id)
            start = time.perf_counter()
            raw_audio = load_audio(str(Path(audio_path)), sample_rate=processor.config.mel_sr)
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
            with torch.no_grad():
                out = model.generate(
                    **inputs,
                    **generation_kwargs,
                )
            text = _safe_decode(processor, out[0, inputs["input_ids"].shape[1] :])
            wall_ms = round((time.perf_counter() - start) * 1000)
        reasoning_trace, answer = split_reasoning(text)
        return EngineResult(
            text=answer,
            model=model_id,
            profile=self.profile,
            settings=settings,
            reasoning_trace=reasoning_trace,
            wall_ms=wall_ms,
        )


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
