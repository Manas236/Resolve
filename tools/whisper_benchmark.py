"""Measure local CPU Whisper models against timestamped fixture references."""
import argparse
import json
from pathlib import Path
from time import perf_counter

from resolve_pipeline.reel_text import normalized
from resolve_pipeline.speech_edit import decode_speech


def word_error_rate(reference, actual):
    expected, result = normalized(reference).split(), normalized(actual).split()
    if not expected:
        raise ValueError("Reference transcript is empty.")
    row = list(range(len(result) + 1))
    for index, target in enumerate(expected, 1):
        next_row = [index]
        for position, actual_word in enumerate(result, 1):
            next_row.append(min(next_row[-1] + 1, row[position] + 1,
                                row[position - 1] + (target != actual_word)))
        row = next_row
    return row[-1] / len(expected)


def benchmark(models, clips, cpu_threads):
    from faster_whisper import WhisperModel

    results = {}
    for label, path in models.items():
        start = perf_counter()
        model = WhisperModel(str(path), device="cpu", compute_type="int8",
                             cpu_threads=cpu_threads, local_files_only=True)
        load = perf_counter() - start
        runs = []
        for clip, reference in clips:
            pcm = decode_speech(clip)
            start = perf_counter()
            segments, _ = model.transcribe(pcm, language="en", word_timestamps=True, vad_filter=True)
            actual = " ".join(segment.text for segment in segments)
            elapsed = perf_counter() - start
            runs.append({"clip": clip.name, "seconds": round(elapsed, 3),
                         "wer": round(word_error_rate(reference.read_text(encoding="utf-8"), actual), 4)})
        results[label] = {"load_seconds": round(load, 3), "runs": runs}
        del model
    qualified = [label for label, row in results.items()
                 if all(run["seconds"] + row["load_seconds"] < 60 for run in row["runs"])]
    winner = min(qualified, key=lambda label: (sum(run["wer"] for run in results[label]["runs"]),
                                              sum(run["seconds"] for run in results[label]["runs"])),
                 default=None)
    return {"cpu_threads": cpu_threads, "models": results, "winner": winner}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", nargs=2, required=True, metavar=("NAME", "DIRECTORY"))
    parser.add_argument("--clip", action="append", nargs=2, required=True, metavar=("AUDIO", "REFERENCE_TXT"))
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    print(json.dumps(benchmark(dict(args.model), [(Path(a), Path(b)) for a, b in args.clip], args.threads), indent=2))
