"""Offline native fixture; no model construction, weights, capture or inference."""
from importlib import metadata
import json
import os
from pathlib import Path
import tempfile

import torch
import torchaudio
from torchcodec.decoders import AudioDecoder


def main():
    assert os.environ.get("HF_HUB_OFFLINE") == "1"
    expected = {"torch": "2.13.0", "torchaudio": "2.11.0", "torchcodec": "0.17.0"}
    assert {name: metadata.version(name) for name in expected} == expected
    signal = (torch.sin(torch.arange(1600) * 0.1) * 0.25).unsqueeze(0)
    assert torch.isfinite(signal).all()
    with tempfile.TemporaryDirectory(prefix="native-fixture-") as directory:
        path = Path(directory) / "synthetic.wav"
        torchaudio.save(str(path), signal, 16000)
        decoded, rate = torchaudio.load(str(path))
        assert rate == 16000 and tuple(decoded.shape) == (1, 1600)
        assert torch.isfinite(decoded).all()
        assert torch.max(torch.abs(decoded - signal)).item() < 0.001
        codec = AudioDecoder(str(path)).get_all_samples()
        assert codec.sample_rate == 16000 and tuple(codec.data.shape) == (1, 1600)
        assert torch.max(torch.abs(codec.data - signal)).item() < 0.001
    from transformers import AutoModel, AutoProcessor
    from oida.native_decoder import probe

    assert callable(AutoModel.from_pretrained) and callable(AutoProcessor.from_pretrained)
    result = probe(timeout=60)
    assert result["status"] == "supported", result.get("error_type", "native decoder unavailable")
    assert result["frames"] == 1600 and result["sample_rate"] == 16000
    assert result["native_libraries"]
    print(json.dumps({"decoder": result["status"], "library_hash_count": len(result["native_libraries"])}))
    print(json.dumps({"versions": expected, "frames": 1600, "sample_rate": 16000,
                      "scope": "synthetic CPU encode/decode and SDK import; no model or accelerator qualification"}))


if __name__ == "__main__":
    main()
