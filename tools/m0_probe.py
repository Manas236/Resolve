#!/usr/bin/env python3
"""Measure Resolve M0 download and timestamp candidates without retaining media."""

import sys

if sys.version_info < (3, 11):
    sys.exit("Resolve M0 requires Python 3.11 or newer (running %s)." % sys.version.split()[0])

import argparse
from array import array
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.parse import urlencode, parse_qs, urljoin, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

WATCH_SMOKE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
MACRO_MARKERS_START = re.compile(r'"macroMarkersListEntity"\s*:\s*(?={)')
HEATMAP_START = re.compile(r'"heatMarkerRenderer"\s*:\s*(?={)')
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
                for block in iter(lambda: response.read1(64 * 1024), b""):
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


def replay_result(html, *, video_id=None):
    if any(message in html.casefold() for message in BLOCK_MESSAGES):
        return {"status": "challenge_detected"}
    decoder = json.JSONDecoder()

    def objects(pattern):
        for match in pattern.finditer(html):
            try:
                result, _ = decoder.raw_decode(html, match.end())
            except (json.JSONDecodeError, RecursionError):
                continue
            if isinstance(result, dict):
                yield result

    def points(rows, start_field, score_field):
        for row in rows:
            if not isinstance(row, dict):
                continue
            start, score = row.get(start_field), row.get(score_field)
            if (type(start) in {str, int} and re.fullmatch(r"[0-9]{1,12}", str(start))
                    and type(score) in {int, float} and 0 <= score <= 1):
                yield score, int(start)

    markers = []
    for entity in objects(MACRO_MARKERS_START):
        marker_list = entity.get("markersList")
        if (not isinstance(marker_list, dict) or marker_list.get("markerType") != "MARKER_TYPE_HEATMAP"
                or (video_id is not None and entity.get("externalVideoId") != video_id)):
            continue
        rows = marker_list.get("markers")
        if isinstance(rows, list):
            markers.extend(points(rows, "startMillis", "intensityScoreNormalized"))
    schema = "macro_markers"
    if not markers:
        schema = "legacy_heat_markers"
        markers = list(points(objects(HEATMAP_START), "timeRangeStartMillis",
                              "heatMarkerIntensityScoreNormalized"))
    if not markers:
        return {"status": "no_heatmap_in_html"}
    score, start = max(markers, key=lambda pair: (pair[0], -pair[1]))
    return {"status": "peak", "start_seconds": start / 1000, "score": score,
            "marker_count": len(markers), "marker_schema": schema}

def probe_replay(url=WATCH_SMOKE_URL):
    result = {"url": url, "method": "watch_html"}
    arguments = parse_qs(urlsplit(url).query)
    video_id = arguments.get("v", [None])[0]
    request_url = url if "hl" in arguments else url + ("&" if arguments else "?") + "hl=en"
    try:
        with build_opener(NoRedirect()).open(request_url, timeout=12) as response:
            result["http_status"] = response.status
            html = response.read(8 * 1024 * 1024 + 1)
            if len(html) > 8 * 1024 * 1024:
                return {**result, "status": "size_limit"}
            return {**result, **replay_result(html.decode("utf-8", errors="replace"), video_id=video_id)}
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


class ProviderChallenge(ValueError):
    pass


def read_json(request):
    with build_opener(NoRedirect()).open(request, timeout=15) as response:
        if response.headers.get("cf-mitigated") == "challenge":
            raise ProviderChallenge
        data = response.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("response_size_limit")
    if data.lstrip().startswith(b"<") and blocked_text(data[:256000].decode(errors="replace")):
        raise ProviderChallenge
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
    "cnvmp3": "https://ww7.cnvmp3.org/",
    "eao": "https://tools.eao-lab.com/",
    "ythd": "https://www.ythd.site/en",
    "ytmp3space": "https://ytmp3.space/",
    "loader": "https://en.loader.to/4/",
    "audiocom": "https://audio.com/",
    "soundworldz": "https://soundworldz.com/",

}

DIRECT_MP3 = {"audiocom", "soundworldz"}


API_ROUTES = {
    "piped": ("https://pipedapi.kavin.rocks", "https://pipedapi.adminforge.de"),
    "invidious": ("https://invidious.nerdvpn.de", "https://yt.chocolatemoo53.com"),
    "cobalt": (),
    "soundcloud": ("https://api.soundcloud.com",),
    "deezer": ("https://api.deezer.com",),
    "itunes": ("https://itunes.apple.com",),
}
AUDIO_ONLY = DIRECT_MP3 | {"soundcloud", "deezer", "itunes"}


