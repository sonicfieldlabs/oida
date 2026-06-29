from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from aear.config import AearConfig
from aear.engine_base import EngineResult, EngineUnavailable, MossEngine
from aear.recipes import GenerationSettings


class MpsMossEngine(MossEngine):
    profile = "mac-mps"

    def __init__(self, config: AearConfig) -> None:
        self.config = config
        self._models: dict[str, object] = {}
        self._processors: dict[str, object] = {}
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
        return self.config.thinking_model if settings.model_kind == "thinking" else self.config.instruct_model

    def _load_pair(self, model_id: str) -> tuple[object, object]:
        if model_id in self._models:
            return self._models[model_id], self._processors[model_id]
        try:
            import torch
            from src.audio_io import load_audio  # noqa: F401
            from src.modeling_moss_audio import MossAudioModel
            from src.processing_moss_audio import MossAudioProcessor
        except Exception as exc:
            raise EngineUnavailable(
                "official MOSS-Audio repo/dependencies are unavailable; set AEAR_MOSS_AUDIO_REPO and install MOSS extras"
            ) from exc

        if self.config.resident_mode == "single":
            self._clear_loaded_models(except_model=None)

        model = MossAudioModel.from_pretrained(
            model_id,
            trust_remote_code=True,
            dtype="auto",
            device_map=self._device(),
        )
        model.eval()
        processor = MossAudioProcessor.from_pretrained(
            model_id,
            trust_remote_code=True,
            enable_time_marker=True,
        )
        self._models[model_id] = model
        self._processors[model_id] = processor
        return model, processor

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
            text = processor.decode(out[0, inputs["input_ids"].shape[1] :], skip_special_tokens=True)
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


def split_reasoning(text: str) -> tuple[str | None, str]:
    start_tag = "<think>"
    end_tag = "</think>"
    if start_tag in text and end_tag in text:
        start = text.index(start_tag) + len(start_tag)
        end = text.index(end_tag)
        return text[start:end].strip(), text[end + len(end_tag) :].strip()
    return None, text.strip()
