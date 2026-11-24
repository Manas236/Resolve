"""Windows acquisition probe and manual/scheduled library refresh."""
import sys

if sys.version_info < (3, 11):
    sys.exit("Resolve PC acquisition requires Python 3.11 or newer.")

import argparse
from collections import Counter
from importlib.util import find_spec
from array import array
from datetime import datetime, timezone
import json
import os
import math
import re
from shutil import which
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from urllib.request import build_opener

from resolve_pipeline.bundles import fetch_inventory, sync_bundle, write_bundle
from resolve_pipeline.config import read_env_file
from resolve_pipeline.downloader import CONTAINERS, DownloadRequest, validate_media
from tools import pc_discovery, pc_vision
from tools.m0_probe import (blocked_text, chosen_timestamp, convert, mp7_convert, probe_energy,
                            probe_replay, read_corpus, write_report, preview_result, NoRedirect, run_sample)


def acquire(url, kind, directory):
    fmt = "bestaudio/best" if kind != "clip" else "bestvideo[height>=720]+bestaudio/best[height>=720]"
    command = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist", "--no-progress",
               "--max-filesize", "500M", "--socket-timeout", "15", "--retries", "1",
               "--format", fmt, "--output", str(directory / "asset.%(ext)s")]
    command += ["--extract-audio", "--audio-format", "mp3"] if kind != "clip" else ["--merge-output-format", "mp4"]
    result = subprocess.run([*command, "--", url], text=True, encoding="utf-8", errors="replace",
                            capture_output=True, timeout=600)
    if result.returncode:
        if blocked_text(result.stderr):
            raise ValueError("provider_challenge")
        raise ValueError("download_failed")
    files = [path for path in directory.glob("asset.*") if path.suffix.lower() in
             {".mp4", ".webm", ".mkv", ".mp3"}]
    if len(files) != 1:
        raise ValueError("missing_or_ambiguous_media")
    if files[0].suffix == ".mkv":
        raise ValueError("unsupported_container")
    return files[0]


def fallback(item, directory):
    kind = "video" if item["kind"] == "clip" else "audio"
    target = {"id": "pc", "kind": kind, "reference_seconds": item.get("duration")}
    url = item["source"]
    routes = [("mp7", mp7_convert(target, url, directory, keep=True))]
    if routes[0][1]["status"] != "verified":
        if os.environ.get("RESOLVE_PC_BROWSER_FALLBACK") == "1":
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
                try:
                    for provider in ("cnvmp3", "ythd"):
                        result = convert(browser, provider, target, url, directory, keep=True)
                        routes.append((provider, result))
                        if result["status"] == "verified":
                            break
                finally:
                    browser.close()
    for provider, result in routes:
        if result["status"] == "verified":
            output = directory / ("fallback.mp4" if kind == "video" else "fallback.mp3")
            (directory / "asset").replace(output)
            return output, provider
    raise ValueError("converter_fallback_failed:" + ",".join(
        f"{provider}={result['status']}" for provider, result in routes))



def verify_asset(path, item):
    kind = item["kind"]
    seconds = item.get("duration")
    if kind == "speech" and seconds is None:
        seconds = float(subprocess.check_output(
            ["ffprobe", "-v", "error", "-protocol_whitelist", "file",
             "-format_whitelist", ",".join(sorted(CONTAINERS)), "-show_entries",
             "format=duration", "-of", "csv=p=0", str(path)], timeout=20))
    return validate_media(path, DownloadRequest(item["source"], "video" if kind == "clip" else "audio",
                                                seconds))

def checked_asset(item, directory):
    url, kind = item["source"], item["kind"]
    if not url.startswith(("https://www.youtube.com/watch?v=", "https://youtu.be/")):
        raise ValueError("Use a public YouTube watch URL for this batch")
    replay = probe_replay(url)
    if replay.get("status") == "challenge_detected" or replay.get("http_status") in {401, 403, 429}:
        raise ValueError("provider_challenge")
    try:
        media_path, route = acquire(url, kind, directory), "yt-dlp"
        verified = verify_asset(media_path, item)
    except (ValueError, subprocess.SubprocessError) as error:
        if str(error) == "provider_challenge":
            raise
        fallback_dir = directory / "fallback"
        fallback_dir.mkdir()
        media_path, route = fallback(item, fallback_dir)
        verified = verify_asset(media_path, item)
    energy = probe_energy(media_path)
    chosen = chosen_timestamp(replay, {}, {"status": "unavailable"}, {"status": "unavailable"},
                              {"duration_seconds": verified.duration_seconds, "energy": energy})
    if chosen is None and kind != "clip":
        raise ValueError("no_usable_timestamp")
    return media_path, {**item, "duration": verified.duration_seconds,
                        "timestamp": chosen["start_seconds"] if chosen else 0,
                        "timestamp_method": chosen["method"] if chosen else "clip_start",
                        "download_route": route,
                        "observed_at": datetime.now(timezone.utc).isoformat()}, replay



