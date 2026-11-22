"""Local speech synthesis for offline comparison and reel voiceover."""

from pathlib import Path
from shutil import which
import subprocess
from tempfile import TemporaryDirectory
import wave


ENGINES = ("flite", "espeak-ng", "piper")


def synthesize(script: str, output: Path, *, engine: str = "piper", piper_model: Path | None = None) -> dict:
    """Write a verified PCM WAV and return its measurable properties."""
    if not script.strip() or "\x00" in script:
        raise ValueError("Provide nonempty speech text without NUL characters")
    if engine not in ENGINES:
        raise ValueError(f"Unknown speech engine: {engine}")
    executable = which("ffmpeg" if engine == "flite" else engine)
    if executable is None:
        raise RuntimeError(f"Install {'FFmpeg with Flite support' if engine == 'flite' else engine}")
    if engine == "piper" and (
        piper_model is None or not piper_model.is_file()
        or not piper_model.with_suffix(piper_model.suffix + ".json").is_file()
    ):
        raise ValueError("Piper requires a local ONNX model and its adjacent .onnx.json file")

    output = Path(output)
    with TemporaryDirectory(dir=output.parent) as directory:
        staging = Path(directory)
        audio_path = staging / "speech.wav"
        if engine == "flite":
            (staging / "script.txt").write_text(script, encoding="utf-8")
            args = ["-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    "flite=voice=slt:textfile=script.txt", audio_path.name]
        elif engine == "espeak-ng":
            args = ["-v", "en-us", "-w", audio_path.name, "--stdin"]
        else:
            args = ["-m", str(piper_model.resolve()), "-f", audio_path.name]

        subprocess.run([executable, *args], cwd=staging, input=script, text=True,
                       encoding="utf-8", timeout=120, check=True)
        with wave.open(str(audio_path), "rb") as sample:
            frames = sample.getnframes()
            if frames == 0 or not any(sample.readframes(frames)):
                raise ValueError(f"{engine} produced empty or silent speech")
            properties = {"audio_seconds": frames / sample.getframerate(),
                          "sample_rate": sample.getframerate(),
                          "channels": sample.getnchannels()}
        audio_path.replace(output)
    return properties
