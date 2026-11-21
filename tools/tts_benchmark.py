"""Benchmark three local Flite voices on the same script."""

import argparse
import json
from pathlib import Path
import subprocess
from time import perf_counter
import wave

SCRIPT = "Show up. Start with one rep. Finish what you started."
VOICES = ("slt", "kal", "rms")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for voice in VOICES:
        output = args.output / f"{voice}.wav"
        started = perf_counter()
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
             "-i", f"flite=voice={voice}:text='{SCRIPT}'", str(output)],
            check=True, timeout=30,
        )
        elapsed = perf_counter() - started
        with wave.open(str(output), "rb") as audio:
            results.append({"engine": "flite", "voice": voice, "seconds": elapsed,
                            "audio_seconds": audio.getnframes() / audio.getframerate()})
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