def decoded_envelope(path):
    pcm = subprocess.check_output(
        ["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file",
         "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "2000",
         "-t", "600", "-f", "s16le", "pipe:1"], timeout=30)
    samples = array("h", pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    return [math.log1p(sum(sample * sample for sample in samples[i:i + 200]) / 200)
            for i in range(0, len(samples) - 199, 200)]


def match_preview(full, preview):
    """Find an unambiguous offset at tenth-second resolution."""
    if len(preview) < 50 or len(preview) > len(full):
        return None
    mean = sum(preview) / len(preview)
    centered = [x - mean for x in preview]
    norm = math.sqrt(sum(x * x for x in centered))
    if norm < 0.01:
        return None
    scores = []
    for i in range(len(full) - len(preview) + 1):
        segment = full[i:i + len(preview)]
        average = sum(segment) / len(segment)
        variance = sum((x - average) ** 2 for x in segment)
        score = (sum(x * y for x, y in zip(centered, segment)) / (norm * math.sqrt(variance))
                 if variance else 0)
        scores.append((score, i))
    best, offset = max(scores)
    competing = max((score for score, i in scores if abs(i - offset) > 5), default=0)
    return offset / 10 if best >= 0.85 and best - competing >= 0.05 else None


def check_preview(provider, origin, item, audio_path, folder, replay):
    result = preview_result(provider, origin, item, include_url=True)
    url = result.pop("preview_url", None)
    if not url:
        return result
    parts = urlsplit(url)
    domains = ("itunes.apple.com", "mzstatic.com") if provider == "itunes" else ("dzcdn.net",)
    if (parts.scheme != "https" or parts.username or parts.password or not parts.hostname
            or not any(parts.hostname == d or parts.hostname.endswith("." + d) for d in domains)):
        return {**result, "status": "unsupported_preview_origin"}
    folder.mkdir()
    downloaded = run_sample(item["id"], "audio", url, folder, keep=True)
    if downloaded["status"] != "verified":
        return {**result, "status": "preview_download_failed", "transfer": downloaded["status"]}
    preview = decoded_envelope(folder / "asset")
    offset = match_preview(decoded_envelope(audio_path), preview)
    overlap = ("unknown" if offset is None or replay.get("status") != "peak"
               else offset <= replay["start_seconds"] < offset + len(preview) / 10)
    return {**result, "status": "preview_verified", "preview_start_seconds": offset,
            "peak_overlap": overlap, "preview_seconds": len(preview) / 10}


def catalog_previews(item, audio_path, directory, replay):
    previews = {}
    for provider, origin in (("itunes", "https://itunes.apple.com"),
                             ("deezer", "https://api.deezer.com")):
        try:
            previews[provider] = check_preview(provider, origin, item, audio_path,
                                               directory / provider, replay)
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
            previews[provider] = {"status": "unavailable", "error": type(error).__name__}
    return previews

def probe(output):
    rows = []
    blocked = False
    corpus = read_corpus()
    targets = Counter(item["kind"] for item in corpus)

    def checkpoint(complete):
        verified = Counter(row["kind"] for row in rows if row["status"] == "verified")
        summary = {kind: {"verified": verified[kind], "target": count}
                   for kind, count in targets.items()}
        write_report(output, {"complete": complete, "summary": summary, "results": rows})

    checkpoint(False)
    for item in corpus:
        row = {"id": item["id"], "kind": item["kind"]}
        if blocked:
            rows.append({**row, "status": "skipped_after_provider_challenge"})
            continue
        with TemporaryDirectory() as staging:
            try:
                audio_path, metadata, replay = checked_asset(
                    {"source": item["watch_url"], "kind": "song" if item["kind"] == "audio" else "clip",
                     "duration": item.get("reference_seconds"), "title": item["title"],
                     "source_page": item["source_url"]}, Path(staging))
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                blocked = isinstance(error, ValueError) and str(error) == "provider_challenge"
                rows.append({**row, "status": str(error) if isinstance(error, ValueError)
                             else type(error).__name__})
            else:
                previews = (catalog_previews(item, audio_path, Path(staging), replay)
                            if item["kind"] == "audio" else {})
                rows.append({**row, "status": "verified", "metadata": metadata, "replay": replay,
                             "previews": previews})
        checkpoint(False)
    checkpoint(True)


def source_html(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Discovery sources must be public HTTPS pages")
    with build_opener(NoRedirect()).open(url, timeout=15) as response:
        payload = response.read(4 * 1024 * 1024 + 1)
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError("Discovery source is too large")
    html = payload.decode("utf-8", errors="replace")
    if blocked_text(html):
        raise ValueError("provider_challenge")
    return html


def refresh(catalog, output, env_file, send, limits, threshold, sources):
    settings = read_env_file(env_file) | dict(os.environ)
    pins = []
    if catalog:
        config = json.loads(catalog.read_text(encoding="utf-8"))
        pins = config.get("entries", []) if isinstance(config, dict) else config
        if not isinstance(pins, list) or not all(isinstance(item, dict)
                and item.get("kind") in {"song", "clip", "speech"} and item.get("source") for item in pins):
            raise ValueError("Optional catalog must list pinned media records")
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path = output.with_suffix(".discovery.json")
    with TemporaryDirectory() as staging:
        root = Path(staging)
        existing = fetch_inventory(settings, root / "inventory.json") if send else set()
        entries = pc_discovery.discover(json.loads(sources.read_text(encoding="utf-8")),
                                        pins, existing, source_html)
        report = []
        vision = None
        counts, attempts = Counter(), Counter()
        batch = []
        for index, item in enumerate(entries):
            kind = item["kind"]
            if counts[kind] >= limits[kind] or attempts[kind] >= limits[kind] * 5:
                continue
            attempts[kind] += 1
            folder = root / str(index)
            folder.mkdir()
            try:
                path, metadata, replay = checked_asset(item, folder)
                if kind == "clip":
                    if vision is None:
                        vision = pc_vision.TrainingVision(threshold)
                    score = vision.screen(path, metadata["duration"], metadata["timestamp"], folder)
                    report.append({"source": item["source"], "vision": score})
                    if not score["accepted"]:
                        continue
                    metadata.update(content="physical_training", vision=score)
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                report.append({"source": item["source"], "error": str(error)})
                if str(error) == "provider_challenge":
                    break
                continue
            if kind == "song" and item.get("title") and item.get("duration"):
                metadata["previews"] = catalog_previews(
                    {**item, "id": str(index), "reference_seconds": item["duration"]},
                    path, folder, replay)
            batch.append((path, metadata))
            counts[kind] += 1
        write_report(report_path, {"accepted": dict(counts), "candidates": report})
        if not batch:
            print(f"No new eligible media. See {report_path}.")
            return
        write_bundle(output, batch)
    if send:
        sync_bundle(output, settings)



def check_deno():
    try:
        process = subprocess.run(["deno", "--version"], capture_output=True, text=True,
                                 timeout=10, check=True)
        version = re.search(r"^deno (\d+)\.(\d+)\.", process.stdout)
        if version and tuple(map(int, version.groups())) >= (2, 3):
            return
    except (OSError, subprocess.SubprocessError):
        pass
    raise ValueError("Install Deno 2.3+ and make deno.exe available on the Windows account's PATH.")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("probe", "refresh"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, help="Optional pins")
    parser.add_argument("--sources", type=Path, default=pc_discovery.ROOT / "tools/pc_sources.json")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Acquisition runs on the designated Windows PC.")
    if find_spec("yt_dlp") is None:
        parser.error("Install yt-dlp in the Windows acquisition virtual environment.")
    for binary in ("ffmpeg", "ffprobe"):
        if not which(binary):
            parser.error(f"Install {binary} on the Windows acquisition PC and add it to PATH.")
    try:
        check_deno()
    except ValueError as error:
        parser.error(str(error))
    os.environ.setdefault("RESOLVE_PC_BROWSER_FALLBACK",
                          read_env_file(args.env_file).get("RESOLVE_PC_BROWSER_FALLBACK", "0"))
    if args.command == "probe":
        probe(args.output)
    else:
        settings = read_env_file(args.env_file) | dict(os.environ)
        try:
            limits = {kind: int(settings.get(f"RESOLVE_PC_MAX_{kind.upper()}S", default))
                      for kind, default in (("song", 10), ("clip", 10), ("speech", 3))}
            threshold = float(settings.get("RESOLVE_PC_VISION_THRESHOLD", "0.65"))
            if not all(0 <= value <= 50 for value in limits.values()) or not 0 < threshold <= 1:
                raise ValueError
        except ValueError:
            parser.error("Acquisition limits must be 0–50 and the vision threshold must be in (0, 1].")
        refresh(args.catalog, args.output, args.env_file, args.send, limits, threshold, args.sources)


if __name__ == "__main__":
    main()
