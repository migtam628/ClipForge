"""Ensure worker failures produce tracebacks in Docker logs."""
import logging

from clipforge import app as module


def test_failed_job_records_and_logs_full_traceback(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    _, folder = module._create("upload", module.Options(), "broken-video.mp4")

    def fail_probe(_source):
        raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")

    monkeypatch.setattr(module, "probe", fail_probe)
    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        module._process(folder, source_path=tmp_path / "missing.mp4")

    record = module._read(folder)
    assert record["status"] == "failed"
    assert "metadata_errors" in record["error"]
    errors = [event for event in caplog.records if event.exc_info]
    assert any("ClipForge job" in event.getMessage() for event in errors)
    assert any("metadata_errors" in str(event.exc_info[1]) for event in errors)
