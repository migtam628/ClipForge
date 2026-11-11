"""FFmpeg/libass timed caption file generator with three styling presets."""
from __future__ import annotations

from pathlib import Path

STYLES = {
    "classic": ("&H00FFFFFF", 54, 2),
    "bold": ("&H0000FFFF", 64, 4),
    "neon": ("&H00FF66FF", 64, 4),
}


def _ass_time(seconds: float) -> str:
    hundredths = max(0, round(seconds * 100))
    hours, rest = divmod(hundredths, 360000)
    minutes, rest = divmod(rest, 6000)
    secs, remainder = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{remainder:02d}"


def _safe(text: str) -> str:
    return text.replace("\\", "").replace("{", "").replace("}", "").replace("\n", " ")


def write_captions(transcript: list[dict], start: float, duration: float,
                   style: str, path: Path) -> bool:
    """Render 4-word phrases relative to the beginning of the clip."""
    if style not in STYLES:
        raise ValueError("Unsupported caption style")
    words = [word for segment in transcript for word in segment.get("words", [])
             if start <= word["start"] < start + duration]
    if not words:
        return False
    color, size, outline = STYLES[style]
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 720", "PlayResY: 1280",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        f"Style: Main,DejaVu Sans,{size},{color},&H000000FF,&H00000000,"
        f"&H88000000,-1,0,0,0,100,100,0,0,1,{outline},1,2,24,24,150,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for idx in range(0, len(words), 4):
        group = words[idx:idx + 4]
        begin = max(0, group[0]["start"] - start)
        end = min(duration, max(group[-1]["end"] - start, begin + 0.2))
        if end <= begin:
            continue
        label = " ".join(_safe(word["text"]) for word in group).upper()
        lines.append(f"Dialogue: 0,{_ass_time(begin)},{_ass_time(end)},Main,,0,0,0,,{label}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True
