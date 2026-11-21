"""Provider-independent clip windows around candidate timestamps."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ClipWindow:
    start_seconds: float
    duration_seconds: float


def clip_window(
    *,
    timestamp_seconds: float,
    media_seconds: float,
    requested_seconds: float,
    lead_in_seconds: float = 0,
) -> ClipWindow:
    """Keep the timestamp in the clip and shift the window at file boundaries."""
    values = (timestamp_seconds, media_seconds, requested_seconds, lead_in_seconds)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Clip times must be finite")
    if media_seconds <= 0 or requested_seconds <= 0 or lead_in_seconds < 0:
        raise ValueError("Durations must be positive and lead-in must be non-negative")
    if not 0 <= timestamp_seconds < media_seconds:
        raise ValueError("Timestamp must be inside the media")
    duration = min(media_seconds, requested_seconds)
    if lead_in_seconds >= duration:
        raise ValueError("Lead-in must be shorter than the clip")
    start = min(max(timestamp_seconds - lead_in_seconds, 0), media_seconds - duration)
    return ClipWindow(start, duration)
