"""Motion fallback must only move the crop for clear, coherent foreground."""
import sys
from types import SimpleNamespace

import pytest

from clipforge import smart_frame
from clipforge.motion_frame import dominant_motion_center, plan_motion_tracking


class Mask:
    def __init__(self, regions, width=180, height=320, coverage=None):
        self.regions = regions
        self.shape = (height, width)
        self.coverage = coverage


def fake_cv2_for_masks(masks):
    class Capture:
        index = -1
        released = False

        def isOpened(self):
            return True

        def get(self, prop):
            return {3: 180, 4: 320}[prop]

        def set(self, *_):
            pass

        def read(self):
            self.index += 1
            return self.index < len(masks), SimpleNamespace(shape=(320, 180, 3))

        def release(self):
            self.released = True

    capture = Capture()

    class Subtractor:
        def apply(self, frame, **_):
            return masks[capture.index]

    fake_cv2 = SimpleNamespace(
        VideoCapture=lambda _: capture,
        CAP_PROP_FRAME_WIDTH=3,
        CAP_PROP_FRAME_HEIGHT=4,
        CAP_PROP_POS_MSEC=5,
        MORPH_ELLIPSE=6,
        MORPH_OPEN=7,
        RETR_EXTERNAL=8,
        CHAIN_APPROX_SIMPLE=9,
        createBackgroundSubtractorMOG2=lambda **_: Subtractor(),
        getStructuringElement=lambda *_: object(),
        resize=lambda frame, *_args, **_kwargs: frame,
        morphologyEx=lambda mask, *_: mask,
        dilate=lambda mask, *_args, **_kwargs: mask,
        countNonZero=lambda mask: mask.coverage if mask.coverage is not None
        else sum(w * h for _, _, w, h in mask.regions),
        findContours=lambda mask, *_: (mask.regions, None),
        contourArea=lambda r: r[2] * r[3],
        boundingRect=lambda r: r,
        error=RuntimeError,
    )
    return fake_cv2, capture


def test_motion_region_rejects_global_motion_and_multiple_subjects():
    cv2, _ = fake_cv2_for_masks([])
    assert dominant_motion_center(Mask([(20, 40, 40, 40)]), cv2) == pytest.approx((40 / 180, 60 / 320))
    assert dominant_motion_center(Mask([(20, 40, 40, 40)], coverage=30000), cv2) is None
    assert dominant_motion_center(Mask([(20, 40, 40, 40), (100, 120, 38, 38)]), cv2) is None
    assert dominant_motion_center(Mask([(20, 40, 5, 6)]), cv2) is None
    assert dominant_motion_center(Mask([(1, 60, 160, 20)]), cv2) is None


def test_motion_tracker_follows_coherent_vertical_movement(tmp_path, monkeypatch):
    masks = [Mask([]) for _ in range(3)] + [
        Mask([(70, 70 + step * 20, 40, 40)]) for step in range(6)
    ]
    cv2, capture = fake_cv2_for_masks(masks)
    monkeypatch.setitem(sys.modules, "cv2", cv2)
    output = tmp_path / "motion.txt"
    assert plan_motion_tracking(tmp_path / "src.mp4", 0, 2.25, output, 1280, 720)
    lines = output.read_text().splitlines()
    assert len(lines) > 10
    assert all("crop@smart x" in line and "crop@smart y" in line for line in lines)
    assert len({line.split("crop@smart y ")[1] for line in lines}) > 1
    assert capture.released


@pytest.mark.parametrize("regions,coverage", [
    ([(20, 40, 40, 40), (100, 120, 38, 38)], None),
    ([(20, 40, 40, 40)], 30000),
    ([(20, 40, 40, 40)], None),
])
def test_ambiguous_or_stationary_motion_keeps_center(tmp_path, monkeypatch, regions, coverage):
    masks = [Mask([]) for _ in range(3)] + [
        Mask(regions, coverage=coverage) for _ in range(6)
    ]
    cv2, capture = fake_cv2_for_masks(masks)
    monkeypatch.setitem(sys.modules, "cv2", cv2)
    output = tmp_path / "not-created.txt"
    assert not plan_motion_tracking(tmp_path / "src.mp4", 0, 2.25, output, 1280, 720)
    assert not output.exists()
    assert capture.released


def test_face_tracking_is_preferred_and_method_is_honest(tmp_path, monkeypatch):
    from clipforge import motion_frame

    events = []
    def face(*_):
        events.append("face")
        return False

    def motion(*_):
        events.append("motion")
        return True

    monkeypatch.setattr(smart_frame, "plan_face_tracking", face)
    monkeypatch.setattr(motion_frame, "plan_motion_tracking", motion)
    args = (tmp_path / "source.mp4", 0, 2, tmp_path / "commands.txt")
    assert smart_frame.plan_subject_tracking(*args) == "motion"
    assert events == ["face", "motion"]
    monkeypatch.setattr(smart_frame, "plan_face_tracking", lambda *_: True)
    events.clear()
    assert smart_frame.plan_subject_tracking(*args) == "face"
    assert events == []
    monkeypatch.setattr(smart_frame, "plan_face_tracking", lambda *_: False)
    monkeypatch.setattr(motion_frame, "plan_motion_tracking", lambda *_: False)
    assert smart_frame.plan_subject_tracking(*args) is None
