"""Transfer media with an authenticated SSH transport and verified manifest."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
from hashlib import file_digest
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import ZipFile

from .acquisition_keys import asset_keys
from .config import read_env_file
from .downloader import DownloadRequest, validate_media


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS imported_media(
        path TEXT PRIMARY KEY, kind TEXT NOT NULL, metadata TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS imported_bundles(
        digest TEXT PRIMARY KEY, arrived TEXT NOT NULL)""")
    db.execute("CREATE TABLE IF NOT EXISTS bundle_monitor(key TEXT PRIMARY KEY, value TEXT NOT NULL)")


def write_bundle(output, entries):
    """Package an editor-reviewed iterable of (path, source metadata) pairs."""
    manifest = []
    with TemporaryDirectory(dir=output.parent) as tmp:
        staged = Path(tmp) / "batch.zip"
        with ZipFile(staged, "w") as archive:
            for index, (path, metadata) in enumerate(entries):
                path = Path(path)
                if path.suffix.lower() not in {".mp4", ".webm", ".mp3", ".wav", ".m4a", ".flac", ".ogg"}:
                    raise ValueError("Unsupported bundle file extension")
                member = f"media/{index}{path.suffix.lower()}"
                with path.open("rb") as stream:
                    checksum = file_digest(stream, "sha256").hexdigest()
                manifest.append({**metadata, "file": member, "sha256": checksum})
                archive.write(path, member)
            if not manifest:
                raise ValueError("Bundle has no media")
            archive.writestr("manifest.json", json.dumps({"version": 1, "entries": manifest},
                                                         allow_nan=False))
        staged.replace(output)