def api_origins(name):
    if name != "cobalt":
        return API_ROUTES[name]
    origin = os.getenv("RESOLVE_COBALT_ORIGIN", "").rstrip("/")
    # An operator may supply an instance whose owner permits API use.
    if re.fullmatch(r"https://[a-zA-Z0-9.-]+", origin) and origin != "https://api.cobalt.tools":
        return (origin,)
    return ()


def exact_track(item, title, artist, duration):
    def words(value):
        return re.findall(r"\w+", str(value).casefold())

    track, _, credits = item["title"].partition(" — ")
    return (words(title) == words(track) and bool(words(artist))
            and " ".join(words(artist)) in {" ".join(words(credit)) for credit in credits.split(",")}
            and type(duration) in (int, float) and math.isfinite(duration)
            and abs(duration - item["reference_seconds"]) <= 3)


def preview_result(name, origin, item):
    title, _, credits = item["title"].partition(" — ")
    query = f'{title} {credits.split(",")[0]}'
    if name == "itunes":
        data = read_json(origin + "/search?" + urlencode(
            {"entity": "song", "country": "US", "limit": 15, "term": query}))
        rows = [(row.get("trackName"), row.get("artistName"),
                 (row.get("trackTimeMillis") or 0) / 1000, row.get("previewUrl"))
                for row in data.get("results", [])]
    else:
        data = read_json(origin + "/search?" + urlencode(
            {"q": query, "limit": 15}))
        if data.get("error"):
            return {"status": "catalog_error", "catalog_code": data["error"].get("code")}
        rows = [(row.get("title"), row.get("artist", {}).get("name"),
                 row.get("duration"), row.get("preview")) for row in data.get("data", [])]
    matched = [row for row in rows if exact_track(item, *row[:3])]
    # Neither search result documents the clip's offset within the original.
    return {"status": "preview_metadata" if any(row[3] for row in matched) else "no_matching_preview",
            "catalog_results": len(rows), "exact_track_matches": len(matched),
            "preview_start_seconds": None, "peak_overlap": "unknown"}


def proxy_media(origin, streams):
    for stream in streams:
        for key in ("proxyUrl", "url"):
            value = stream.get(key)
            if isinstance(value, str):
                url = urljoin(origin + "/", value)
                host = urlsplit(origin).netloc
                if public_url(url, host) or (
                        host == "pipedapi.kavin.rocks" and public_url(url, "pipedproxy.kavin.rocks")):
                    yield url


def soundcloud_sample(origin, item, folder):
    token = os.getenv("RESOLVE_SOUNDCLOUD_TOKEN")
    if not token:
        return {"status": "skipped_no_soundcloud_token"}
    headers = {"Authorization": "OAuth " + token, "Accept": "application/json"}
    rows = read_json(Request(origin + "/tracks?" + urlencode(
        {"q": item["title"].replace(" — ", " "), "limit": 15}), headers=headers))
    if isinstance(rows, dict):
        rows = rows.get("collection", [])
    for row in rows:
        if not exact_track(item, row.get("title"), row.get("user", {}).get("username"),
                           (row.get("duration") or 0) / 1000):
            continue
        url = row.get("download_url")
        if row.get("downloadable") is not True or not isinstance(url, str):
            continue
        if not public_url(url, urlsplit(origin).netloc):
            continue
        # The original-download endpoint redirects to a signed storage URL.
        # Never forward the OAuth header to that URL.
        try:
            with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=15):
                return {"status": "unexpected_download_response"}
        except HTTPError as error:
            target = error.headers.get("Location", "")
            code = error.code
            error.close()
            if code not in {302, 303, 307, 308}:
                return {"status": "http_error", "http_status": code}
            host = urlsplit(target).hostname or ""
            if (host == "sndcdn.com" or host.endswith(".sndcdn.com")
                    or host == "soundcloud.com.s3.amazonaws.com") and public_url(target, host):
                return run_sample(item["id"], "audio", target, folder, item["reference_seconds"])
            return {"status": "unapproved_media_host"}
    return {"status": "no_creator_enabled_match"}


