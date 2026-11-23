"""Render a selected video and song window with format-specific audio and text."""

from collections.abc import Sequence
from pathlib import Path
import json
import math
import re
import subprocess
from tempfile import TemporaryDirectory

from .captions import Cue, highlighted_ass
from .caption_style import FONTS, music_markers, styled_ass
from .framing import VERTICAL_FRAME
from .speech_edit import decode_speech, relative_words, select_passage

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
    text_lines: Sequence[str] = (),
    caption_preset: str | None = None,
) -> None:
    """Burn in clip-relative captions and mix local files into a vertical MP4."""
    if format_id not in {"F1", "F2", "F3"}:
        raise ValueError("Use format F1, F2 or F3")
    if not all(math.isfinite(value) and value >= 0 for value in (video_start, song_start, duration)):
        raise ValueError("Invalid render interval")
    if not 20 <= duration <= (35 if format_id == "F1" else 25):
        raise ValueError("F1 reels must last 20–35 seconds; other formats last 20–25 seconds")
    if not hook.strip():
        raise ValueError("Hook text is required")
    if format_id == "F2" and not (quote.strip() or text_lines):
        raise ValueError("F2 requires a quote")
    if format_id == "F3" and voiceover is None:
        raise ValueError("F3 requires clean voiceover audio")
    end_ms = round(duration * 1000)
    if format_id != "F2":
        _validate_speech(words, end_ms)
    destination = Path(destination).resolve()
    inputs = [Path(video).resolve(), Path(song).resolve()]
    if voiceover is not None:
        inputs.append(Path(voiceover).resolve())
    if destination in inputs:
        raise ValueError("Output must differ from the inputs")
    speech = {"F1": "2:a:0" if voiceover is not None else "0:a:0", "F3": "2:a:0"}.get(format_id)
    font_option = ":fontsdir=fonts" if caption_preset else ""
    filters = [f"[0:v:0]{VERTICAL_FRAME},fps=30,ass=captions.ass{font_option}[outv]"]
    if speech is None:
        filters.append("[1:a:0]aresample=48000,volume=0.75[outa]")
    else:
        filters.extend([
            f"[{speech}]aresample=48000,apad,asplit=2[dialogue][sidechain]",
            "[1:a:0]aresample=48000,volume=0.38[music]",
            "[music][sidechain]sidechaincompress=threshold=0.025:ratio=10:attack=12:release=350[ducked]",
            "[dialogue][ducked]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[outa]",
        ])
    with TemporaryDirectory(dir=destination.parent) as directory:
        staging = Path(directory)
        if caption_preset:
            from shutil import copytree

            copytree(FONTS, staging / "fonts")
            markers = music_markers(song, song_start, duration) if text_lines else ()
            ass = styled_ass(hook, text_lines, words if speech else (), caption_preset, end_ms, markers)
        else:
            ass = highlighted_ass(words if speech else (), start_ms=0, end_ms=end_ms, hook=hook,
                                  quote=quote if speech is None else "")
        (staging / "captions.ass").write_text(ass, encoding="utf-8")
        staged = staging / "reel.mp4"
        command = ["ffmpeg", "-nostdin", "-v", "error"]
        for path, start in zip(inputs, (video_start, song_start, 0)):
            command.extend(["-protocol_whitelist", "file", "-ss", str(start), "-i", str(path)])
        command.extend([
            "-filter_complex_threads", "1", "-filter_complex", ";".join(filters),
            "-map", "[outv]", "-map", "[outa]", "-t", str(duration),
            "-c:v", "libx264", "-preset", "veryfast", "-threads", "2", "-pix_fmt", "yuv420p",
            "-maxrate", "20M", "-bufsize", "20M", "-c:a", "aac", "-ar", "48000", "-b:a", "128k", "-movflags", "+faststart", staged.name,
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


class NoSpeech(ValueError):
    """The selected source cannot provide a speech segment."""


class LocalReelBuilder:
    """Prepare clean speech and render a local editorial preset."""

    def __init__(self, *, hook, quote="", voice_script="", whisper_model=None, piper_model=None, voice="piper",
                 kokoro_model=None, kokoro_voices=None, voice_id="af_heart",
                 generate_captions=False, gemini_key=""):
        if not (hook.strip() or generate_captions):
            raise ValueError("Set a hook for the local format renderer")
        self.hook, self.quote, self.voice_script = hook, quote, voice_script
        self.whisper_model, self.piper_model = whisper_model, piper_model
        self.generate_captions, self.gemini_key = generate_captions, gemini_key
        self.voice = voice
        self.kokoro_model, self.kokoro_voices, self.voice_id = kokoro_model, kokoro_voices, voice_id

    def format_requirements(self):
        from importlib.util import find_spec
        from shutil import which

        whisper = (self.whisper_model is not None
                   and (Path(self.whisper_model) / "model.bin").is_file()
                   and find_spec("faster_whisper") is not None)

        if self.voice == "kokoro":
            voice_ready = (find_spec("kokoro_onnx") is not None and bool(self.voice_id)
                           and all(path and Path(path).is_file()
                                   for path in (self.kokoro_model, self.kokoro_voices)))
        else:
            voice_ready = (self.voice == "piper" and which("piper") is not None and self.piper_model
                           and Path(self.piper_model).is_file()
                           and Path(str(self.piper_model) + ".json").is_file())
        return {
            "F1": None if whisper else "Configure a local Whisper model.",
            "F2": None if self.quote.strip() or self.generate_captions else "Configure quote text.",
            "F3": next((reason for ready, reason in (
                (whisper, "Configure a local Whisper model."),
                (bool(self.voice_script.strip()), "Configure a voice script."),
                (voice_ready, "Configure a local Kokoro or Piper engine and voice."),
            ) if not ready), None),
        }

    def __call__(self, video, song, destination, format_id, *, speech_source=None, starts=(0, 0, 0), video_text=None, caption_preset=None, select_music=None):
        from .captions import transcribe_local_words
        from .tts import synthesize

        if format_id != "F2" and self.whisper_model is None:
            raise ValueError("Configure a local Whisper model for speech formats")
        with TemporaryDirectory(dir=destination.parent) as directory:
            speech = Path(directory) / "speech.wav"
            words, voiceover, duration = (), None, 22
            if format_id == "F1":
                if speech_source is None:
                    raise NoSpeech("F1 skipped: add a separate speech source.")
                try:
                    samples = decode_speech(speech_source)
                except subprocess.SubprocessError as error:
                    raise NoSpeech("F1 skipped: the speech source has no audio track.") from error
                words = transcribe_local_words(speech_source, self.whisper_model, samples=samples)
                try:
                    passage, mood = select_passage(words, samples, self.gemini_key)
                except ValueError as error:
                    raise NoSpeech(str(error)) from error
                duration = (passage.clip_end_ms - passage.clip_start_ms) / 1000
                words = relative_words(passage)
                subprocess.run(
                    ["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file",
                     "-ss", str(passage.clip_start_ms / 1000), "-i", str(speech_source),
                     "-t", str(duration), "-map", "0:a:0", "-ac", "1", "-ar", "16000", str(speech)],
                    check=True, timeout=45,
                )
                voiceover = speech
                if select_music:
                    song, offset = select_music(mood | {"speech": str(speech_source),
                                                       "start": passage.clip_start_ms / 1000,
                                                       "end": passage.clip_end_ms / 1000})
                    starts = (starts[0], offset, 0)
            if format_id == "F3":
                synthesize(self.voice_script, speech, engine=self.voice, piper_model=self.piper_model,
                           kokoro_model=self.kokoro_model, kokoro_voices=self.kokoro_voices,
                           voice=self.voice_id)
                voiceover = speech
                words = transcribe_local_words(speech, self.whisper_model)
            render_reel(video, song, destination, format_id=format_id, hook=video_text["hook"] if video_text else self.hook,
                        quote=self.quote, words=words, voiceover=voiceover, duration=duration,
                        video_start=starts[0], song_start=starts[1],
                        text_lines=video_text["lines"] if video_text else (), caption_preset=caption_preset)


def configured_builder(settings, *, hook=None, quote="", voice_script="", whisper_model=None, piper_model=None):
    def model_path(argument, name):
        value = argument or settings.get(name)
        return Path(value).expanduser() if value else None

    hook = hook or ""
    return LocalReelBuilder(
        generate_captions=True, gemini_key=settings.get("GEMINI_API_KEY", ""),
        hook=hook, quote=quote,
        voice_script=voice_script or settings.get("RESOLVE_VOICE_SCRIPT", ""),
        whisper_model=model_path(whisper_model, "RESOLVE_WHISPER_MODEL"),
        piper_model=model_path(piper_model, "RESOLVE_PIPER_MODEL"),
        voice=settings.get("RESOLVE_TTS_ENGINE", "piper"),
        kokoro_model=model_path(None, "RESOLVE_KOKORO_MODEL"),
        kokoro_voices=model_path(None, "RESOLVE_KOKORO_VOICES"),
        voice_id=settings.get("RESOLVE_TTS_VOICE", "af_heart"),
    )
