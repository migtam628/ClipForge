"""CPU-bounded audio transient markers for human-reviewed music placement.

Positive volume changes can be useful edit accents, but are not guaranteed
musical beats. Never claim a detected transient is a song's tempo or downbeat.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import subprocess
import sys
from array import array
from pathlib import Path

from clipforge.recovery import load_json, save_json

VERSION = 1
SAMPLE_RATE = 8000
SAMPLES_PER_WINDOW = 640  # 80 ms resolution, cheap on CPU
MAX_ANALYSIS_SECONDS = 120


def rhythmic_accents(track: Path) -> dict:
    """Find bounded peaks in window-to-window energy growth, or abstain."""
    if not track.is_file():
        raise FileNotFoundError("Selected music file is unavailable")
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(track),
        "-t", str(MAX_ANALYSIS_SECONDS), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "s16le", "pipe:1",
    ]
    try:
        output = subprocess.run(command, capture_output=True, check=True, timeout=90).stdout
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Music inspection timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Unable to analyze audio accents in this track") from exc
    if not output or len(output) > MAX_ANALYSIS_SECONDS * SAMPLE_RATE * 2 + 4096:
        raise RuntimeError("Music PCM analysis output is invalid")
    pcm = array("h")
    pcm.frombytes(output[:len(output) // 2 * 2])
    if sys.byteorder != "little":
        pcm.byteswap()
    energies = []
    for index in range(0, len(pcm) - SAMPLES_PER_WINDOW + 1, SAMPLES_PER_WINDOW):
        frame = pcm[index:index + SAMPLES_PER_WINDOW]
        energies.append(math.sqrt(sum(value * value for value in frame) / len(frame)))
    if len(energies) < 5:
        return {"markers": [], "analyzed_seconds": round(len(pcm) / SAMPLE_RATE, 2),
                "method": "energy_transients", "review_required": True}
    flux = [0.0] + [max(0.0, new - old) for old, new in zip(energies, energies[1:])]
    floor = max(150.0, statistics.median(energies) * 0.12, max(flux) * 0.22)
    peaks = []
    for index in range(2, len(flux) - 1):
        if (flux[index] >= floor and flux[index] > flux[index - 1]
                and flux[index] >= flux[index + 1]):
            stamp = index * SAMPLES_PER_WINDOW / SAMPLE_RATE
            if not peaks or stamp - peaks[-1][0] >= 0.32:
                peaks.append((stamp, flux[index]))
            elif flux[index] > peaks[-1][1]:
                peaks[-1] = (stamp, flux[index])
    # Bound UI output, and prioritize the strongest peaks across the range.
    selected = sorted(sorted(peaks, key=lambda item: item[1], reverse=True)[:24])
    return {"markers": [round(moment, 2) for moment, _ in selected],
            "analyzed_seconds": round(len(pcm) / SAMPLE_RATE, 2),
            "method": "energy_transients", "review_required": True}


def inspect_music_rhythm(track: Path, checkpoint: Path) -> dict:
    stat = track.stat()
    signature = hashlib.sha256(json.dumps([
        VERSION, str(track.resolve()), stat.st_size, stat.st_mtime_ns,
    ]).encode()).hexdigest()
    saved = load_json(checkpoint)
    if (isinstance(saved, dict) and saved.get("signature") == signature
            and isinstance(saved.get("result"), dict)):
        return {**saved["result"], "cached": True}
    result = rhythmic_accents(track)
    save_json(checkpoint, {"signature": signature, "result": result})
    return {**result, "cached": False}