def api_attempt(name, origin, item, watch, folder):
    kind = item["kind"]
    if name in {"deezer", "itunes"}:
        return preview_result(name, origin, item)
    if name == "soundcloud":
        return soundcloud_sample(origin, item, folder)
    if name == "cobalt":
        payload = {"url": watch, "downloadMode": kind if kind == "audio" else "auto",
                   "videoQuality": "720"}
        data = read_json(Request(origin + "/", data=json.dumps(payload).encode(),
                                 headers={"Accept": "application/json",
                                          "Content-Type": "application/json"}))
        status = data.get("status")
        if status not in {"tunnel", "redirect"}:
            code = str(data.get("error", {}).get("code", ""))
            if blocked_text(code) or "auth" in code:
                return {"status": "skipped_provider_challenge"}
            return {"status": "provider_rejected"}
        streams = [data]
    else:
        video = item["youtube_id"]
        path = "/streams/" + video if name == "piped" else "/api/v1/videos/" + video + "?local=true"
        data = read_json(origin + path)
        if data.get("error"):
            return {"status": "skipped_provider_challenge" if blocked_text(str(data["error"]))
                    else "provider_rejected"}
        if name == "piped":
            streams = data.get("audioStreams" if kind == "audio" else "videoStreams", [])
            if kind == "video":
                streams = [row for row in streams if not row.get("videoOnly", True)]
        else:
            streams = data.get("adaptiveFormats" if kind == "audio" else "formatStreams", [])
            if kind == "audio":
                streams = [row for row in streams if str(row.get("type", "")).startswith("audio/")]
        if kind == "video":
            streams = [row for row in streams
                       if int(row.get("height") or re.sub(r"\D.*", "", row.get("qualityLabel") or
                                                              row.get("quality") or "0") or 0) >= 720]
    for url in proxy_media(origin, streams):
        return run_sample(item["id"], kind, url, folder, item.get("reference_seconds"))
    return {"status": "no_approved_proxy_stream"}


def probe_api_route(name, item, watch, folder, blocked=None):
    if blocked is None:
        blocked = set()
    attempts = {}
    for origin in api_origins(name):
        if origin in blocked:
            attempts[urlsplit(origin).netloc] = {"status": "skipped_provider_block"}
            continue
        try:
            result = api_attempt(name, origin, item, watch, folder)
        except HTTPError as error:
            result = {"status": "http_error", "http_status": error.code}
            error.close()
        except ProviderChallenge:
            result = {"status": "skipped_provider_challenge"}
        except (OSError, HTTPException, ValueError, TypeError, AttributeError) as error:
            result = {"status": "api_unavailable", "error_type": type(error).__name__}
        if result["status"] == "skipped_provider_challenge" or result.get("http_status") in {401, 403, 429}:
            blocked.add(origin)
        attempts[urlsplit(origin).netloc] = result
    if not attempts:
        return {"status": "skipped_no_permitted_instance"}
    best = next((row for row in attempts.values() if row["status"] in {"verified", "preview_metadata"}),
                next(iter(attempts.values())))
    return {**best, "instances": attempts}


def provider_url(url, provider):
    try:
        host = urlsplit(url).netloc
    except ValueError:
        return False
    if provider == "notube" and (host == "cdn.notube.net" or re.fullmatch(r"s[0-9]{2}\.notube\.net", host)):
        return public_url(url, host)
    if provider == "ythd" and host == "clickapi.net":
        return public_url(url, "clickapi.net")
    return public_url(url, urlsplit(PROVIDERS[provider]).netloc)


def blocked_text(value):
    value = value.casefold()
    return any(fragment in value for fragment in BLOCK_MESSAGES + (
        "just a moment", "checking your browser", "captcha verification", "turnstile", "proof-of-work", "proof of work",
    ))


def browser_blocked(page):
    return (blocked_text(page.locator("body").inner_text(timeout=3000)) or
            page.locator("iframe[src*='captcha'], iframe[src*='challenges.cloudflare.com']").count() > 0)


