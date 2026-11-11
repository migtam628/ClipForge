"""Atomic checkpoints and safe source recovery for resumable clip jobs."""
from __future__ import annotations

import json
import os
from pathlib import Path

MEDIA_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}


def save_json(path: Path, payload: object) -> None:
    """Write a complete checkpoint before replacing the previous file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def recover_source(folder: Path, job: dict) -> Path | None:
    """Look only for source media inside the server-owned job directory.

    Previous versions did not persist source_file; include a legacy scan.
    Never treat partial .part downloads or arbitrary user paths as completed media.
    """
    recorded = job.get("source_file")
    names = [recorded] if recorded else []
    names.extend(f"source{suffix}" for suffix in sorted(MEDIA_SUFFIXES))
    for name in names:
        if not isinstance(name, str) or Path(name).name != name:
            continue
        if not name.startswith("source.") or Path(name).suffix.lower() not in MEDIA_SUFFIXES:
            continue
        path = folder / name
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None
