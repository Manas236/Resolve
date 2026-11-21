#!/usr/bin/env python3
"""Run the first M0 direct-media smoke checks and produce a JSON report."""

import argparse
from array import array
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, build_opener

WATCH_SMOKE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
HEATMAP_START = re.compile(r'heatMarkerRenderer"\s*:\s*({[^{}]+})')
BLOCK_MESSAGES = (
    "sign in to confirm you’re not a bot",
    "sign in to confirm you're not a bot",
    "unusual traffic from your computer network",
    "verify you are human",
)
ROOT = Path(__file__).resolve().parents[1]
SAMPLES = (
    ("samplelib-audio", "audio", "https://samplelib.com/preview/mp3/sample-15s.mp3"),
    ("samplelib-video", "video", "https://samplelib.com/preview/mp4/sample-15s.mp4"),
    ("soundhelix-audio", "audio", "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"),
)
MAX_BYTES = 32 * 1024 * 1024
ALLOWED_FORMATS = {"mp3", "mov", "mp4", "webm", "matroska", "wav", "ogg", "flac"}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def validate_media(metadata, kind, reference_seconds=None):
    streams = metadata["streams"]
    format_info = metadata["format"]
    duration = float(format_info["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("invalid_duration")
    if not ALLOWED_FORMATS.intersection(format_info["format_name"].split(",")):
        raise ValueError("unsupported_container")
    if any(stream["codec_type"] not in {"audio", "video"} for stream in streams):
        raise ValueError("unexpected_stream")
    video = [
        stream for stream in streams
        if stream["codec_type"] == "video" and not stream.get("disposition", {}).get("attached_pic")
    ]
    audio = [stream for stream in streams if stream["codec_type"] == "audio"]
    if kind == "audio" and (video or not audio):
        raise ValueError("expected_audio")
    if kind == "video" and not video:
        raise ValueError("expected_video")
    if reference_seconds is not None and abs(duration - reference_seconds) > 3:
        raise ValueError("wrong_duration")
    result = {"duration_seconds": duration, "audio_streams": len(audio)}
    if video:
        dimensions = [(stream["width"], stream["height"]) for stream in video]
        width, height = max(dimensions, key=lambda pair: (min(pair), max(pair)))
        result.update(width=width, height=height, meets_720p=min(width, height) >= 720)
    return result


def strongest_window(samples, sample_rate, seconds=3):
    width = sample_rate * seconds
    windows = [
        sum(value * value for value in samples[start:start + width]) / width
        for start in range(0, len(samples) - width + 1, width)
    ]
    if not windows or max(windows) == 0:
        return None
    index = max(range(len(windows)), key=windows.__getitem__)
    return {"start_seconds": index * seconds, "window_seconds": seconds,
            "rms": round(math.sqrt(windows[index]) / 32768, 6)}


def probe_energy(path):
    rate = 4000
    process = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file",
            "-format_whitelist", ",".join(sorted(ALLOWED_FORMATS)), "-i", str(path),
            "-map", "0:a:0", "-t", "900", "-ac", "1", "-ar", str(rate),
            "-f", "s16le", "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    if process.returncode:
        return {"status": "decode_failed"}
    samples = array("h", process.stdout)
    if sys.byteorder != "little":
        samples.byteswap()
    peak = strongest_window(samples, rate)
    if peak is None:
        return {"status": "no_signal"}
    return {"status": "energy_peak", "scan_limit_seconds": 900, **peak}


def run_sample(identifier, kind, url, folder):
    result = {"id": identifier, "kind": kind, "url": url, "method": "http"}
    path = folder / "asset"
    try:
        opener = build_opener(NoRedirect())
        digest = hashlib.sha256()
        size = 0
        with opener.open(url, timeout=12) as response:
            result["http_status"] = response.status
            content_type = response.headers.get_content_type()
            result["content_type"] = content_type
            if not content_type.startswith(("audio/", "video/")) and content_type != "application/octet-stream":
                raise ValueError("unexpected_content_type")
            with path.open("xb") as destination:
                while block := response.read(64 * 1024):
                    size += len(block)
                    if size > MAX_BYTES:
                        raise ValueError("size_limit")
                    digest.update(block)
                    destination.write(block)
        process = subprocess.run(
            [
                "ffprobe", "-v", "error", "-protocol_whitelist", "file",
                "-format_whitelist", ",".join(sorted(ALLOWED_FORMATS)),
                "-show_streams", "-show_format", "-of", "json", str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=20,
            check=False,
        )
        if process.returncode:
            raise ValueError("ffprobe_rejected")
        result.update(validate_media(json.loads(process.stdout), kind))
        if result["audio_streams"]:
            try:
                result["energy"] = probe_energy(path)
            except (OSError, subprocess.TimeoutExpired) as error:
                result["energy"] = {"status": "unavailable", "error_type": type(error).__name__}
        result.update(status="verified", bytes=size, sha256=digest.hexdigest())
    except HTTPError as error:
        result.update(status="http_error", http_status=error.code)
        error.close()
    except URLError as error:
        result.update(status="network_error", error_type=type(error.reason).__name__)
    except (OSError, TimeoutError, subprocess.TimeoutExpired) as error:
        result.update(status="local_or_transport_error", error_type=type(error).__name__)
    except (KeyError, TypeError, ValueError) as error:
        result.update(status="rejected", reason=str(error) if type(error) is ValueError else type(error).__name__)
    finally:
        path.unlink(missing_ok=True)
    return result


def replay_result(html):
    normalized = html.casefold()
    if any(message in normalized for message in BLOCK_MESSAGES):
        return {"status": "challenge_detected"}
    markers = []
    for match in HEATMAP_START.finditer(html):
        marker = json.loads(match[1])
        start = marker.get("timeRangeStartMillis")
        score = marker.get("heatMarkerIntensityScoreNormalized")
        if type(start) in {str, int} and str(start).isdigit() and type(score) in {int, float}:
            if 0 <= score <= 1:
                markers.append((score, int(start)))
    if not markers:
        return {"status": "no_heatmap_in_html"}
    score, start = max(markers, key=lambda pair: (pair[0], -pair[1]))
    return {"status": "peak", "start_seconds": start / 1000,
            "score": score, "marker_count": len(markers)}


def probe_replay():
    result = {"url": WATCH_SMOKE_URL, "method": "watch_html"}
    try:
        with build_opener(NoRedirect()).open(WATCH_SMOKE_URL, timeout=12) as response:
            result["http_status"] = response.status
            html = response.read(8 * 1024 * 1024 + 1)
            if len(html) > 8 * 1024 * 1024:
                return {**result, "status": "size_limit"}
            return {**result, **replay_result(html.decode("utf-8", errors="replace"))}
    except HTTPError as error:
        result.update(status="http_error", http_status=error.code)
        error.close()
    except (URLError, OSError, ValueError) as error:
        result.update(status="unavailable", error_type=type(error).__name__)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "research/m0_server_results.json")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="resolve-m0-") as temporary:
        results = [run_sample(*sample, Path(temporary)) for sample in SAMPLES]
    report = {
        "schema_version": 1,
        "scope": "stage_a_smoke_only",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "replay_smoke": probe_replay(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent, delete=False) as output:
        path = Path(output.name)
        try:
            json.dump(report, output, indent=2)
            output.write("\n")
            output.close()
            path.replace(args.output)
        finally:
            path.unlink(missing_ok=True)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
