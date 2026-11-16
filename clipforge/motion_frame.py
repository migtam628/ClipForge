"""Conservative motion-saliency crop fallback for short static-camera clips.

This is not object recognition: MOG2 foreground regions can include any moving
pixels. Reject low-confidence footage instead of jumping the crop. Sampled
video frames and masks remain in memory and are never saved.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

from clipforge.smart_frame import crop_follow_path, write_tracking_commands

LOGGER = logging.getLogger("uvicorn.error")


def dominant_motion_center(mask, cv2) -> tuple[float, float] | None:
    """Return a single credible moving region, or reject ambiguous motion.

    Ignore broad full-frame changes (camera pans/cuts/lighting) and scenes with
    multiple equally strong moving areas; neither safely identifies a subject.
    """
    height, width = mask.shape[:2]
    if min(height, width) < 2:
        return None
    coverage = cv2.countNonZero(mask) / (width * height)
    if not 0.003 <= coverage <= 0.23:
        return None
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions = sorted((cv2.contourArea(c), cv2.boundingRect(c)) for c in contours)
    regions.reverse()
    if not regions:
        return None
    area, (x, y, w, h) = regions[0]
    if area / (width * height) < 0.005 or w < 8 or h < 8:
        return None
    if len(regions) > 1 and regions[1][0] > area * 0.55:
        return None
    # Edge-to-edge moving strips are often panning or a transition, not a
    # single focal subject. Reject them even when foreground coverage is low.
    if w / width > 0.85 or h / height > 0.85:
        return None
    return ((x + w / 2) / width, (y + h / 2) / height)


def plan_motion_tracking(source: Path, start: float, duration: float, output: Path,
                         out_width: int = 720, out_height: int = 1280) -> bool:
    """Follow a dominant moving region only with repeated stable evidence.

    Up to 32 frames are sampled at <=384px wide. An unsteady camera, scene
    cuts, complex motion or low motion confidence leave the crop centered.
    """
    if not math.isfinite(duration) or not math.isfinite(start):
        return False
    if duration < 1.5 or duration > 120 or start < 0:
        return False
    try:
        import cv2
    except ImportError:
        return False

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        capture.release()
        return False
    try:
        width = round(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if min(width, height, out_width, out_height) < 2:
            return False
        subtractor = cv2.createBackgroundSubtractorMOG2(
            history=24, varThreshold=32, detectShadows=False,
        )
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        interval = max(0.25, duration / 32)
        timestamp = 0.0
        sampled = 0
        candidates: list[tuple[float, float, float]] = []
        while timestamp < duration and sampled < 32:
            capture.set(cv2.CAP_PROP_POS_MSEC, (start + timestamp) * 1000)
            ok, frame = capture.read()
            if not ok:
                break
            factor = min(1.0, 384 / width)
            scaled = cv2.resize(frame, None, fx=factor, fy=factor)
            foreground = subtractor.apply(scaled, learningRate=0.08)
            # Initial samples train the background model; their masks are not
            # evidence of a moving subject.
            if sampled >= 3:
                clean = cv2.morphologyEx(foreground, cv2.MORPH_OPEN, kernel)
                clean = cv2.dilate(clean, kernel, iterations=1)
                center = dominant_motion_center(clean, cv2)
                if center is not None:
                    candidates.append((timestamp, *center))
            sampled += 1
            timestamp += interval
        eligible = max(0, sampled - 3)
        if len(candidates) < max(3, math.ceil(eligible * 0.45)):
            return False
        xs = [x for _, x, _ in candidates]
        ys = [y for _, _, y in candidates]
        # Non-moving foreground is often an artifact/animated graphics; no
        # reason to move framing toward it over a centered crop.
        if max(max(xs) - min(xs), max(ys) - min(ys)) < 0.12:
            return False
        # Discard samples if detection jumps too far between consecutive
        # observations; that usually means the dominant object has switched.
        if any(math.dist(a[1:], b[1:]) > 0.4 for a, b in zip(candidates, candidates[1:])):
            return False
        # Center before the first reliable sample, then interpolate smoothly.
        path = [(0.0, 0.5, 0.5), *candidates]
        positions = crop_follow_path(path, width, height, out_width, out_height)
        if not positions:
            return False
        if max(x for _, x, _ in positions) - min(x for _, x, _ in positions) < 8 and \
                max(y for _, _, y in positions) - min(y for _, _, y in positions) < 8:
            return False
        return write_tracking_commands(positions, output)
    except (ValueError, OSError, cv2.error):
        LOGGER.warning("Motion tracking unavailable; keeping centered crop", exc_info=True)
        return False
    finally:
        capture.release()
