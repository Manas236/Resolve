"""Burn-in presets and clip-relative music timing."""
from pathlib import Path
import subprocess
from textwrap import wrap

from .captions import _ass_text, _ass_time

FONTS = Path(__file__).with_name("fonts")
PREVIEW_DIR = Path(__file__).resolve().parents[1] / "research" / "caption_presets"
PRESETS = {
    "anton": ("Anton", "Anton", 111, 95, "&H0046E9FF&"),
    "bebas": ("Bebas Neue", "Bebas Neue", 133, 109, "&H00C6FF68&"),
    "montserrat": ("Montserrat Black", "Resolve Montserrat Black", 102, 85, "&H007B88FF&"),
}


def music_markers(song, offset, duration):
    import librosa
    import numpy as np

    pcm = subprocess.check_output([
        "ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file",
        "-ss", str(offset), "-i", str(song), "-t", str(duration), "-map", "0:a:0",
        "-ac", "1", "-ar", "22050", "-f", "f32le", "pipe:1",
    ], timeout=30)
    samples = np.frombuffer(pcm, dtype="<f4")
    if samples.size < 2048 or not np.any(samples):
        return []
    rate = 22050
    envelope = librosa.onset.onset_strength(y=samples, sr=rate)
    _, beats = librosa.beat.beat_track(onset_envelope=envelope, sr=rate)
    onsets = librosa.onset.onset_detect(onset_envelope=envelope, sr=rate)
    strength = float(np.max(envelope))
    scored = {int(frame): 1.0 for frame in beats}
    for frame in onsets:
        scored[int(frame)] = max(scored.get(int(frame), 0), 1 + float(envelope[frame]) / (strength or 1))
    return [(round(frame * 512 / rate * 1000), weight) for frame, weight in sorted(scored.items())]


def line_boundaries(markers, count, duration_ms):
    if not count:
        return []
    start = min(1500, duration_ms)
    spacing = (duration_ms - start) / count
    result = []
    for index in range(count):
        target = round(start + spacing * index)
        radius = 250 if index == 0 else min(1200, spacing / 3)
        eligible = [(when, weight) for when, weight in markers
                    if abs(when - target) <= radius and (when >= start or index == 0 and when >= 1400)]
        chosen = min(eligible, key=lambda row: abs(row[0] - target) - 280 * row[1])[0] if eligible else target
        result.append(chosen)
    return [*result, duration_ms]


def _accented(text, width, accent):
    rows = [row.split() for row in wrap(_ass_text(text).upper(), width, break_long_words=False, break_on_hyphens=False)]
    candidates = [(len(word.strip(".,!?")), i, j)
                  for i, row in enumerate(rows) for j, word in enumerate(row)]
    if candidates:
        _, i, j = max(candidates, key=lambda item: item[0])
        rows[i][j] = "{\\1c" + accent + "}" + rows[i][j] + "{\\1c&H00FFFFFF&}"
    return r"\N".join(" ".join(row) for row in rows)


def styled_ass(hook, lines, words, preset, end_ms, markers=()):
    _, family, hook_size, line_size, accent = PRESETS[preset]
    header = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BackColour,Bold,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV",
    ]
    for name, size in (("Hook", hook_size), ("Line", line_size), ("Word", line_size)):
        header.append(f"Style: {name},{family},{size},&H00FFFFFF,&H00000000,&H78000000,0,1,7,5,5,105,105,0")
    header.extend(["[Events]", "Format: Layer,Start,End,Style,Text"])

    def event(style, start, end, text, y, keyword=False):
        if start >= end or not text.strip():
            return
        width = 14 if preset == "montserrat" and style == "Hook" else 17
        payload = (_accented(text, width, accent) if keyword else
                   r"\N".join(wrap(_ass_text(text).upper(), width, break_long_words=False, break_on_hyphens=False)))
        pop = r"\fscx86\fscy86\t(0,85,\fscx108\fscy108)\t(85,145,\fscx100\fscy100)"
        word_color = "\\1c" + accent if style == "Word" else ""
        header.append(f"Dialogue: 0,{_ass_time(start // 10)},{_ass_time((end + 9) // 10)},{style},"
                      f"{{\\pos(540,{y})\\blur0.6{pop}{word_color}}}{payload}")

    boundaries = line_boundaries(markers, len(lines), end_ms)
    event("Hook", 0, boundaries[0] if boundaries else min(1500, end_ms), hook, 480)
    for index, text in enumerate(lines):
        event("Line", boundaries[index], boundaries[index + 1], text, 1210, keyword=True)
    for word in words:
        event("Word", max(0, word.start_ms), min(end_ms, word.end_ms), word.text, 1210)
    return "\n".join(header) + "\n"
