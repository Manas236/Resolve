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


def cut_verified_segment(
    source, destination, request, *, timestamp_seconds, duration_seconds=22, lead_in_seconds=1,
):
    """Validate the full source, then cut and verify a local audio or video segment."""
    from pathlib import Path
    import subprocess
    from tempfile import TemporaryDirectory

    from .downloader import DownloadRequest, MediaRejected, validate_media

    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise ValueError("Segment output must differ from the source")
    suffix = ".mp3" if request.kind == "audio" else ".mp4"
    if destination.suffix.lower() != suffix:
        raise ValueError(f"Use a {suffix} destination for {request.kind}")
    media = validate_media(source, request)
    window = clip_window(timestamp_seconds=timestamp_seconds,
                         media_seconds=media.duration_seconds, requested_seconds=duration_seconds,
                         lead_in_seconds=lead_in_seconds)
    codecs = (["-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", "192k"] if request.kind == "audio"
              else ["-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast",
                    "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart"])
    with TemporaryDirectory(dir=destination.parent) as directory:
        staged = Path(directory) / f"segment{suffix}"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file",
             "-ss", str(window.start_seconds), "-i", str(source), "-t", str(window.duration_seconds),
             *codecs, str(staged)], check=True, timeout=180,
        )
        segment = validate_media(staged, DownloadRequest(request.source, request.kind,
                                                         window.duration_seconds))
        if abs(segment.duration_seconds - window.duration_seconds) > 0.25:
            raise MediaRejected("wrong_segment_duration")
        staged.replace(destination)
    return window, segment
