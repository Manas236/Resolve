"""Clip-relative subtitles from timestamped transcription cues."""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Cue:
    start_ms: int
    end_ms: int
    text: str


def _timecode(milliseconds: int) -> str:
    seconds, fraction = divmod(milliseconds, 1000)
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02}:{minute:02}:{second:02},{fraction:03}"


def clip_srt(cues: Iterable[Cue], *, start_ms: int, end_ms: int) -> str:
    """Export intersecting transcription cues on the clipped media timeline."""
    if not 0 <= start_ms < end_ms:
        raise ValueError("Invalid clip interval")
    blocks = []
    for cue in sorted(cues, key=lambda cue: (cue.start_ms, cue.end_ms)):
        if not 0 <= cue.start_ms < cue.end_ms:
            raise ValueError("Invalid cue interval")
        start = max(start_ms, cue.start_ms)
        end = min(end_ms, cue.end_ms)
        text = " ".join(cue.text.split())
        if start < end and text:
            blocks.append(
                f"{len(blocks) + 1}\n{_timecode(start - start_ms)} --> "
                f"{_timecode(end - start_ms)}\n{text}\n"
            )
    return "\n".join(blocks)


def _ass_time(centiseconds):
    seconds, fraction = divmod(centiseconds, 100)
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours}:{minute:02}:{second:02}.{fraction:02}"


def _ass_text(text):
    return " ".join(text.split()).translate(str.maketrans({"{": "｛", "}": "｝", "\\": "＼"}))


def highlighted_ass(words: Iterable[Cue], *, start_ms: int, end_ms: int, hook: str = "", quote: str = "") -> str:
    """Create burn-in subtitle events from measured word timestamps."""
    if not 0 <= start_ms < end_ms:
        raise ValueError("Invalid clip interval")
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, Bold, BorderStyle, Outline, Alignment, MarginL, MarginR, MarginV",
        "Style: Word,DejaVu Sans,76,&H0000E9FF,&H00000000,-1,1,5,2,80,80,300",
        "Style: Quote,DejaVu Sans,80,&H00FFFFFF,&H00000000,-1,1,6,5,100,100,80",
        "Style: Hook,DejaVu Sans,100,&H00FFFFFF,&H00000000,-1,1,6,8,80,80,230",
        "[Events]", "Format: Layer, Start, End, Style, Text",
    ]
    if _ass_text(hook):
        lines.append(f"Dialogue: 1,0:00:00.00,{_ass_time((min(2000, end_ms - start_ms) + 9) // 10)},Hook,{_ass_text(hook)}")
    if _ass_text(quote):
        lines.append(f"Dialogue: 0,0:00:00.00,{_ass_time((end_ms - start_ms + 9) // 10)},Quote,{_ass_text(quote)}")
    previous_end = 0
    for word in sorted(words, key=lambda cue: (cue.start_ms, cue.end_ms)):
        if not 0 <= word.start_ms <= word.end_ms:
            raise ValueError("Invalid word interval")
        start = max(word.start_ms, start_ms)
        end = min(word.end_ms, end_ms)
        text = _ass_text(word.text)
        if start >= end or not text:
            continue
        begin_cs = max(previous_end, (start - start_ms + 5) // 10)
        end_cs = min((end_ms - start_ms + 9) // 10, (end - start_ms + 5) // 10)
        if begin_cs < end_cs:
            lines.append(f"Dialogue: 0,{_ass_time(begin_cs)},{_ass_time(end_cs)},Word,{text}")
            previous_end = end_cs
    return "\n".join(lines) + "\n"


def transcribe_words(clean_speech, model):
    """Use an already loaded Whisper model on clean, local speech."""
    result = model.transcribe(str(clean_speech), word_timestamps=True)
    return [
        Cue(round(word["start"] * 1000), round(word["end"] * 1000), word["word"].strip())
        for segment in result["segments"] for word in segment["words"]
    ]


def transcribe_local_words(clean_speech, model_directory):
    """Transcribe with a preinstalled local Whisper model on CPU."""
    from pathlib import Path
    import subprocess

    if not Path(clean_speech).is_file():
        raise ValueError("Clean speech file is missing")
    if not Path(model_directory).is_dir():
        raise ValueError("Provide a local faster-whisper model directory")

    from faster_whisper import WhisperModel
    import numpy as np

    model = WhisperModel(str(model_directory), device="cpu", compute_type="int8",
                         local_files_only=True)
    pcm = subprocess.check_output(
        ["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file",
         "-i", str(Path(clean_speech).resolve()), "-map", "0:a:0",
         "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"],
        timeout=120,
    )
    segments, _ = model.transcribe(np.frombuffer(pcm, dtype="<f4"),
                                    language="en", word_timestamps=True)
    return [
        Cue(round(word.start * 1000), round(word.end * 1000), word.word.strip())
        for segment in segments for word in segment.words
    ]
