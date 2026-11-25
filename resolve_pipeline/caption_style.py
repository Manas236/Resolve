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
    from scipy.signal import find_peaks
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
    strength = float(np.max(envelope))
    onsets, _ = find_peaks(envelope, prominence=max(1e-6, strength * 0.07), distance=3)
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


def styled_ass(hook, lines, words, preset, end_ms, markers=(), *, experimental=False):
    _, family, hook_size, line_size, accent = PRESETS[preset]
    header = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BackColour,Bold,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV",
    ]
    for name, size in (("Hook", hook_size), ("Line", line_size), ("Word", line_size)):
        header.append(f"Style: {name},{family},{size},&H00FFFFFF,&H00000000,&H78000000,0,1,7,5,5,105,105,0")
    header.extend(["[Events]", "Format: Layer,Start,End,Style,Text"])

    def event(style, start, end, text, y, keyword=False, phrase=None):
        if start >= end or not text.strip():
            return
        width = 14 if preset == "montserrat" and style == "Hook" else 17
        payload = (_accented(text, width, accent) if keyword else
                   r"\N".join(wrap(_ass_text(text).upper(), width, break_long_words=False, break_on_hyphens=False)))
        if phrase:
            chunk, active = phrase
            pieces = [_ass_text(word.text).upper() for word in chunk]
            lengths = list(map(len, pieces))
            pieces[active] = "{\\1c" + accent + "}" + pieces[active] + "{\\1c&H00FFFFFF&}"
            cut = min(range(1, len(pieces)), key=lambda i: abs(sum(lengths[:i]) - sum(lengths[i:]))) if len(pieces) > 1 else 1
            payload = " ".join(pieces)
            if sum(lengths) + len(pieces) - 1 > width:
                payload = r"\N".join((" ".join(pieces[:cut]), " ".join(pieces[cut:])))
        pop = r"\fscx86\fscy86\t(0,85,\fscx108\fscy108)\t(85,145,\fscx100\fscy100)"
        if phrase and active:
            pop = ""
        header.append(f"Dialogue: 0,{_ass_time(start // 10)},{_ass_time((end + 9) // 10)},{style},"
                      f"{{\\pos(540,{y})\\blur0.6{pop}}}{payload}")

    boundaries = [3500, 8000, 15000, end_ms] if experimental else line_boundaries(markers, len(lines), end_ms)
    event("Hook", 0, min(1500, end_ms) if experimental or not boundaries else boundaries[0], hook, 480)
    for index, text in enumerate(lines):
        event("Line", boundaries[index], boundaries[index + 1], text, 1210, keyword=True)
    slots = max(1, (len(words) + 4) // 5)
    for start in range(slots):
        chunk = words[len(words) * start // slots:len(words) * (start + 1) // slots]
        for index, word in enumerate(chunk):
            end = chunk[index + 1].start_ms if index + 1 < len(chunk) else word.end_ms
            event("Word", max(0, word.start_ms), min(end_ms, end), word.text, 1210, phrase=(chunk, index))
    return "\n".join(header) + "\n"


# Horizontal advances from the bundled Poppins SemiBold. Libass scales by
# its 1400-unit ascender-to-descender height at the script resolution.
_POPPINS_ADVANCES = {
    'a': 678, 'b': 678, 'c': 602, 'd': 678, 'e': 617, 'f': 345, 'g': 678,
    'h': 661, 'i': 278, 'j': 278, 'k': 584, 'l': 278, 'm': 1048, 'n': 661,
    'o': 638, 'p': 678, 'q': 678, 'r': 404, 's': 545, 't': 388, 'u': 661,
    'v': 599, 'w': 844, 'x': 540, 'y': 605, 'z': 483, 'A': 716, 'B': 643,
    'C': 768, 'D': 717, 'E': 532, 'F': 530, 'G': 768, 'H': 717, 'I': 278,
    'J': 570, 'K': 663, 'L': 459, 'M': 899, 'N': 735, 'O': 785, 'P': 608,
    'Q': 787, 'R': 641, 'S': 609, 'T': 577, 'U': 697, 'V': 711, 'W': 1024,
    'X': 686, 'Y': 636, 'Z': 576, '0': 646, '1': 362, '2': 574, '3': 599,
    '4': 661, '5': 644, '6': 640, '7': 548, '8': 643, '9': 626, ' ': 238,
    '.': 260, ',': 254, '!': 354, '?': 538, '—': 927, '–': 702, ':': 263,
    ';': 324, '-': 583, "'": 195, '’': 275,
}


def paragraph_layout(text):
    words = text.split()
    widths = [sum(_POPPINS_ADVANCES.get(c, 1000) for c in word) for word in words]
    for size in range(78, 0, -1):
        limit = 910 * 1400 / size
        lines = []
        width = 0
        for word, advance in zip(words, widths):
            if advance > limit:
                break
            if not lines or width + _POPPINS_ADVANCES[" "] + advance > limit:
                lines.append(word)
                width = advance
            else:
                lines[-1] += " " + word
                width += _POPPINS_ADVANCES[" "] + advance
        else:
            if len(lines) <= 8:
                return size, lines
    raise ValueError("Paragraph exceeds the frame width.")


def quiet_ass(format_id, text, lines, words, end_ms, markers=(), *, speaker=""):
    """Let libass balance a full block inside fixed safe margins."""
    size = 108 if format_id in {"L", "F4"} else 84
    if format_id == "P":
        size, paragraph = paragraph_layout(text)
    ass = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920",
        "WrapStyle: 0", "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BackColour,Bold,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV",
        f"Style: Quiet,Poppins SemiBold,{size},&H00FFFFFF,&H60000000,&H88000000,0,1,1.5,2,5,88,88,0",
        "[Events]", "Format: Layer,Start,End,Style,Text",
    ]

    def block(start, end, body, fade=True):
        if start < end:
            effects = r"{\an5\pos(540,855)\blur0.4" + (r"\fad(400,0)" if fade else "") + "}"
            if speaker:
                body += r"\N{\fs42\fscx100\fscy100}" + _ass_text(speaker)
            ass.append(f"Dialogue: 0,{_ass_time(start // 10)},{_ass_time(end // 10)},Quiet,{effects}{body}")

    if format_id == "F1":
        groups = max(1, (len(words) + 4) // 5)
        for i in range(groups):
            chunk = words[len(words)*i//groups:len(words)*(i+1)//groups]
            for j, word in enumerate(chunk):
                body = [_ass_text(w.text) for w in chunk]
                body[j] = r"{\1c&H00DDDDDD&\u1}" + body[j] + r"{\rQuiet}"
                block(word.start_ms, min(end_ms, chunk[j+1].start_ms if j+1 < len(chunk) else word.end_ms),
                      " ".join(body), False)
    elif format_id == "P":
        block(0, end_ms, r"{\q2\fscx125\fscy125}" + r"\N".join(_ass_text(line) for line in paragraph))
    elif format_id == "F4":
        eligible = [(weight, when) for when, weight in markers if end_ms*.38 <= when <= end_ms*.66]
        turn = max(eligible)[1] if eligible else end_ms//2
        block(0, turn, _ass_text(text))
        block(turn, end_ms, _ass_text(lines[0]))
    else:
        block(0, end_ms, _ass_text(text))
    return "\n".join(ass) + "\n"
