"""Render three local baselines per format using an open film and a song."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from resolve_pipeline.captions import transcribe_local_words
from resolve_pipeline.reels import render_reel
from resolve_pipeline.tts import synthesize

SCRIPTS = (
    "Show up. Start with one rep. Finish what you started. "
    "When you feel like quitting, slow down and take a breath. "
    "Then do the next thing you can do. You need one decision you can repeat tomorrow.",
    "Take the first step before you feel ready. Then put your feet back under you. "
    "Make one promise that you can keep today. Let tomorrow start with that promise.",
    "Measure today by what you did. The work you finish will matter more than the excuse "
    "you could have made. Take the next step and keep moving.",
)
HOOKS = ("Start with one rep", "Keep one promise", "Take the next step")


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    tracks = []
    for index, (hook, script) in enumerate(zip(HOOKS, SCRIPTS), 1):
        for format_id in ("F1", "F2", "F3"):
            path = args.output / f"{format_id}-{index}.mp4"
            start = args.speech_start[index - 1] if format_id == "F1" else (index - 1) * 5
            words = []
            voice = None
            if format_id == "F1":
                clean = args.output / f"speech-source-{index}.wav"
                subprocess.run(
                    ["ffmpeg", "-nostdin", "-y", "-v", "error", "-protocol_whitelist", "file",
                     "-ss", str(start), "-i", str(args.video), "-t", "25", "-map", "0:a:0",
                     "-ac", "1", "-ar", "16000", str(clean)], check=True, timeout=45,
                )
                words = transcribe_local_words(clean, args.whisper_model)
            if format_id == "F3":
                voice = args.output / f"voiceover-{index}.wav"
                synthesize(script, voice, piper_model=args.piper_model)
                words = transcribe_local_words(voice, args.whisper_model)
            duration = 22
            if format_id == "F1":
                duration = next(
                    (ms / 1000 for ms in range(22000, 25000, 100)
                     if (spoken := [word for word in words if word.start_ms < ms])
                     and spoken[-1].end_ms <= ms and re.search(r"[.!?][\"']?$", spoken[-1].text)),
                    None,
                )
                if duration is None:
                    raise ValueError(f"No measured sentence boundary near F1 start {start}s")
            render_reel(args.video, args.song, path, format_id=format_id,
                        hook=hook, quote=hook + ".", words=words, voiceover=voice,
                        video_start=start, song_start=(index - 1) * 3, duration=duration)
            selected = [word for word in words if word.start_ms < duration * 1000]
            with path.open("rb") as rendered:
                digest = hashlib.file_digest(rendered, "sha256").hexdigest()
            tracks.append({"path": str(path.resolve()), "duration": duration,
                           "video_start": start, "format": format_id,
                           "word_count": len(selected),
                           "last_word": selected[-1].text if selected else None,
                           "last_word_end_ms": selected[-1].end_ms if selected else None,
                           "sha256": digest})
    return tracks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for path in ("video", "song", "output", "whisper_model", "piper_model"):
        parser.add_argument(path, type=Path)
    parser.add_argument("--speech-start", type=float, nargs=3, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2))


if __name__ == "__main__":
    main()