def wait_for_link(page, selector, provider, kind, *, result_frame=False):
    deadline = time.monotonic() + 90
    selected_format = False
    seen_frame = False
    while time.monotonic() < deadline:
        if browser_blocked(page):
            return None, "challenge_detected"
        if provider == "directyt" and page.locator("#dl-error").is_visible():
            return None, "provider_rejected"
        frames = [page]
        if result_frame:
            frames = [frame for frame in page.frames if frame.parent_frame
                      and provider_url(frame.url, provider)]
            seen_frame |= bool(frames)
        for frame in frames:
            if frame is not page and browser_blocked(frame):
                return None, "challenge_detected"
            for link in frame.locator(selector).all():
                if link.is_visible():
                    href = link.get_attribute("href", timeout=3000)
                    if href and not href.startswith("#"):
                        return urljoin(frame.url, href), "link_found"
            if result_frame and not selected_format:
                label = re.compile(r"^(?:convert to |download )?(?:"
                                   + ("MP3" if kind == "audio" else "MP4|720p") + r")$", re.I)
                for choice in frame.get_by_role("button", name=label).all():
                    if choice.is_visible():
                        choice.click()
                        selected_format = True
                        break
        page.wait_for_timeout(1000)
    return None, "no_result_frame" if result_frame and not seen_frame else "no_download_link"

def submit_public_form(page, watch, kind):
    """Use labelled controls on the public page, without submitting an ambiguous form."""
    fields = page.locator(
        "input[type=url], input[placeholder*='youtube' i], input[placeholder*='paste' i]"
    )
    field = next((candidate for candidate in fields.all() if candidate.is_visible()), None)
    if field is None:
        return False
    field.fill(watch)
    formats = ("mp3",) if kind == "audio" else ("720p", "mp4 hd", "1080p", "mp4")
    for menu in page.locator("select").all():
        if not menu.is_visible():
            continue
        options = menu.locator("option")
        for fmt in formats:
            match = next((option for option in options.all()
                          if fmt in (option.text_content() or "").casefold()), None)
            if match is not None:
                menu.select_option(value=match.get_attribute("value") or match.text_content())
                break
    if browser_blocked(page):
        return False
    format_button = page.get_by_role("button", name=re.compile(
        r"^(?:convert to |get )?(?:" + ("mp3" if kind == "audio" else "mp4|720p") + r")$", re.I
    ))
    submit = page.get_by_role("button", name=re.compile(
        r"^(?:convert|start|download|get video|get audio|go)(?: now)?$", re.I
    ))
    for controls in (format_button, submit):
        button = next((candidate for candidate in controls.all() if candidate.is_visible()), None)
        if button is not None:
            button.click()
            return True
    return False


def generic_download_selector(kind):
    if kind == "audio":
        return "a[download][href*='.mp3' i], a:has-text('Download MP3'), a[href$='.mp3' i]"
    return ("a[download][href*='.mp4' i], a:has-text('Download MP4'), "
            "a:has-text('Download 720p'), a:has-text('Download 1080p'), a[href$='.mp4' i]")


def mp7_convert(item, watch, folder):
    base = PROVIDERS["mp7"]
    media_format = "mp3" if item["kind"] == "audio" else "mp4"
    request = Request(
        urljoin(base, "/convert"),
        data=urlencode({"url": watch, "format": media_format}).encode(),
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
    )
    stage = "submit"
    poll_failures = 0
    try:
        result = read_json(request)
        deadline = time.monotonic() + 120
        while isinstance(result, dict):
            if blocked_text(str(result.get("error", ""))):
                return {"status": "challenge_detected", "stage": stage}
            if not result.get("success") or result.get("status") == "failed":
                return {"status": "provider_rejected", "stage": stage}
            if result.get("status") == "complete":
                link = result.get("download_url")
                if not isinstance(link, str) or not provider_url(urljoin(base, link), "mp7"):
                    return {"status": "unsupported_download_origin", "stage": stage}
                media = run_sample(item["id"], item["kind"], urljoin(base, link), folder,
                                   item.get("reference_seconds"))
                return {**media, "method": "http_json", "stage": "media_transfer"}
            if time.monotonic() >= deadline:
                return {"status": "provider_job_timeout", "stage": stage}
            if stage == "submit":
                job_id = result.get("job_id")
                if type(job_id) is int and job_id > 0:
                    job_id = str(job_id)
                if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job_id):
                    break
            stage = "poll"
            time.sleep(2)
            try:
                result = read_json(urljoin(base, "/status/" + job_id))
            except HTTPError as error:
                poll_failures += 1
                if error.code not in {500, 502, 503, 504} or poll_failures >= 3:
                    raise
                error.close()
            else:
                poll_failures = 0
    except HTTPError as error:
        error.close()
        return {"status": "http_error", "http_status": error.code, "stage": stage}
    except (OSError, ValueError, HTTPException) as error:
        return {"status": "provider_response_unavailable", "error_type": type(error).__name__, "stage": stage}
    return {"status": "unexpected_provider_response", "stage": stage}


