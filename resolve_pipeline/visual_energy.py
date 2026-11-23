"""Low-cost visual statistics for PC discovery and older local library entries."""
from pathlib import Path
import subprocess


def measure(path, start=0, duration=25):
    import numpy as np

    raw = subprocess.check_output(["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file",
        "-ss", str(start), "-i", str(Path(path).resolve()), "-t", str(duration), "-an", "-vf",
        "fps=4,scale=48:32,format=gray", "-threads", "1", "-f", "rawvideo", "pipe:1"], timeout=40)
    pixels = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 32, 48).astype(np.float32)
    if not len(pixels):
        raise ValueError("No frames available for mood analysis.")
    brightness = float(pixels.mean()/255)
    motion = float(np.abs(np.diff(pixels, axis=0)).mean()/255) if len(pixels) > 1 else 0.
    return round(brightness, 3), round(motion, 3)


def fallback_tags(path, start=0):
    brightness, motion = measure(path, start)
    return {"label": ("low light" if brightness < .4 else "bright" if brightness > .65 else "moderate light")
            + (", high motion" if motion > .07 else ", restrained motion"),
            "brightness": brightness, "motion": motion,
            "intensity": round(.3 * brightness + .7 * min(1., motion * 6), 3)}
