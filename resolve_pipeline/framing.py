"""FFmpeg framing for a vertical reel."""

# Use display aspect ratio so anamorphic video keeps its proportions.
VERTICAL_FRAME = (
    "scale=w='max(1080,ceil(1920*dar/2)*2)':"
    "h='max(1920,ceil(1080/dar/2)*2)',setsar=1,crop=1080:1920"
)


def render_vertical(source, destination, *, start_seconds, duration_seconds):
    """Render a local clip with source audio, when present."""
    import math
    from pathlib import Path
    import subprocess
    from tempfile import TemporaryDirectory

    if not math.isfinite(start_seconds) or start_seconds < 0:
        raise ValueError("Invalid clip start")
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("Invalid clip duration")
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise ValueError("Output must differ from input")
    with TemporaryDirectory(dir=destination.parent) as tmp:
        staged = Path(tmp) / "reel.mp4"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file",
             "-ss", str(start_seconds), "-i", str(source), "-t", str(duration_seconds),
             "-map", "0:v:0", "-map", "0:a:0?", "-vf", VERTICAL_FRAME,
             "-filter_threads", "1", "-threads", "2", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(staged)],
            check=True, timeout=180,
        )
        staged.replace(destination)
