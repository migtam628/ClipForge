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


def crop_follow_path(
    centers: list[tuple[float, float, float]],
    frame_width: int, frame_height: int,
    out_width: int = 720, out_height: int = 1280,
    step: float = 0.05,
) -> list[tuple[float, int, int]]:
    """Smooth two-axis subject centers into continuous crop movement.

    The crop always fits the actual export aspect ratio. Sparse detections are
    smoothed and interpolated at 20 Hz so playback does not jump per sample.
    Clip lengths and the number of samples are already bounded by the caller.
    """
    if min(frame_width, frame_height, out_width, out_height) <= 0:
        return []
    if not math.isfinite(step) or not 0.04 <= step <= 1.0:
        raise ValueError("Tracking step must be between 0.04 and 1 second")
    scale = max(out_width / frame_width, out_height / frame_height)
    scaled_width = math.ceil(frame_width * scale)
    scaled_height = math.ceil(frame_height * scale)
    max_x = max(0, scaled_width - out_width)
    max_y = max(0, scaled_height - out_height)

    def target(x: float, y: float) -> tuple[float, float]:
        x = min(1.0, max(0.0, x))
        y = min(1.0, max(0.0, y))
        return (
            min(max_x, max(0.0, scaled_width * x - out_width / 2)),
            min(max_y, max(0.0, scaled_height * y - out_height / 2)),
        )

    points = sorted(
        (float(t), float(x), float(y)) for t, x, y in centers[:64]
        if all(math.isfinite(v) for v in (t, x, y)) and t >= 0
    )
    if not points:
        return []
    if points[-1][0] - points[0][0] > 120:
        raise ValueError("Face tracking is limited to 120-second clips")
    previous_t, first_x, first_y = points[0]
    smooth_x, smooth_y = target(first_x, first_y)
    anchors = [(previous_t, smooth_x, smooth_y)]
    for timestamp, x, y in points[1:]:
        dt = timestamp - previous_t
        if dt <= 0:
            continue
        wanted_x, wanted_y = target(x, y)
        # Fast enough to follow motion, slow enough to suppress noisy boxes.
        alpha = 1 - math.exp(-dt / 0.55)
        if max_x and abs(wanted_x - smooth_x) < out_width * 0.006:
            wanted_x = smooth_x
        if max_y and abs(wanted_y - smooth_y) < out_height * 0.006:
            wanted_y = smooth_y
        max_dx = out_width * 1.15 * dt
        max_dy = out_height * 1.15 * dt
        smooth_x += min(max_dx, max(-max_dx, (wanted_x - smooth_x) * alpha))
        smooth_y += min(max_dy, max(-max_dy, (wanted_y - smooth_y) * alpha))
        smooth_x = min(max_x, max(0.0, smooth_x))
        smooth_y = min(max_y, max(0.0, smooth_y))
        anchors.append((timestamp, smooth_x, smooth_y))
        previous_t = timestamp

    # FFmpeg sendcmd changes are instantaneous; interpolate between detection
    # anchors so the apparent camera movement remains smooth when played.
    result: list[tuple[float, int, int]] = []
    for index, (timestamp, x, y) in enumerate(anchors):
        if index:
            prior_t, prior_x, prior_y = anchors[index - 1]
            frames = math.ceil((timestamp - prior_t) / step)
            for tick in range(1, frames):
                phase = min(1.0, (tick * step) / (timestamp - prior_t))
                result.append((
                    round(prior_t + tick * step, 2),
                    round(prior_x + (x - prior_x) * phase),
                    round(prior_y + (y - prior_y) * phase),
                ))
        result.append((round(timestamp, 2), round(x), round(y)))
    return result


def write_tracking_commands(
    positions: list[tuple[float, int] | tuple[float, int, int]], output: Path,
) -> bool:
    """Use FFmpeg sendcmd to update crop@smart x and optional y coordinates."""
    if not positions:
        return False
    lines = []
    for position in positions:
        timestamp, x = position[:2]
        if len(position) == 3:
            y = position[2]
            lines.append(
                f"{timestamp:.2f} crop@smart x {max(0, x)}, "
                f"crop@smart y {max(0, y)};"
            )
        else:
            lines.append(f"{timestamp:.2f} crop@smart x {max(0, x)};")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def plan_face_tracking(source: Path, start: float, duration: float, output: Path,
                       out_width: int = 720, out_height: int = 1280) -> bool:
    """Sample at most 48 frames, track a consistent face and smooth XY crop.

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
        interval = max(0.5, duration / 48)
        previous = (0.5, 0.5)
        samples = []
        face_hits = 0
        timestamp = 0.0
        while timestamp < duration and len(samples) < 48:
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
                def score(face):
                    x, y, fw, fh = face
                    center = ((x + fw / 2) / gray.shape[1],
                              (y + fh / 2) / gray.shape[0])
                    area = (fw * fh) / (gray.shape[1] * gray.shape[0])
                    return area * 5 - math.dist(center, previous) * (0.65 if face_hits else 0)

                f = max(faces, key=score)
                previous = ((f[0] + f[2] / 2) / gray.shape[1],
                            (f[1] + f[3] / 2) / gray.shape[0])
                face_hits += 1
            samples.append((timestamp, *previous))
            timestamp += interval
        # An isolated detection can be a Haar false positive; avoid moving the
        # camera based on just one uncertain frame.
        if face_hits < 2:
            return False
        positions = crop_follow_path(samples, width, height, out_width, out_height)
        return write_tracking_commands(positions, output)
    except (ValueError, OSError, cv2.error):
        LOGGER.warning("Face tracking failed; using centered crop", exc_info=True)
        return False
    finally:
        capture.release()
