#!/usr/bin/env python3
"""Measure Resolve M0 download and timestamp candidates without retaining media."""

import argparse
from array import array
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

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
MAX_BYTES = 256 * 1024 * 1024
ALLOWED_FORMATS = {"mp3", "mov", "mp4", "webm", "matroska", "wav", "ogg", "flac"}


class MediaRejected(ValueError):
    """A fixed, report-safe reason for rejecting a media response."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def validate_media(metadata, kind, reference_seconds=None):
    streams = metadata["streams"]
    format_info = metadata["format"]
    duration = float(format_info["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise MediaRejected("invalid_duration")
    if not ALLOWED_FORMATS.intersection(format_info["format_name"].split(",")):
        raise MediaRejected("unsupported_container")
    if any(stream["codec_type"] not in {"audio", "video"} for stream in streams):
        raise MediaRejected("unexpected_stream")
    video = [
        stream for stream in streams
        if stream["codec_type"] == "video" and not stream.get("disposition", {}).get("attached_pic")
    ]
    audio = [stream for stream in streams if stream["codec_type"] == "audio"]
    if kind == "audio" and (video or not audio):
        raise MediaRejected("expected_audio")
    if kind == "video" and not video:
        raise MediaRejected("expected_video")
    if reference_seconds is not None and abs(duration - reference_seconds) > 3:
        raise MediaRejected("wrong_duration")
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
    peaks = [
        offset * seconds for offset, value in enumerate(windows)
        if value >= windows[index] * 0.64
        and (offset == 0 or value >= windows[offset - 1])
        and (offset + 1 == len(windows) or value >= windows[offset + 1])
    ]
    return {"start_seconds": index * seconds, "window_seconds": seconds,
            "rms": round(math.sqrt(windows[index]) / 32768, 6),
            "strong_local_peaks_seconds": peaks}


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


def run_sample(identifier, kind, url, folder, reference_seconds=None):
    result = {"id": identifier, "kind": kind, "method": "http"}
    path = folder / "asset"
    try:
        opener = build_opener(NoRedirect())
        digest = hashlib.sha256()
        size = 0
        with opener.open(url, timeout=12) as response:
            result["http_status"] = response.status
            content_type = response.headers.get_content_type()
            result["content_type"] = content_type
            length = response.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > MAX_BYTES:
                raise MediaRejected("size_limit")
            if not content_type.startswith(("audio/", "video/")) and content_type != "application/octet-stream":
                raise MediaRejected("unexpected_content_type")
            deadline = time.monotonic() + 90
            with path.open("xb") as destination:
                while block := response.read1(64 * 1024):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("transfer_deadline")
                    size += len(block)
                    if size > MAX_BYTES:
                        raise MediaRejected("size_limit")
                    digest.update(block)
                    destination.write(block)
            if length.isdigit() and size != int(length):
                raise MediaRejected("transfer_length_mismatch")
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
            raise MediaRejected("ffprobe_rejected")
        result.update(validate_media(json.loads(process.stdout), kind, reference_seconds))
        if kind == "video" and not result["meets_720p"]:
            raise MediaRejected("below_720p")
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
    except (OSError, HTTPException, subprocess.TimeoutExpired) as error:
        result.update(status="local_or_transport_error", error_type=type(error).__name__)
    except MediaRejected as error:
        result.update(status="rejected", reason=str(error))
    except (KeyError, TypeError, ValueError) as error:
        result.update(status="rejected", reason="invalid_media_metadata", error_type=type(error).__name__)
    finally:
        path.unlink(missing_ok=True)
    return result


def replay_result(html):
    normalized = html.casefold()
    if any(message in normalized for message in BLOCK_MESSAGES):
        return {"status": "challenge_detected"}
    markers = []
    for match in HEATMAP_START.finditer(html):
        try:
            marker = json.loads(match[1])
        except json.JSONDecodeError:
            continue
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


def probe_replay(url=WATCH_SMOKE_URL):
    result = {"url": url, "method": "watch_html"}
    try:
        with build_opener(NoRedirect()).open(url, timeout=12) as response:
            result["http_status"] = response.status
            html = response.read(8 * 1024 * 1024 + 1)
            if len(html) > 8 * 1024 * 1024:
                return {**result, "status": "size_limit"}
            return {**result, **replay_result(html.decode("utf-8", errors="replace"))}
    except HTTPError as error:
        result.update(status="http_error", http_status=error.code)
        error.close()
    except (OSError, HTTPException, ValueError) as error:
        result.update(status="unavailable", error_type=type(error).__name__)
    return result


def public_url(url, host):
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and parsed.netloc == host and
            not parsed.username and not parsed.password)


def read_json(request):
    with build_opener(NoRedirect()).open(request, timeout=15) as response:
        data = response.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("response_size_limit")
    return json.loads(data)


def direct_control(item, folder):
    if item["kind"] == "audio":
        return run_sample(item["id"], "audio", item["direct_url"], folder, item["reference_seconds"])
    host = "video.blender.org"
    try:
        metadata = read_json(f"https://{host}/api/v1/videos/{item['peertube_id']}")
        files = metadata["files"] + [
            entry for playlist in metadata["streamingPlaylists"] for entry in playlist["files"]
        ]
        eligible = [
            entry for entry in files if min(entry["width"], entry["height"]) >= 720
            and entry.get("hasAudio") and entry.get("hasVideo")
            and public_url(entry["fileUrl"], host)
        ]
        if not eligible:
            return {"status": "no_eligible_direct_file"}
        selected = min(eligible, key=lambda entry: entry["size"])
        return run_sample(item["id"], "video", selected["fileUrl"], folder, metadata["duration"])
    except (OSError, HTTPException, ValueError, TypeError, KeyError) as error:
        return {"status": "control_metadata_unavailable", "error_type": type(error).__name__}


PROVIDERS = {
    "mp7": "https://mp7.dev/",
    "notube": "https://notube.net/en",
    "directyt": "https://directytdownloader.com/",
}


def provider_url(url, provider):
    try:
        host = urlsplit(url).netloc
    except ValueError:
        return False
    if provider == "notube" and (host == "cdn.notube.net" or re.fullmatch(r"s[0-9]{2}\.notube\.net", host)):
        return public_url(url, host)
    return public_url(url, urlsplit(PROVIDERS[provider]).netloc)


def blocked_text(value):
    value = value.casefold()
    return any(fragment in value for fragment in BLOCK_MESSAGES + (
        "just a moment", "checking your browser", "captcha verification",
    ))


def browser_blocked(page):
    return (blocked_text(page.locator("body").inner_text(timeout=3000)) or
            page.locator("iframe[src*='captcha'], iframe[src*='challenges.cloudflare.com']").count() > 0)


def wait_for_link(page, selector, provider):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if browser_blocked(page):
            return None, "challenge_detected"
        if provider == "mp7" and page.locator("#status-text").text_content(timeout=3000) == "failed":
            return None, "provider_rejected"
        if provider == "directyt" and page.locator("#dl-error").is_visible():
            return None, "provider_rejected"
        links = page.locator(selector).all()
        for link in links:
            if link.is_visible():
                href = link.get_attribute("href", timeout=3000)
                if href and not href.startswith("#"):
                    return urljoin(page.url, href), "link_found"
        page.wait_for_timeout(1000)
    return None, "no_download_link"


def convert(browser, provider, item, watch, folder):
    landing = PROVIDERS[provider]
    context = browser.new_context(accept_downloads=False, service_workers="block")
    context.route("**/*", lambda route: (
        route.continue_() if provider_url(route.request.url, provider) else route.abort()
    ))
    try:
        page = context.new_page()
        page.set_default_timeout(8000)
        response = page.goto(landing, wait_until="domcontentloaded", timeout=15000)
        if response is None:
            return {"status": "no_landing_response"}
        if not response.ok:
            return {"status": "browser_http_error", "http_status": response.status}
        if browser_blocked(page):
            return {"status": "challenge_detected"}
        if provider == "mp7":
            page.locator("#url").fill(watch)
            format_name = "mp3" if item["kind"] == "audio" else "mp4"
            page.locator(f"#convert-form [data-format={format_name}]").click()
            selector = "#download-link"
        elif provider == "notube":
            page.locator("#keyword").fill(watch)
            page.locator("#myDropdown").select_option("mp3" if item["kind"] == "audio" else "mp4hd")
            page.locator("#submit-button").click()
            selector = "a[download], a[href*='/download/']:text-is('Download'), a:text-is('Download')"
        else:
            if page.locator("#consent-decline").is_visible():
                page.locator("#consent-decline").click()
            page.locator("#dl-url").fill(watch)
            page.locator("#dl-form button[type=submit]").click()
            if item["kind"] == "audio":
                page.locator("#tab-mp3").click()
                selector = "#panel-mp3 a[href]:not([href='#'])"
            else:
                selector = "#dl-video-list li:has-text('720p') a[href], #dl-video-list li:has-text('1080p') a[href]"
        url, status = wait_for_link(page, selector, provider)
        if url is None:
            return {"status": status}
        if not provider_url(url, provider):
            return {"status": "unsupported_download_origin"}
        return run_sample(item["id"], item["kind"], url, folder, item.get("reference_seconds"))
    finally:
        context.close()


def landing_http(url):
    try:
        with build_opener(NoRedirect()).open(url, timeout=12) as reply:
            content = reply.read(256 * 1024).decode("utf-8", errors="replace")
            return {"http_status": reply.status,
                    "status": "challenge_detected" if blocked_text(content) else "landing_reached"}
    except HTTPError as error:
        error.close()
        return {"http_status": error.code, "status": "http_error"}
    except (OSError, HTTPException) as error:
        return {"status": "unavailable", "error_type": type(error).__name__}


def grounded_prompt(key, prompt):
    request = Request(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        data=json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
        }).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    try:
        data = read_json(request)
        for candidate in data.get("candidates", []):
            if candidate.get("groundingMetadata", {}).get("groundingChunks"):
                response = " ".join(part.get("text", "") for part in candidate.get("content", {}).get("parts", []))
                return {"status": "grounded", "response": response}
        return {"status": "no_grounding_metadata"}
    except HTTPError as error:
        error.close()
        return {"status": "http_error", "http_status": error.code}
    except (OSError, HTTPException, ValueError, TypeError) as error:
        return {"status": "unavailable", "error_type": type(error).__name__}


def gemini_grounding(key):
    if not key:
        return {"status": "skipped_no_billing_disabled_key"}
    result = grounded_prompt(key, "Search for the Blender Spring open movie and cite its official page.")
    return {"status": result["status"], **({"http_status": result["http_status"]} if "http_status" in result else {})}


def check_offset(offset, control):
    if type(offset) not in {int, float} or not math.isfinite(offset) or not 0 <= offset < control.get("duration_seconds", 0):
        return {"status": "invalid_or_unverified_offset"}
    peaks = control.get("energy", {}).get("strong_local_peaks_seconds", [])
    return {"status": "cross_checked" if any(abs(offset - peak) <= 3 for peak in peaks)
            else "rejected_no_nearby_audio_peak", "start_seconds": offset}


def gemini_timestamp(key, enabled, item, watch, control):
    if not enabled:
        return {"status": "skipped_grounding_unavailable"}
    result = grounded_prompt(key, (
        f"Search for a documented popular segment of {item['title']} in this exact upload: {watch}. "
        "If a search source documents the start offset in the original upload, reply with "
        "offset_seconds=<number>. Otherwise reply unknown. Do not estimate."
    ))
    if result["status"] != "grounded":
        return result
    match = re.search(r"offset_seconds\s*=\s*(\d+(?:\.\d+)?)", result["response"], re.I)
    if not match:
        return {"status": "no_documented_offset"}
    return check_offset(float(match[1]), control)


SOCIAL_HOSTS = {"www.instagram.com", "www.tiktok.com"}


def social_offset(links, youtube_id):
    for url in links:
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if not public_url(url, "www.youtube.com") or parsed.path != "/watch":
            continue
        parameters = parse_qs(parsed.query)
        offset = parameters.get("t", [""])[0].removesuffix("s")
        if parameters.get("v") == [youtube_id] and re.fullmatch(r"\d{1,5}", offset):
            return int(offset)
    return None


def social_timestamp(browser, item, control):
    urls = item.get("social_urls", [])
    if not urls:
        return {"status": "no_validated_source_mapping"}
    if browser is None:
        return {"status": "skipped_browser_unavailable"}
    for url in urls:
        if not any(public_url(url, host) for host in SOCIAL_HOSTS):
            continue
        landing = landing_http(url)
        if landing["status"] == "challenge_detected" or landing.get("http_status") in {401, 403, 429}:
            continue
        context = browser.new_context(accept_downloads=False, service_workers="block")
        try:
            host = urlsplit(url).netloc
            context.route("**/*", lambda route: (
                route.continue_() if public_url(route.request.url, host)
                and route.request.resource_type != "media" else route.abort()
            ))
            page = context.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=15000)
            if response and response.ok and not browser_blocked(page):
                links = page.locator("a[href]").evaluate_all("(nodes) => nodes.map((node) => node.href)")
                offset = social_offset(links, item["youtube_id"])
                if offset is not None:
                    return check_offset(offset, control)
        except Exception as error:
            return {"status": "browser_error", "error_type": type(error).__name__}
        finally:
            context.close()
    return {"status": "no_public_link_to_exact_offset"}


def browser_replay(browser, watch, replay):
    if browser is None:
        return {"status": "skipped_browser_unavailable"}
    if replay["status"] == "challenge_detected" or replay.get("http_status") in {401, 403, 429}:
        return {"status": "skipped_watch_block"}
    if replay["status"] == "peak":
        return {"status": "skipped_http_already_worked"}
    context = browser.new_context(accept_downloads=False, service_workers="block")
    try:
        context.route("**/*", lambda route: (
            route.continue_() if public_url(route.request.url, "www.youtube.com")
            and route.request.resource_type != "media" else route.abort()
        ))
        page = context.new_page()
        response = page.goto(watch, wait_until="domcontentloaded", timeout=15000)
        if response is None or not response.ok:
            return {"status": "browser_http_error", "http_status": response.status if response else None}
        if browser_blocked(page):
            return {"status": "challenge_detected"}
        state = page.evaluate("() => JSON.stringify(window.ytInitialData || {})")
        return replay_result(state)
    except Exception as error:
        return {"status": "browser_error", "error_type": type(error).__name__}
    finally:
        context.close()


def chosen_timestamp(replay, browser_result, social, gemini, control):
    for candidate in (replay, browser_result):
        if candidate.get("status") == "peak" and 0 <= candidate["start_seconds"] < control.get("duration_seconds", 0):
            return {"method": "youtube_replay", "start_seconds": candidate["start_seconds"]}
    for method, candidate in (("social", social), ("gemini", gemini)):
        if candidate["status"] == "cross_checked":
            return {"method": method, "start_seconds": candidate["start_seconds"]}
    energy = control.get("energy", {})
    if energy.get("status") == "energy_peak":
        return {"method": "audio_energy", "start_seconds": energy["start_seconds"]}
    return None


def read_corpus():
    items = json.loads((ROOT / "research/m0_corpus.json").read_text(encoding="utf-8"))
    for kind in ("audio", "video"):
        if sum(item["kind"] == kind for item in items) != 10:
            raise ValueError("expected ten items of each kind")
    if len(items) != 20 or len({item["id"] for item in items}) != 20:
        raise ValueError("expected twenty distinct items")
    for item in items:
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", item["youtube_id"]):
            raise ValueError("invalid watch identifier")
        if item["kind"] == "audio" and not public_url(item["direct_url"], "incompetech.com"):
            raise ValueError("invalid audio reference origin")
        if item["kind"] == "video" and not re.fullmatch(r"[A-Za-z0-9]{22}", item["peertube_id"]):
            raise ValueError("invalid Blender video identifier")
    return items


def provider_summary(providers, rows):
    summaries = {}
    for provider in providers:
        summaries[provider] = {}
        for kind in ("audio", "video"):
            attempts = [row["converters"][provider] for row in rows
                        if row["kind"] == kind and provider in row["converters"]]
            verified = sum(attempt["status"] == "verified" for attempt in attempts)
            summaries[provider][kind] = {
                "target": 10,
                "observed": len(attempts),
                "verified": verified,
                "attempted": sum(not attempt["status"].startswith("skipped_") for attempt in attempts),
                "corpus_success_rate": verified / 10,
            }
    return summaries


def run_matrix(args):
    from contextlib import ExitStack

    corpus = read_corpus()
    if args.limit:
        corpus = [item for kind in ("audio", "video")
                  for item in [entry for entry in corpus if entry["kind"] == kind][:args.limit]]
    versions = tool_versions()
    landings = {name: landing_http(url) for name, url in PROVIDERS.items()}
    rows = []
    key = os.getenv("GEMINI_API_KEY") if args.gemini_free_project else None
    grounding = gemini_grounding(key)
    blocked = set()
    with ExitStack() as stack:
        folder = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="resolve-m0-")))
        browser = None
        browser_status = "disabled"
        if not args.no_browser:
            try:
                from playwright.sync_api import sync_playwright
                playwright = stack.enter_context(sync_playwright())
                browser = playwright.chromium.launch(
                    executable_path=args.chromium, headless=True, chromium_sandbox=True, timeout=15000,
                )
                stack.callback(browser.close)
                browser_status = "ready"
            except Exception as error:
                browser_status = type(error).__name__
        for item in corpus:
            watch = f"https://www.youtube.com/watch?v={item['youtube_id']}"
            print(f"Probing {item['kind']} {item['title']}", flush=True)
            control = direct_control(item, folder)
            replay = probe_replay(watch)
            headless_replay = browser_replay(browser, watch, replay)
            social = social_timestamp(browser, item, control)
            gemini = gemini_timestamp(key, grounding["status"] == "grounded", item, watch, control)
            attempts = {}
            target = {**item}
            if item["kind"] == "video":
                target["reference_seconds"] = control.get("duration_seconds")
            for name, landing in landings.items():
                if (name in blocked or landing["status"] == "challenge_detected" or
                        landing.get("http_status") in {401, 403, 429}):
                    attempts[name] = {"status": "skipped_provider_block"}
                elif browser is None:
                    attempts[name] = {"status": "skipped_browser_unavailable"}
                else:
                    try:
                        attempts[name] = convert(browser, name, target, watch, folder)
                    except Exception as error:
                        attempts[name] = {"status": "browser_error", "error_type": type(error).__name__}
                    if (attempts[name]["status"] == "challenge_detected" or
                            attempts[name].get("http_status") in {401, 403, 429}):
                        blocked.add(name)
            rows.append({
                "id": item["id"], "kind": item["kind"], "title": item["title"],
                "publisher_control": control, "converters": attempts,
                "timestamps": {
                    "youtube_replay": replay,
                    "youtube_replay_headless": headless_replay,
                    "social": social,
                    "gemini": gemini,
                    "audio_energy": control.get("energy", {"status": "no_reference_audio"}),
                    "selected": chosen_timestamp(replay, headless_replay, social, gemini, control),
                },
            })
            write_report(args.output, matrix_report(args, rows, landings, browser_status, grounding, False, versions))
    return matrix_report(args, rows, landings, browser_status, grounding, True, versions)


def tool_version(command):
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = result.stdout.splitlines()
    return lines[0] if not result.returncode and lines else None


def tool_versions():
    return {
        "python": sys.version.split()[0],
        "ffmpeg": tool_version(["ffmpeg", "-version"]),
        "ffprobe": tool_version(["ffprobe", "-version"]),
        "repo_revision": tool_version(["git", "rev-parse", "HEAD"]),
    }


def matrix_report(args, rows, landings, browser_status, grounding, complete, versions):
    return {
        "schema_version": 2,
        "scope": "m0_matrix_sample" if args.limit else "m0_matrix",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "complete": complete,
        "tools": versions,
        "browser_status": browser_status,
        "http_landing_checks": landings,
        "provider_summary": provider_summary(landings, rows),
        "gemini_grounding": grounding,
        "results": rows,
    }


def write_report(destination, report):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, delete=False) as output:
        path = Path(output.name)
        try:
            json.dump(report, output, indent=2, allow_nan=False)
            output.write("\n")
            output.close()
            path.replace(destination)
        finally:
            path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "research/m0_server_results.json")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--limit", type=int, choices=range(1, 11), help="diagnostic rows per media kind")
    parser.add_argument("--chromium", help="path to installed Chrome or Chromium")
    parser.add_argument("--no-browser", action="store_true", help="run HTTP diagnostics only")
    parser.add_argument("--gemini-free-project", action="store_true",
                        help="use exported GEMINI_API_KEY only if its project has billing disabled")
    args = parser.parse_args()
    if args.smoke:
        with tempfile.TemporaryDirectory(prefix="resolve-m0-") as temporary:
            results = [run_sample(*sample, Path(temporary)) for sample in SAMPLES]
        report = {
            "schema_version": 1,
            "scope": "stage_a_smoke_only",
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "results": results,
            "replay_smoke": probe_replay(),
        }
    else:
        report = run_matrix(args)
    write_report(args.output, report)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