def convert(browser, provider, item, watch, folder):
    landing = item.get("provider_pages", {}).get(provider, PROVIDERS[provider])
    if not provider_url(landing, provider):
        return {"status": "unsupported_page_origin"}
    context = browser.new_context(accept_downloads=False, service_workers="block")
    context.route("**/*", lambda route: (
        route.continue_() if provider_url(route.request.url, provider) else route.abort()
    ))
    stage = "landing"
    result_frame = False
    response_codes = set()
    try:
        page = context.new_page()
        page.on("response", lambda response: response_codes.add(response.status)
                if response.request.resource_type in {"xhr", "fetch"} else None)
        page.set_default_timeout(8000)
        response = page.goto(landing, wait_until="domcontentloaded", timeout=15000)
        if response is None:
            return {"status": "no_landing_response"}
        if not response.ok:
            return {"status": "browser_http_error", "http_status": response.status}
        if browser_blocked(page):
            return {"status": "challenge_detected"}
        stage = "form"
        if provider == "notube":
            page.locator("#keyword").fill(watch)
            audio = item["kind"] == "audio"
            if page.locator("#myDropdown").is_visible():
                page.locator("#myDropdown").select_option("mp3" if audio else "mp4hd")
            else:
                page.locator("#fmtBtn").click()
                page.locator("#fmtPanel").get_by_text("MP3" if audio else "MP4 HD", exact=True).click()
            page.locator("#submit-button").click()
            selector = "a[download], a[href*='/download/']:text-is('Download'), a:text-is('Download')"
        elif provider == "directyt":
            if page.locator("#consent-decline").is_visible():
                page.locator("#consent-decline").click()
            page.locator("#dl-url").fill(watch)
            page.locator("#dl-form button[type=submit]").click()
            if item["kind"] == "audio":
                page.locator("#tab-mp3").click()
                selector = "#panel-mp3 a[href]:not([href='#'])"
            else:
                selector = "#dl-video-list li:has-text('720p') a[href], #dl-video-list li:has-text('1080p') a[href]"
        elif provider == "cnvmp3":
            fields = page.locator("#video-url")
            field = next((candidate for candidate in fields.all() if candidate.is_visible()), None)
            if field is None:
                return {"status": "public_form_unavailable", "stage": stage}
            field.fill(watch)
            page.locator("#format-select-display").click()
            option = 1 if item["kind"] == "audio" else 0
            page.locator(f".format-select-options[data-format='{option}']").click()
            if item["kind"] == "video":
                quality = page.locator("#quality-video-select-display")
                if quality.is_visible():
                    quality.click()
                    page.locator("#quality-video-select-list-720").click()
            page.locator("#convert-button-1").first.click()
            result_frame = True
            selector = generic_download_selector(item["kind"]) + ", a:text-is('Download')"
        elif provider == "ythd":
            page.locator(".convert-form input[name=url]").fill(watch)
            page.locator(".convert-form button[type=submit]").click()
            result_frame = True
            selector = generic_download_selector(item["kind"])
        elif provider in DIRECT_MP3:
            selector = generic_download_selector("audio")
        else:
            if not submit_public_form(page, watch, item["kind"]):
                return {"status": "challenge_detected" if browser_blocked(page) else "public_form_unavailable"}
            selector = generic_download_selector(item["kind"])
        stage = "result_link"
        url, status = wait_for_link(page, selector, provider, item["kind"], result_frame=result_frame)
        if url is None:
            return {"status": status, "stage": stage, "request_statuses": sorted(response_codes)}
        if not provider_url(url, provider):
            return {"status": "unsupported_download_origin", "stage": stage}
        stage = "media_transfer"
        result = run_sample(item["id"], item["kind"], url, folder, item.get("reference_seconds"))
        return {**result, "method": "headless_then_http", "stage": stage}
    except Exception as error:
        return {"status": "browser_error", "stage": stage,
                "error_type": type(error).__name__, "request_statuses": sorted(response_codes)}
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
        video_id = parse_qs(urlsplit(watch).query).get("v", [None])[0]
        return replay_result(state, video_id=video_id)
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


