"""Build a bounded acquisition candidate list from public feeds and YouTube metadata."""
from collections import defaultdict
from itertools import zip_longest
import json
from pathlib import Path
import re
import subprocess
import sys

from resolve_pipeline.acquisition_keys import asset_keys, watch_url, words
from resolve_pipeline.public_sources import article_watch_links, spotify_playlist_tracks
from tools.m0_probe import blocked_text


ROOT = Path(__file__).resolve().parents[1]


def youtube(target):
    command = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--skip-download", "--dump-single-json",
               "--no-warnings", "--socket-timeout", "15", "--retries", "1", "--", target]
    process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=75)
    if blocked_text(process.stderr):
        raise ValueError("provider_challenge")
    if process.returncode:
        print("YouTube metadata request failed; skipping candidate.")
        return []
    payload = json.loads(process.stdout)
    rows = payload.get("entries") if payload.get("_type") == "playlist" else [payload]
    return [row for row in rows or [] if isinstance(row, dict)]


def video_item(row, kind, page):
    identifier, duration = row.get("id"), row.get("duration")
    ceiling = 300 if kind == "song" else 900
    if (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier)
            or type(duration) not in (int, float) or not 22 <= duration <= ceiling
            or row.get("live_status") in {"is_live", "is_upcoming"}):
        return None
    return {"kind": kind, "source": f"https://www.youtube.com/watch?v={identifier}",
            "title": row.get("title") or identifier, "duration": duration,
            "source_pages": [page], "views": row.get("view_count") or 0,
            "upload_date": row.get("upload_date") or ""}


def rank(entries, existing):
    seen = set(existing)
    ordered = sorted(entries, key=lambda item: (
        bool(item.get("pinned")), len(set(item.get("source_pages", []))),
        item.get("upload_date", ""), item.get("views", 0)), reverse=True)
    for item in ordered:
        keys = asset_keys(item)
        if not keys.intersection(seen):
            seen.update(keys)
            yield item


def discover(config, pins, existing, fetch, search=youtube, *, track_limit=45):
    candidates = [{**item, "pinned": True} for item in pins]
    tracks = {}
    for page in config["spotify_playlists"]:
        try:
            playlist = spotify_playlist_tracks(fetch(page))
        except (ValueError, OSError) as error:
            print(f"Spotify source skipped ({type(error).__name__}).")
            continue
        for track in playlist:
            record, pages = tracks.setdefault(track.id, (track, set()))
            pages.add(page)
    unsynced_tracks = []
    for track, pages in tracks.values():
        seed = {"kind": "song", "spotify_id": track.id, "title": f"{track.title} — {track.artist}"}
        if not asset_keys(seed) & existing:
            unsynced_tracks.append((track, pages, seed))
    by_genre = defaultdict(list)
    for track, pages, seed in sorted(unsynced_tracks, key=lambda row: -len(row[1])):
        genre = next((config.get("playlist_genres", {})[p] for p in sorted(pages)
                      if p in config.get("playlist_genres", {})), "phonk")
        by_genre[genre].append((track, pages, seed, genre))
    # Interleave feeds before the lookup budget is exhausted by the first playlist.
    balanced = [entry for group in zip_longest(*by_genre.values()) for entry in group if entry]
    for track, pages, seed, genre in balanced[:track_limit]:
        for row in search(f"ytsearch4:{track.title} {track.artist} official audio"):
            item = video_item(row, "song", sorted(pages)[0])
            if item is None:
                continue
            title = words(item["title"])
            artist = words(track.artist.split(",")[0])
            if (abs(item["duration"] - track.duration_seconds) <= 3
                    and words(track.title) in title
                    and (artist in title or artist in words(row.get("channel") or row.get("uploader") or ""))):
                candidates.append({**item, **seed, "duration": track.duration_seconds,
                                   "genre": genre, "source_pages": sorted(pages),
                                   "duration_reference": "spotify"})
                break

    for kind, pages in (("song", config["song_articles"]), ("clip", config["training_pages"])):
        links = {}
        for page in pages:
            try:
                for url in article_watch_links(fetch(page)):
                    links.setdefault(url, []).append(page)
            except (OSError, ValueError) as error:
                print(f"Article source skipped ({type(error).__name__}).")
        for url, appearances in list(links.items())[:30]:
            if asset_keys({"kind": kind, "source": url}) & existing:
                continue
            for row in search(url):
                item = video_item(row, kind, appearances[0])
                if not item:
                    continue
                if kind == "song":
                    title = words(item["title"])
                    if not any(tag in title for tag in ("phonk", "gym edit", "montagem", "funk")):
                        continue
                    item.update(genre="gym_edit", duration_reference="youtube_metadata")
                    if row.get("track") and row.get("artist"):
                        item["title"] = f"{row['track']} — {row['artist']}"
                item["source_pages"] = appearances
                candidates.append(item)
    for query in config["training_searches"]:
        for row in search(f"ytsearch15:{query}"):
            item = video_item(row, "clip", f"youtube-search:{query}")
            if item:
                candidates.append(item)
    merged = {}
    for item in candidates:
        item["source"] = watch_url(item["source"]) or item["source"]
        key = (item["kind"], item["source"])
        previous = merged.setdefault(key, item)
        if previous is not item:
            previous["source_pages"] = sorted(set(previous.get("source_pages", []) + item.get("source_pages", [])))
            for field in ("spotify_id", "genre", "duration_reference"):
                if field in item:
                    previous.setdefault(field, item[field])
    return list(rank(merged.values(), existing))
