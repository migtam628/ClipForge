"""Exact, even-pixel output canvases for common social video formats."""

OUTPUT_SIZES: dict[str, tuple[int, int]] = {
    "9:16": (720, 1280),
    "2:3": (720, 1080),
    "3:4": (720, 960),
    "4:5": (720, 900),
    "1:1": (720, 720),
    "4:3": (960, 720),
    "3:2": (1080, 720),
    "16:9": (1280, 720),
    "21:9": (1680, 720),
}
