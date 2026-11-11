"""Bounded CPU face-aware crop planning for short-form video.

Haar detection is a lightweight first pass, not identity recognition.
No biometric templates, face crops or raw sampled frames are persisted.
Only temporary FFmpeg crop coordinates are saved with a job.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

LOGGER = logging.getLogger("uvicorn.error")


def crop_positions(centers: list[tuple[float, float]], frame_width: int, frame_height: int,
                   out_width: int = 720, out_height: int = 1280) -> list[tuple[float, int]]:
    """Translate normalized face centers into smooth, bounded FFmpeg crop x values."""
    if frame_width < 1 or frame_height < 1:
        return []
    scale = max(out_width / frame_width, out_height / frame_height)
    scaled_width = math.ceil(frame_width * scale)
    max_x = max(0, scaled_width - out_width)
    smooth = 0.5
    result = []
    for timestamp, center in centers[:60]:
        if not math.isfinite(timestamp) or timestamp < 0 or not math.isfinite(center):
            continue
        # Prevent fast camera jumps. Consecutive samples are at least 1s apart.
        smooth = smooth * 0.62 + min(0.95, max(0.05, center)) * 0.38
        x = min(max_x, max(0, round(scaled_width * smooth - out_width / 2)))
        result.append((round(timestamp, 2), x))
    return result


def write_tracking_commands(positions: list[tuple[float, int]], output: Path) -> bool:
    """Use FFmpeg sendcmd to update crop@smart x at bounded timestamps."""
    if not positions:
        return False
    lines = [f"{timestamp:.2f} crop@smart x {max(0, x)};"
             for timestamp, x in positions]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def plan_face_tracking(source: Path, start: float, duration: float, output: Path,
                       out_width: int = 720, out_height: int = 1280) -> bool:
    """Sample at most 32 frames, track the largest/nearest face and smooth crop.

    Falls back to the normal centered crop if the detector is unavailable or
    no reliable face is visible. Cost is bounded regardless of source length.
    """
    try:
        import cv2
    except ImportError:
        LOGGER.warning("OpenCV not available; using centered crop")
        return False

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        capture.release()
        return False
    try:
        width = round(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if width < 2 or height < 2 or duration < 1:
            return False
        classifier = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        if classifier.empty():
            return False
        interval = max(1.0, duration / 32)
        previous = 0.5
        samples = []
        saw_face = False
        timestamp = 0.0
        while timestamp < duration and len(samples) < 32:
            capture.set(cv2.CAP_PROP_POS_MSEC, (start + timestamp) * 1000)
            ok, frame = capture.read()
            if not ok:
                break
            # Face detection at <=384px wide to bound per-sample CPU/memory.
            factor = min(1, 384 / max(1, width))
            scaled = cv2.resize(frame, None, fx=factor, fy=factor)
            gray = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)
            faces = classifier.detectMultiScale(
                gray, scaleFactor=1.12, minNeighbors=4,
                minSize=(18, 18),
            )
            if len(faces):
                ranked = sorted(
                    faces,
                    key=lambda face: -(face[2] * face[3]) +
                    abs((face[0] + face[2] / 2) / gray.shape[1] - previous) * 600,
                )
                f = ranked[0]
                previous = (f[0] + f[2] / 2) / gray.shape[1]
                saw_face = True
            samples.append((timestamp, previous))
            timestamp += interval
        if not saw_face:
            return False
        positions = crop_positions(samples, width, height, out_width, out_height)
        return write_tracking_commands(positions, output)
    except (ValueError, OSError, cv2.error):
        LOGGER.warning("Face tracking failed; using centered crop", exc_info=True)
        return False
    finally:
        capture.release()
