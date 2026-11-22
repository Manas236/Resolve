"""Generate comparable local speech samples with configured offline engines."""

import argparse
import json
from pathlib import Path
import subprocess
from time import perf_counter

from resolve_pipeline.tts import ENGINES, synthesize

SCRIPT = (
    "Show up. Start with one rep. Finish what you started. "
    "When you feel like quitting, slow down and take a breath. "
    "Then do the next thing you can do. You do not need a perfect day. "
    "You need one decision you can repeat tomorrow."
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--piper-model", type=Path)
    parser.add_argument("--kokoro-model", type=Path)
    parser.add_argument("--kokoro-voices", type=Path)
    parser.add_argument("--kokoro-voice", default="af_heart")
    parser.add_argument("--engine", action="append", choices=ENGINES,
                        help="Select engines individually. The default runs all three.")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for engine in dict.fromkeys(args.engine or ("flite", "espeak-ng", "piper")):
        output = args.output / f"{engine}.wav"
        started = perf_counter()
        try:
            properties = synthesize(SCRIPT, output, engine=engine, piper_model=args.piper_model,
                                    kokoro_model=args.kokoro_model, kokoro_voices=args.kokoro_voices,
                                    voice=args.kokoro_voice)
        except (ImportError, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            results.append({"engine": engine, "error": str(exc)})
        else:
            results.append({"engine": engine, "path": str(output.resolve()),
                            "elapsed_seconds": perf_counter() - started, **properties})
    print(json.dumps({"script": SCRIPT, "results": results}, indent=2))
    return int(any("error" in result for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
