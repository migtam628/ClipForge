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


def write_narration_captions(
    script: str, narration_seconds: float, clip_seconds: float,
    style: str, path: Path,
) -> bool:
    """Approximate word timing for TTS narration (not a claim of exact alignment).

    Future versions can use provider word timestamps for precise karaoke captions.
    """
    words = script.split()
    if not words or narration_seconds <= 0:
        return False
    span = min(float(clip_seconds), narration_seconds)
    step = span / len(words)
    synthetic = [{
        "words": [
            {"start": round(i * step, 3),
             "end": round(min(span, (i + 1) * step), 3),
             "text": word}
            for i, word in enumerate(words)
        ],
    }]
    return write_captions(synthetic, 0, clip_seconds, style, path)


def write_hook(title: str, duration: float, path: Path) -> bool:
    """Display a brief, readable opening headline within social safe margins.

    Title is data, not ASS markup; remove formatting metacharacters and
    enforce bounded length before embedding in a subtitle file.
    """
    title = " ".join(_safe(title).split())[:80].strip()
    if not title or duration < 1:
        return False
    # Prefer up to two lines, no more than 28 characters on each.
    words = title.split()
    lines = []
    current = ""
    for word in words:
        if len(current) + len(word) + (1 if current else 0) > 28 and current:
            lines.append(current)
            current = ""
        current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    if len(lines) > 2:
        lines = lines[:2]
        if len(lines[1]) > 25:
            lines[1] = lines[1][:24].rstrip() + "…"
    value = r"\N".join(lines).upper()
    # Main captions are bottom-aligned; hook occupies top safe-zone, avoiding
    # platform profile controls and ensuring the video itself stays visible.
    script = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 720", "PlayResY: 1280",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        "Style: Hook,DejaVu Sans,49,&H00FFFFFF,&H00000000,&H00000000,"
        "&H78000000,-1,0,0,0,100,100,0,0,3,3,1,8,45,45,180,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 0,{_ass_time(0)},{_ass_time(min(3.2, duration))},"
        f"Hook,,0,0,0,,{value}",
    ]
    path.write_text("\n".join(script) + "\n", encoding="utf-8")
    return True