def read_corpus(baseline=False):
    filename = "m0_baseline.json" if baseline else "m0_corpus.json"
    items = json.loads((ROOT / "research" / filename).read_text(encoding="utf-8"))
    for kind in ("audio", "video"):
        if sum(item["kind"] == kind for item in items) != 10:
            raise ValueError("expected ten items of each kind")
    if len(items) != 20 or len({item["youtube_id"] for item in items}) != 20:
        raise ValueError("expected twenty distinct watch IDs")
    for item in items:
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", item["youtube_id"]):
            raise ValueError("invalid watch identifier")
        watch = f"https://www.youtube.com/watch?v={item['youtube_id']}"
        if not baseline and (item.get("watch_url") != watch or not item.get("source_url")):
            raise ValueError("invalid target provenance")
        if item["kind"] == "audio" and (
            type(item.get("reference_seconds")) not in (int, float)
            or not math.isfinite(item["reference_seconds"]) or item["reference_seconds"] <= 0
        ):
            raise ValueError("invalid audio reference duration")
        if baseline and item["kind"] == "audio" and not public_url(item["direct_url"], "incompetech.com"):
            raise ValueError("invalid audio reference origin")
        if baseline and item["kind"] == "video" and not re.fullmatch(r"[A-Za-z0-9]{22}", item["peertube_id"]):
            raise ValueError("invalid Blender video identifier")
    return items


def provider_summary(providers, rows):
    summaries = {}
    for provider in providers:
        summaries[provider] = {}
        for kind in ("audio", "video"):
            if provider in AUDIO_ONLY and kind == "video":
                continue
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


def timestamp_reference(control, attempts):
    if control.get("status") == "verified":
        return "publisher", control
    for name, attempt in attempts.items():
        if attempt["status"] == "verified":
            return name, attempt
    return None, {}


def outcome_summary(rows):
    from collections import Counter

    summary = {}
    for kind in ("audio", "video"):
        subset = [row for row in rows if row["kind"] == kind]
        summary[kind] = {
            "publisher_verified": sum(row["publisher_control"]["status"] == "verified" for row in subset),
            "timestamp_methods_selected": dict(Counter(
                (row["timestamps"]["selected"] or {}).get("method", "unavailable") for row in subset
            )),
        }
    return summary


@contextmanager
def start_browser(executable):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("Playwright is missing. Run the probe setup in NEEDS_MANAS.md.") from error
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                executable_path=executable, headless=True, chromium_sandbox=True, timeout=15000,
            )
        except Exception as error:
            raise RuntimeError(
                "Chromium preflight failed. Install the browser and Linux system dependencies "
                "with the commands in NEEDS_MANAS.md. If already installed, check sandbox or "
                f"permission errors in the Chromium diagnostic below.\n{error}"
            ) from error
        try:
            yield browser
        finally:
            browser.close()


def browser_failure_reason(error):
    detail = str(error).casefold()
    for fragment, reason in (
        ("no usable sandbox", "sandbox_unavailable"),
        ("socket() failed", "browser_socket_denied"),
        ("executable doesn’t exist", "browser_missing"),
        ("executable doesn't exist", "browser_missing"),
        ("dependencies", "browser_start_failed"),
        ("playwright is missing", "playwright_missing"),
    ):
        if fragment in detail:
            return reason
    return "browser_start_failed"


