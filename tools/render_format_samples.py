"""Render F1–F4 from local media into the dashboard Review queue."""
import argparse
import json
from pathlib import Path

from resolve_pipeline.config import read_env_file
from resolve_pipeline.control import Pipeline
from resolve_pipeline.reels import configured_builder
from resolve_pipeline.scheduler import FORMATS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("state", "video", "song", "speech"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--whisper-model", type=Path, required=True)
    parser.add_argument("--voice-script", required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    builder = configured_builder(read_env_file(args.env_file), whisper_model=args.whisper_model,
                                 voice_script=args.voice_script)
    pipeline = Pipeline(args.state, [args.video], reel_builder=builder)
    pipeline.add_song(args.song)
    pipeline.add_speech(args.speech)
    samples = {}
    for format_id in FORMATS:
        reel_id, = pipeline.make_now(1, format_id)
        _, state, error = next(row for row in pipeline.list_reels() if row[0] == reel_id)
        samples[format_id] = {"id": reel_id, "state": state, "error": error}
        print(json.dumps({format_id: samples[format_id]}), flush=True)
    return 0 if all(row["state"] == "review" for row in samples.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