def import_bundle(pipeline, bundle):
    with bundle.open("rb") as stream:
        digest = file_digest(stream, "sha256").hexdigest()
    root = pipeline.directory / "library"
    root.mkdir(exist_ok=True)
    with closing(sqlite3.connect(pipeline.database)) as db, db:
        schema(db)
        if db.execute("SELECT 1 FROM imported_bundles WHERE digest=?", (digest,)).fetchone():
            return False
    with TemporaryDirectory(dir=root) as tmp, ZipFile(bundle) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names) or len(names) > 501:
            raise ValueError("Invalid bundle members")
        if "manifest.json" not in names or archive.getinfo("manifest.json").file_size > 1024 * 1024:
            raise ValueError("Invalid bundle manifest")
        manifest = json.loads(archive.read("manifest.json"))
        entries = manifest["entries"]
        if manifest["version"] != 1 or not entries or len(entries) != len(names) - 1:
            raise ValueError("Invalid bundle schema")
        seen = set()
        staged = []
        for row in entries:
            name, kind = row["file"], row["kind"]
            if (not re.fullmatch(r"media/[0-9]+\.(mp4|webm|mp3|wav|m4a|flac|ogg)", name)
                    or name in seen or kind not in {"song", "clip", "speech"}):
                raise ValueError("Invalid bundle asset")
            if kind == "clip" and row.get("content") != "physical_training":
                raise ValueError("Production clips must be physical training footage")
            if not isinstance(row.get("source"), str) or not row["source"].strip():
                raise ValueError("Bundle asset requires source metadata")
            seen.add(name)
            if name not in names or archive.getinfo(name).file_size > 600 * 1024 * 1024:
                raise ValueError("Bundle asset missing or too large")
            path = Path(tmp) / Path(name).name
            with archive.open(name) as stream, path.open("wb") as output:
                shutil.copyfileobj(stream, output)
            with path.open("rb") as stream:
                checksum = file_digest(stream, "sha256").hexdigest()
            if checksum != row["sha256"]:
                raise ValueError("Bundle checksum mismatch")
            timestamp = row["timestamp"]
            media = validate_media(path, DownloadRequest(row["source"], "video" if kind == "clip" else "audio",
                                                         row.get("duration")))
            if type(timestamp) not in {float, int} or not 0 <= timestamp < media.duration_seconds:
                raise ValueError("Invalid asset timestamp")
            staged.append((path, root / (checksum + path.suffix),
                           {**row, "duration": media.duration_seconds}))

        with closing(sqlite3.connect(pipeline.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM imported_bundles WHERE digest=?", (digest,)).fetchone():
                return False
            existing = inventory_keys(db)
            for path, destination, row in staged:
                keys = asset_keys(row)
                if keys & existing:
                    continue
                existing.update(keys)
                if not destination.exists():
                    path.replace(destination)
                db.execute("""INSERT INTO imported_media VALUES(?,?,?)
                              ON CONFLICT(path) DO UPDATE SET metadata=excluded.metadata
                              WHERE kind=excluded.kind""",
                           (str(destination), row["kind"], json.dumps(row)))
                db.execute("INSERT OR IGNORE INTO library(path,kind,peak) VALUES(?,?,?)",
                           (str(destination), row["kind"], row["timestamp"]))
            db.execute("INSERT INTO imported_bundles VALUES(?,?)",
                       (digest, datetime.now(timezone.utc).isoformat()))
    return True


def ssh_target(settings):
    host = settings["RESOLVE_SSH_HOST"]
    inbox = settings["RESOLVE_SSH_INBOX"].rstrip("/")
    key = str(Path(settings["RESOLVE_SSH_KEY"]).expanduser().resolve(strict=True))
    if not re.fullmatch(r"(?:[a-zA-Z0-9_]+@)?[a-zA-Z0-9][a-zA-Z0-9.-]*", host):
        raise ValueError("Invalid SSH host")
    if not re.fullmatch(r"/[a-zA-Z0-9_/-]+", inbox) or ".." in inbox:
        raise ValueError("Invalid remote inbox")
    options = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-i", key]
    return host, inbox, options


def fetch_inventory(settings, destination):
    host, inbox, options = ssh_target(settings)
    subprocess.run(["scp", *options, f"{host}:{inbox}/inventory.json", str(destination)],
                   check=True, timeout=60)
    entries = json.loads(destination.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not all(isinstance(key, str) for key in entries):
        raise ValueError("Invalid acquisition inventory")
    return set(entries)


def inventory_keys(db):
    return {key for (metadata,) in db.execute("SELECT metadata FROM imported_media")
            for key in asset_keys(json.loads(metadata))}


def sync_bundle(bundle, settings):
    """Publish a unique ready filename only after the upload completes."""
    host, inbox, options = ssh_target(settings)
    name = "resolve-" + uuid4().hex
    pending = f"{inbox}/{name}.part"
    subprocess.run(["scp", *options, str(bundle.resolve()), f"{host}:{pending}"],
                   check=True, timeout=3600)
    subprocess.run(["ssh", *options, host, "mv", "--", pending, f"{inbox}/{name}.zip"],
                   check=True, timeout=30)


def receive_bundles(pipeline, inbox, now, alert):
    """Import ready bundles and send one alert per stale interval."""
    from datetime import timedelta
    from zipfile import BadZipFile

    for bundle in sorted(inbox.glob("resolve-*.zip")):
        try:
            import_bundle(pipeline, bundle)
        except (BadZipFile, ValueError, KeyError, TypeError, OSError) as error:
            pipeline.log_action("bundle", f"{bundle.name}: {type(error).__name__}")
            bundle.rename(bundle.with_suffix(".rejected"))
        else:
            bundle.unlink()
    with closing(sqlite3.connect(pipeline.database)) as db, db:
        with TemporaryDirectory(dir=inbox) as folder:
            inventory = Path(folder) / "inventory.json"
            inventory.write_text(json.dumps(sorted(inventory_keys(db))), encoding="utf-8")
            inventory.replace(inbox / "inventory.json")
        db.execute("INSERT OR IGNORE INTO bundle_monitor VALUES('started',?)", (now.isoformat(),))
        started = db.execute("SELECT value FROM bundle_monitor WHERE key='started'").fetchone()[0]
        arrived = db.execute("SELECT MAX(arrived) FROM imported_bundles").fetchone()[0] or started
        alerted = db.execute("SELECT value FROM bundle_monitor WHERE key='alerted'").fetchone()
    if alert and now - datetime.fromisoformat(arrived) >= timedelta(days=9) and alerted != (arrived,):
        alert("Resolve has received no verified media bundle in nine days. Existing media remains in use.")
        with closing(sqlite3.connect(pipeline.database)) as db, db:
            db.execute("INSERT OR REPLACE INTO bundle_monitor VALUES('alerted',?)", (arrived,))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    sync_bundle(args.bundle, read_env_file(args.env_file))


if __name__ == "__main__":
    main()
