"""Render a matched comparison for the dashboard's caption preset picker."""
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Lock

from .caption_style import PRESETS, PREVIEW_DIR
from .reels import render_reel

_lock = Lock()


def render_previews(output=PREVIEW_DIR):
    with _lock, TemporaryDirectory() as temporary:
        folder = Path(temporary)
        source, music = folder / "fixture.mp4", folder / "fixture.wav"
        # Alternating dark and bright panels make edge contrast easy to compare.
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=s=432x768:r=12:d=22", "-vf", "hue=s=0,eq=brightness=-0.12",
                        "-c:v", "libx264", "-threads", "2", str(source)], check=True, timeout=30)
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                        r"aevalsrc=0.65*sin(2*PI*65*t)*exp(-24*mod(t\,0.5)):s=22050:d=22",
                        str(music)], check=True, timeout=30)
        output.mkdir(parents=True, exist_ok=True)
        for preset in PRESETS:
            render_reel(source, music, output / f"{preset}.mp4", format_id="F2",
                        hook="STOP RUSHING THE SET",
                        text_lines=("RESET BEFORE YOU PULL", "KEEP THE BAR CLOSE",
                                    "MAKE THE LAST REP CLEAN"),
                        caption_preset=preset)


if __name__ == "__main__":
    render_previews()