def run_matrix(args):
    corpus = [(False, item) for item in read_corpus()]
    if args.limit:
        corpus = [pair for kind in ("audio", "video")
                  for pair in [entry for entry in corpus if entry[1]["kind"] == kind][:args.limit]]
    if not args.limit:
        corpus.extend((True, item) for item in read_corpus(baseline=True))
    versions = tool_versions()
    if not versions["ffmpeg"] or not versions["ffprobe"]:
        raise RuntimeError("The M0 matrix requires working ffmpeg and ffprobe on PATH.")
    rows = []
    key = os.getenv("GEMINI_API_KEY") if args.gemini_free_project else None
    blocked = set()
    with ExitStack() as stack:
        folder = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="resolve-m0-")))
        browser = None
        browser_status = "disabled"
        browser_failure = None
        if not args.no_browser:
            try:
                browser = stack.enter_context(start_browser(args.chromium))
            except RuntimeError as error:
                browser_status = "unavailable"
                browser_failure = browser_failure_reason(error)
                print(f"Chromium unavailable ({browser_failure}); continuing plain HTTP checks.", flush=True)
            else:
                browser_status = "ready"
        requested = getattr(args, "providers", None)
        landings = {name: landing_http(url) for name, url in PROVIDERS.items()
                    if requested is None or name in requested}
        for name in API_ROUTES:
            if requested is None or name in requested:
                landings[name] = {"status": "api_probe"}
        grounding = gemini_grounding(key)
        for baseline, item in corpus:
            watch = f"https://www.youtube.com/watch?v={item['youtube_id']}"
            print(f"Probing {item['kind']} {item['title']}", flush=True)
            control = direct_control(item, folder) if baseline else {"status": "not_applicable"}
            replay = probe_replay(watch)
            headless_replay = browser_replay(browser, watch, replay)
            attempts = {}
            target = {**item}
            if item["kind"] == "video":
                target["reference_seconds"] = control.get("duration_seconds")
            for name, landing in ({} if baseline else landings).items():
                if name in AUDIO_ONLY and item["kind"] == "video":
                    continue
                if name in DIRECT_MP3 and name not in item.get("provider_pages", {}):
                    attempts[name] = {"status": "skipped_no_matched_direct_page"}
                elif (name in blocked or landing["status"] == "challenge_detected" or
                        landing.get("http_status") in {401, 403, 429}):
                    attempts[name] = {"status": "skipped_provider_block"}
                elif name in API_ROUTES:
                    attempts[name] = probe_api_route(name, target, watch, folder, blocked)
                elif name == "mp7":
                    attempts[name] = mp7_convert(target, watch, folder)
                elif name == "loader":
                    attempts[name] = {"status": "skipped_provider_challenge"}
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
            audio_source, reference_media = timestamp_reference(control, attempts)
            social = social_timestamp(browser, item, reference_media)
            gemini = gemini_timestamp(key, grounding["status"] == "grounded", item, watch, reference_media)
            rows.append({
                "id": item["id"], "kind": item["kind"], "title": item["title"],
                "corpus": "baseline" if baseline else "target", "watch_url": watch,
                "publisher_control": control, "converters": attempts,
                "timestamps": {
                    "youtube_replay": replay,
                    "youtube_replay_headless": headless_replay,
                    "social": social,
                    "gemini": gemini,
                    "validation_audio_source": audio_source,
                    "audio_energy": reference_media.get("energy", {"status": "no_reference_audio"}),
                    "selected": chosen_timestamp(replay, headless_replay, social, gemini, reference_media),
                },
            })
            write_report(args.output, matrix_report(args, rows, landings, browser_status, grounding, False, versions, browser_failure))
    return matrix_report(args, rows, landings, browser_status, grounding, True, versions, browser_failure)


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


def matrix_report(args, rows, landings, browser_status, grounding, complete, versions, browser_failure=None):
    target = [row for row in rows if row["corpus"] == "target"]
    baseline = [row for row in rows if row["corpus"] == "baseline"]
    return {
        "schema_version": 3,
        "scope": "m0_diagnostic" if args.limit or args.no_browser or getattr(args, "providers", None) else "m0_matrix",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "complete": complete,
        "tools": versions,
        "browser_status": browser_status,
        "browser_failure": browser_failure,
        "http_landing_checks": landings,
        "provider_summary": provider_summary(landings, target),
        "outcome_summary": outcome_summary(target),
        "baseline_summary": outcome_summary(baseline),
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
            output.close()
            path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--check-browser", action="store_true", help="launch Chromium without probing any site")
    parser.add_argument("--limit", type=int, choices=range(1, 11), help="diagnostic rows per media kind")
    parser.add_argument("--providers", nargs="+", choices=sorted(PROVIDERS.keys() | API_ROUTES.keys()),
                        help="restrict provider checks to the specified registry names")
    parser.add_argument("--chromium", help="path to installed Chrome or Chromium")
    parser.add_argument("--no-browser", action="store_true", help="run HTTP diagnostics only")
    parser.add_argument("--gemini-free-project", action="store_true",
                        help="use exported GEMINI_API_KEY only if its project has billing disabled")
    args = parser.parse_args()
    if args.check_browser:
        try:
            with start_browser(args.chromium):
                print("Chromium preflight passed.")
        except RuntimeError as error:
            parser.exit(2, str(error) + "\n")
        return 0
    if args.output is None:
        filename = "m0_server_results.json"
        if args.smoke:
            filename = "m0_smoke_results.json"
        elif args.limit or args.no_browser or args.providers:
            filename = "m0_diagnostic_results.json"
        args.output = ROOT / "research" / filename
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
        try:
            report = run_matrix(args)
        except RuntimeError as error:
            parser.exit(2, str(error) + "\n")
    write_report(args.output, report)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
