"""Render a selected video and song window with format-specific audio and text."""

from collections.abc import Sequence
from pathlib import Path
import json
import math
import re
import subprocess
from tempfile import TemporaryDirectory

from .captions import Cue, highlighted_ass
from .framing import VERTICAL_FRAME

_SENTENCE_END = re.compile(r"""[.!?]["'’”)]*$""")


def _validate_speech(words: Sequence[Cue], duration_ms: int) -> None:
    spoken = [word for word in words if word.start_ms < duration_ms and word.text.strip()]
    if not spoken:
        raise ValueError("Supply word timestamps measured on the clean speech")
    final = max(spoken, key=lambda word: word.end_ms)
    if final.end_ms > duration_ms or not _SENTENCE_END.search(final.text.strip()):
        raise ValueError("Speech must end on a complete sentence")


def render_reel(
    video: Path,
    song: Path,
    destination: Path,
    *,
    format_id: str,
    hook: str,
    quote: str = "",
    words: Sequence[Cue] = (),
    voiceover: Path | None = None,
    video_start: float = 0,
    song_start: float = 0,
    duration: float = 22,
) -> None:
    """Burn in clip-relative captions and mix local files into a vertical MP4."""
    if format_id not in {"F1", "F2", "F3"}:
        raise ValueError("Use format F1, F2 or F3")
    if not all(math.isfinite(value) and value >= 0 for value in (video_start, song_start, duration)):
        raise ValueError("Invalid render interval")
    if not 20 <= duration <= 25:
        raise ValueError("Standard reels must last 20–25 seconds")
    if not hook.strip():
        raise ValueError("Hook text is required")
    if format_id == "F2" and not quote.strip():
        raise ValueError("F2 requires a quote")
    if format_id == "F3" and voiceover is None:
        raise ValueError("F3 requires clean voiceover audio")
    end_ms = round(duration * 1000)
    if format_id != "F2":
        _validate_speech(words, end_ms)
    destination = Path(destination).resolve()
    inputs = [Path(video).resolve(), Path(song).resolve()]
    if format_id == "F3":
        inputs.append(Path(voiceover).resolve())
    if destination in inputs:
        raise ValueError("Output must differ from the inputs")
    speech = {"F1": "0:a:0", "F3": "2:a:0"}.get(format_id)
    filters = [f"[0:v:0]{VERTICAL_FRAME},ass=captions.ass[outv]"]
    if speech is None:
        filters.append("[1:a:0]volume=0.75[outa]")
    else:
        filters.extend([
            f"[{speech}]aresample=48000,apad,asplit=2[dialogue][sidechain]",
            "[1:a:0]aresample=48000,volume=0.38[music]",
            "[music][sidechain]sidechaincompress=threshold=0.025:ratio=10:attack=12:release=350[ducked]",
            "[dialogue][ducked]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[outa]",
        ])
    with TemporaryDirectory(dir=destination.parent) as directory:
        staging = Path(directory)
        ass = highlighted_ass(
            words if speech else (), start_ms=0, end_ms=end_ms, hook=hook,
            quote=quote if speech is None else "",
        )
        (staging / "captions.ass").write_text(ass, encoding="utf-8")
        staged = staging / "reel.mp4"
        command = ["ffmpeg", "-nostdin", "-v", "error"]
        for path, start in zip(inputs, (video_start, song_start, 0)):
            command.extend(["-protocol_whitelist", "file", "-ss", str(start), "-i", str(path)])
        command.extend([
            "-filter_complex_threads", "1", "-filter_complex", ";".join(filters),
            "-map", "[outv]", "-map", "[outa]", "-t", str(duration),
            "-c:v", "libx264", "-preset", "veryfast", "-threads", "2", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-movflags", "+faststart", staged.name,
        ])
        subprocess.run(command, cwd=staging, check=True, timeout=180)
        probe = json.loads(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration",
             "-of", "json", str(staged)], timeout=20,
        ))
        stream_durations = {stream["codec_type"]: float(stream["duration"])
                            for stream in probe["streams"]}
        if any(stream_durations.get(kind, 0) < duration - 0.12 for kind in ("video", "audio")):
            raise ValueError("Selected inputs do not cover the requested reel duration")
        staged.replace(destination)


def speech_duration(words: Sequence[Cue]) -> float:
    """Pick the first whole-sentence cut in the standard closing window."""
    for end_ms in range(22000, 25000, 100):
        try:
            _validate_speech(words, end_ms)
        except ValueError:
            continue
        return end_ms / 1000
    raise ValueError("No complete sentence ends in the standard reel window")


class LocalReelBuilder:
    """Prepare clean speech and render a local editorial preset."""

    def __init__(self, *, hook, quote="", voice_script="", whisper_model=None, piper_model=None):
        if not hook.strip():
            raise ValueError("Set a hook for the local format renderer")
        self.hook, self.quote, self.voice_script = hook, quote, voice_script
        self.whisper_model, self.piper_model = whisper_model, piper_model

    def __call__(self, video, song, destination, format_id):
        from .captions import transcribe_local_words
        from .tts import synthesize

        if format_id != "F2" and self.whisper_model is None:
            raise ValueError("Configure a local Whisper model for speech formats")
        with TemporaryDirectory(dir=destination.parent) as directory:
            speech = Path(directory) / "speech.wav"
            words, voiceover, duration = (), None, 22
            if format_id == "F1":
                subprocess.run(
                    ["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file",
                     "-i", str(video), "-t", "25", "-map", "0:a:0",
                     "-ac", "1", "-ar", "16000", str(speech)], check=True, timeout=45,
                )
                words = transcribe_local_words(speech, self.whisper_model)
                duration = speech_duration(words)
            if format_id == "F3":
                synthesize(self.voice_script, speech, piper_model=self.piper_model)
                voiceover = speech
                words = transcribe_local_words(speech, self.whisper_model)
            render_reel(video, song, destination, format_id=format_id, hook=self.hook,
                        quote=self.quote, words=words, voiceover=voiceover, duration=duration)
