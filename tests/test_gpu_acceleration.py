"""CPU default, CUDA GPU selection, and safe fallback without physical GPU access."""
from __future__ import annotations

import subprocess
import sys
import types
from types import SimpleNamespace

import pytest

from clipforge import ai, engine
from clipforge.environment import validate_patch


def _mock_whisper(monkeypatch, *, fail_cuda=False):
    observed = []

    class FakeWhisperModel:
        def __init__(self, _name, **options):
            observed.append(options)
            if fail_cuda and options["device"] == "cuda":
                raise RuntimeError("cuDNN unavailable")

        def transcribe(self, *_args, **_kwargs):
            return iter([SimpleNamespace(
                start=0.0, end=1.0, text="hello",
                words=[SimpleNamespace(start=0.1, end=0.5, word=" hello")],
            )]), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(
        WhisperModel=FakeWhisperModel))
    return observed


def test_whisper_cpu_fallback_when_no_gpu(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_WHISPER_DEVICE", "auto")
    monkeypatch.setattr(ai, "_cuda_device_count", lambda: 0)
    models = _mock_whisper(monkeypatch)
    result = ai.transcribe(tmp_path / "source.mp4", tmp_path / "models")
    assert [item["device"] for item in models] == ["cpu"]
    assert models[0]["compute_type"] == "int8"
    assert result[0]["words"][0]["text"] == "hello"
    assert ai.whisper_acceleration()["selected"] == "cpu"


def test_whisper_auto_selects_cuda_and_falls_back_if_unusable(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_WHISPER_DEVICE", "auto")
    monkeypatch.setattr(ai, "_cuda_device_count", lambda: 1)
    models = _mock_whisper(monkeypatch, fail_cuda=True)
    assert ai.transcribe(tmp_path / "source.mp4", tmp_path / "models")
    assert [(item["device"], item["compute_type"]) for item in models] == [
        ("cuda", "float16"), ("cpu", "int8"),
    ]
    assert ai.whisper_acceleration() == {
        "requested": "auto", "selected": "cuda", "cuda_devices": 1,
        "requires_gpu": False,
    }


def test_whisper_forced_cuda_requires_usable_gpu(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_WHISPER_DEVICE", "cuda")
    monkeypatch.setattr(ai, "_cuda_device_count", lambda: 0)
    models = _mock_whisper(monkeypatch)
    with pytest.raises(RuntimeError, match="no usable CUDA GPU"):
        ai.transcribe(tmp_path / "source.mp4", tmp_path / "models")
    assert models == []


def test_whisper_forced_cpu_ignores_available_gpu(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_WHISPER_DEVICE", "cpu")
    monkeypatch.setattr(ai, "_cuda_device_count", lambda: pytest.fail(
        "CUDA probe must not run in forced CPU mode"))
    models = _mock_whisper(monkeypatch)
    ai.transcribe(tmp_path / "source.mp4", tmp_path / "models")
    assert models[0]["device"] == "cpu"


def test_video_auto_encodes_with_nvenc_and_retries_cpu(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_VIDEO_ENCODER", "auto")
    monkeypatch.setattr(engine, "_nvenc_available", lambda: True)
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        if "h264_nvenc" in command:
            raise subprocess.CalledProcessError(1, command, stderr="encoder unavailable")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(engine.subprocess, "run", run)
    engine.render_sequence(tmp_path / "source.mp4", tmp_path / "export.mp4",
                           [{"start": 4, "duration": 5}], has_source_audio=False)
    assert len(commands) == 2
    assert "h264_nvenc" in commands[0]
    assert "libx264" in commands[1]


def test_video_cpu_default_never_probes_gpu(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPFORGE_VIDEO_ENCODER", raising=False)
    monkeypatch.setattr(engine, "_nvenc_available", lambda: pytest.fail(
        "CPU should never run GPU probe"))
    commands = []
    monkeypatch.setattr(engine.subprocess, "run", lambda cmd, **_kwargs: commands.append(cmd))
    engine.render_sequence(tmp_path / "source.mp4", tmp_path / "export.mp4",
                           [{"start": 4, "duration": 5}], has_source_audio=False)
    assert "libx264" in commands[0]


def test_forced_nvenc_reports_missing_gpu_without_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_VIDEO_ENCODER", "nvenc")
    monkeypatch.setattr(engine, "_nvenc_available", lambda: False)
    with pytest.raises(RuntimeError, match="no usable NVIDIA encoder"):
        engine.render_sequence(tmp_path / "source.mp4", tmp_path / "export.mp4",
                               [{"start": 4, "duration": 5}], has_source_audio=False)


def test_hardware_settings_accept_only_explicit_modes():
    assert validate_patch({"CLIPFORGE_WHISPER_DEVICE": "auto",
                           "CLIPFORGE_VIDEO_ENCODER": "nvenc"}) == {
        "CLIPFORGE_WHISPER_DEVICE": "auto", "CLIPFORGE_VIDEO_ENCODER": "nvenc",
    }
    for setting, bad in [("CLIPFORGE_WHISPER_DEVICE", "gpu"),
                         ("CLIPFORGE_VIDEO_ENCODER", "cuda"),
                         ("CLIPFORGE_VIDEO_ENCODER", "nvenc;rm -rf /")]:
        with pytest.raises(ValueError):
            validate_patch({setting: bad})
