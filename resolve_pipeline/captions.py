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
