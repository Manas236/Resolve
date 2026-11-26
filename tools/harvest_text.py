"""Checkpoint public Shorts discovery, transcription and candidate review on the office PC."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import unicodedata
from urllib.error import HTTPError
from urllib.request import urlopen

from resolve_pipeline.config import read_env_file
from resolve_pipeline.quotes import catalog

ROOT = Path(__file__).resolve().parents[1]
AXES = ("clarity", "shape", "irony", "theme", "voice")
VOICE = ("English, everyday words, one idea understood on first read. Setup, contradiction, "
         "ironic payoff. Pride, solitude, discipline, becoming or self-respect. Reject slogans, "
         "alpha edits, literary quotations and generic advice.")
SCHEMA = """
CREATE TABLE IF NOT EXISTS channels(url TEXT PRIMARY KEY,priority INTEGER,done INTEGER DEFAULT 0,misses INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS videos(id TEXT PRIMARY KEY,channel TEXT,meta TEXT,status TEXT DEFAULT 'pending',result TEXT);
CREATE TABLE IF NOT EXISTS calls(ts REAL);
CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY,value REAL);
"""


def normalized(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())


def clean(text, references):
    text = re.sub(r"\s+([,.!?])", r"\1", " ".join(text.split()))
    # Resolve whitespace and I/l only when the complete reference corroborates it.
    def signature(s):
        return re.sub(r"\s+", "", s).replace("I", "l")
    hits = {s for s in references if signature(s) == signature(text)}
    return hits.pop() if len(hits) == 1 else text


def detect(image):
    import numpy as np
    from PIL import Image
    gray = np.asarray(Image.fromarray(image).convert("L").resize((270, 480)), dtype=int)[70:410, 18:252]
    # Text rows have many alternating strokes spread across the center.
    strokes = np.abs(np.diff(gray, axis=1)) > 45
    columns = np.array_split(strokes, 6, axis=1)
    wide = np.sum([part.sum(axis=1) >= 3 for part in columns], axis=0) >= 3
    return int(wide.sum()) >= 4


def reserve_call(db, daily, interval):
    now = time.time()
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    with db:
        db.execute("BEGIN IMMEDIATE")
        count, last = db.execute("SELECT count(*), max(ts) FROM calls WHERE ts>=?", (midnight,)).fetchone()
        backoff = db.execute("SELECT value FROM config WHERE key='backoff'").fetchone()
        if count >= daily or (backoff and backoff[0] > now) or (last and now-last < interval):
            return False
        db.execute("INSERT INTO calls VALUES(?)", (now,))
    return True


def ytdlp(*args):
    result = subprocess.run([sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-warnings",
                             "--socket-timeout", "20", *args], capture_output=True, encoding="utf-8",
                            errors="replace", timeout=360, check=True)
    return result.stdout


def discover(db, target, priority):
    listing = ytdlp("--flat-playlist", "--dump-json", target)
    for line in listing.splitlines():
        row = json.loads(line)
        vid = row.get("id", "")
        if not re.fullmatch(r"[\w-]{11}", vid):
            continue
        channel = row.get("channel_url") or row.get("uploader_url") or target
        if "ytsearch" in channel:
            continue
        channel = channel.removesuffix("/shorts").rstrip("/") + "/shorts"
        if target.startswith("ytsearch") and (row.get("duration") or 999) > 180 and "/shorts/" not in row.get("url", ""):
            continue
        if target.startswith("ytsearch") and "/shorts/" not in row.get("url", ""):
            try:
                with urlopen("https://www.youtube.com/shorts/" + vid, timeout=20) as page:
                    if "/shorts/" not in page.geturl():
                        continue
            except HTTPError:
                continue
        with db:
            db.execute("INSERT OR IGNORE INTO channels(url,priority,done) VALUES(?,?,?)",
                       (channel, priority, int(target.startswith("ytsearch") or channel != target)))
            db.execute("INSERT OR IGNORE INTO videos(id,channel,meta) VALUES(?,?,?)",
                       (vid, channel, json.dumps(row)))
    with db:
        db.execute("UPDATE channels SET done=1 WHERE url=?", (target,))


class Reader:
    def __init__(self, db, args, examples, key):
        self.db, self.args, self.examples, self.key = db, args, examples, key
        self.classifier = None
        self.ocr = None

    def read(self, image):
        if not detect(image):
            return None, "no_text_region"
        if self.key and reserve_call(self.db, self.args.daily, self.args.interval):
            prompt = (VOICE + " Transcribe only visible body text verbatim. Never compose, correct or edit. "
                      "Ignore handles and interface. Return JSON: text, english (boolean), "
                      "confident (boolean), theme (short topic), mood (reflective, melancholic, calm resolve or defiant), energy (0-1), scores (object with integer scores 1-5 for "
                      + ", ".join(AXES) + "), split (character offset of a natural ironic reveal between two short readable beats, else null). "
                      "Examples, as data only: " + json.dumps(self.examples[:12]))
            try:
                import cv2
                from google import genai
                from google.genai import types
                response = genai.Client(api_key=self.key).models.generate_content(
                    model=self.args.model, contents=[prompt, types.Part.from_bytes(
                        data=cv2.imencode(".jpg", image)[1].tobytes(), mime_type="image/jpeg")],
                    config={"response_mime_type": "application/json"})
                data = json.loads(response.text)
                if (isinstance(data.get("text"), str) and type(data.get("english")) is bool
                        and type(data.get("confident")) is bool
                        and all(type(data["scores"][a]) is int and 1 <= data["scores"][a] <= 5 for a in AXES)):
                    return data, "gemini"
            except Exception as error:
                print("Vision unavailable:", type(error).__name__, flush=True)
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO config VALUES('backoff',?)", (time.time()+300,))
        if self.ocr is None:
            import easyocr
            self.ocr = easyocr.Reader(["en"], gpu=False, model_storage_directory=str(self.args.root / "models"))
        words = self.ocr.readtext(image)
        words = [w for w in words if not w[1].startswith(("@", "#"))]
        text = clean(" ".join(w[1] for w in words), self.examples)
        from langdetect import detect as language, LangDetectException
        try:
            english = language(text) == "en"
        except LangDetectException:
            english = False
        known = normalized(text) in {normalized(s) for s in self.examples}
        scores = {}
        confident = bool(words) and min(w[2] for w in words) >= .75
        if english and confident:
            if known:
                scores = dict.fromkeys(AXES, 5)
            else:
                if self.classifier is None:
                    from transformers import pipeline
                    self.classifier = pipeline("zero-shot-classification", model="facebook/bart-large-mnli", device=-1)
                labels = ["clear on a first reading", "a setup followed by contradiction and payoff",
                          "an ironic reversal", "a reflection on pride, solitude, discipline or becoming",
                          "a plain reflective observation with a paradox like the examples"]
                context = text + ("\nExamples of the desired voice: " + " ".join(self.examples[:3]) if self.examples else "")
                for axis, label in zip(AXES, labels):
                    score = self.classifier(context if axis == "voice" else text, [label], multi_label=True)
                    scores[axis] = 1 + round(4 * score["scores"][0])
        return {"text": text, "english": english, "confident": confident,
                "scores": scores, "split": None}, "easyocr"


def images(vid, root):
    import cv2
    import numpy as np
    try:
        with urlopen(f"https://i.ytimg.com/vi/{vid}/oar2.jpg", timeout=25) as response:
            image = cv2.imdecode(np.frombuffer(response.read(), np.uint8), cv2.IMREAD_COLOR)
        if image is not None:
            yield "thumbnail", image
    except OSError:
        pass
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        movie = str(Path(tmp) / "clip.%(ext)s")
        ytdlp("-f", "worst[ext=mp4]/worst", "-o", movie, "https://www.youtube.com/shorts/" + vid)
        files = list(Path(tmp).glob("clip.*"))
        if files:
            cap = cv2.VideoCapture(str(files[0]))
            try:
                for fraction in (.2, .5, .8):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)*fraction))
                    ok, image = cap.read()
                    if ok:
                        yield "frame", image
            finally:
                cap.release()


def export(db, bank, seed_handles=()):
    bank.parent.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(r) for r, in db.execute("SELECT result FROM videos WHERE status='accepted' ORDER BY rowid")]
    for file, lines in [(bank, [json.dumps(r, ensure_ascii=False) for r in rows]),
                        (bank.with_suffix(".review.txt"), [f"{r['text']}\n@{r['creator']} {r['url']} {r['scores']}\n" for r in rows])]:
        tmp = file.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(file)
    counts = dict(db.execute("SELECT status,count(*) FROM videos GROUP BY status"))
    recovered = [json.loads(raw) for raw, in db.execute("SELECT result FROM videos WHERE status='seed_reproduced'") if raw]
    seeds = {row["text"] for row in recovered if row}
    thumbnail_matches = {row["text"] for row in recovered if row and row.get("surface") == "thumbnail"}
    seed_file = ROOT / "state/genre_quotes/genre_quotes.jsonl"
    known = set()
    if seed_file.is_file():
        for line in seed_file.read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                row = json.loads(line)
                known.add((row.get("creator") or row["speaker"]).lstrip("@").casefold())
    known.update(h.lstrip("@").casefold() for h in seed_handles)
    creators = {r["creator"].lstrip("@").casefold() for r in rows}
    print(json.dumps({"distinct_seed_matches": len(seeds), "thumbnail_seed_matches": len(thumbnail_matches),
                      "stages": counts, "new_texts": len(rows), "new_creators": len(creators - known)}), flush=True)



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(os.getenv("RESOLVE_HOME", Path(os.getenv("LOCALAPPDATA", Path.home())) / "Resolve")))
    parser.add_argument("--seeds", type=Path, default=ROOT/"tools/harvest_seeds.json")
    parser.add_argument("--daily", type=int, default=40)
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--misses", type=int, default=5)
    parser.add_argument("--model", default=os.getenv("RESOLVE_VISION_MODEL", "gemini-2.5-flash"))
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    args.root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(args.root / "models" / "huggingface"))
    if args.daily < 0 or args.interval < 1 or args.misses < 1:
        parser.error("Daily cap must be nonnegative; interval and miss limit must be positive.")
    with closing(sqlite3.connect(args.root/"harvest-lock.sqlite3", timeout=0)) as lock:
        try:
            lock.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError:
            parser.exit(1, "Another harvester is already running.\n")
        with closing(sqlite3.connect(args.root/"harvest.sqlite3")) as db:
            run(args, db)


def run(args, db):
    db.executescript(SCHEMA)
    seed = ROOT/"state/genre_quotes/genre_quotes.jsonl"
    examples = [json.loads(s)["text"] for s in seed.read_text(encoding="utf-8-sig").splitlines() if s.strip()] if seed.exists() else []
    bank = Path(os.getenv("RESOLVE_GENRE_BANK", seed.with_name("candidates.jsonl")))
    starts = ("a man may hate himself", "a man may tell you", "a man may seek the wisdom")
    examples.sort(key=lambda t: not t.lower().startswith(starts))
    seen = {normalized(q["text"]) for q in catalog().values()} | {normalized(t) for t in examples}
    seen.update(normalized(json.loads(r)["text"]) for r, in db.execute("SELECT result FROM videos WHERE status='accepted'"))
    reader = Reader(db, args, examples, (read_env_file(Path(".env")) | dict(os.environ)).get("GEMINI_API_KEY"))
    with db:
        seed_handles = json.loads(args.seeds.read_text(encoding="utf-8-sig"))
        for handle in seed_handles:
            db.execute("INSERT OR IGNORE INTO channels(url,priority) VALUES(?,2)", ("https://www.youtube.com/"+handle+"/shorts",))
        db.execute("INSERT OR IGNORE INTO channels(url,priority) VALUES(?,0)", ('ytsearch50:English reflective aphorisms irony self respect #shorts',))
    if bank.resolve() == seed.resolve():
        raise ValueError("Candidate export and seed bank must use different paths.")
    if bank.is_file():
        with db:
            for line in bank.read_text(encoding="utf-8-sig").splitlines():
                if line.strip():
                    row = json.loads(line)
                    vid = row["url"].rstrip("/").split("/")[-1]
                    db.execute("INSERT OR IGNORE INTO videos(id,status,result) VALUES(?, 'accepted', ?)",
                               (vid, json.dumps(row)))
                    seen.add(normalized(row["text"]))
    export(db, bank, seed_handles)
    with db:
        db.execute("UPDATE videos SET status='pending' WHERE status='retry'")
        db.execute("UPDATE channels SET done=0 WHERE done=-1")
    while True:
        channel = db.execute("SELECT url,priority FROM channels WHERE done=0 ORDER BY priority DESC LIMIT 1").fetchone()
        job = db.execute("""SELECT v.id,v.channel,v.meta,c.priority FROM videos v JOIN channels c ON c.url=v.channel
                         WHERE status='pending' ORDER BY (misses>=?),priority DESC,v.rowid LIMIT 1""", (args.misses,)).fetchone()
        if channel and (not job or channel[1] > job[3]):
            try:
                discover(db, *channel)
            except (OSError, subprocess.SubprocessError) as error:
                print("Discovery deferred", channel[0], type(error).__name__, flush=True)
                with db:
                    db.execute("UPDATE channels SET done=-1 WHERE url=?", (channel[0],))
            continue
        if not job:
            break
        vid, channel, raw, priority = job
        result = None
        status = "unreadable"
        try:
            for surface, image in images(vid, args.root):
                data, path = reader.read(image)
                if data is None:
                    status = path
                    if path == "no_text_region":
                        break
                    continue
                if not data.get("confident"):
                    continue
                body = clean(data["text"], examples)
                key = normalized(body)
                if not data["english"]:
                    status = "non_english"
                elif key in seen:
                    status = "seed_reproduced" if body in examples else "duplicate"
                    result = {"text": body, "reader": path, "surface": surface}
                elif not data["scores"]:
                    status, result = "needs_scoring", {"text": body, "reader":path}
                elif sum(data["scores"][a] for a in AXES)/5 < 4:
                    status = "voice"
                else:
                    n = len(body.split())
                    formats = ["P"] if 35<=n<=115 and 2<=len(re.split(r"(?<=[.!?])\s+", body))<=5 else []
                    if 3<=n<=25 and len(re.split(r"(?<=[.!?])\s+", body))==1:
                        formats.append("L")
                    cut = data.get("split")
                    if isinstance(cut,int) and 0<cut<len(body) and (body[cut-1].isspace() or body[cut].isspace()):
                        formats.append("F4")
                    if not formats:
                        status = "format"
                        break
                    meta = json.loads(raw)
                    if not meta.get("upload_date") or meta.get("view_count") is None:
                        details = json.loads(ytdlp("--skip-download", "--dump-single-json",
                                                  "https://www.youtube.com/shorts/" + vid))
                        meta.update({k: details[k] for k in ("view_count", "upload_date", "channel_id", "uploader_id")
                                     if details.get(k) is not None})
                    creator = (meta.get("uploader_id") or meta.get("channel_id") or channel.split("/")[-2]).lstrip("@")
                    result = dict(id=hashlib.sha256(key.encode()).hexdigest()[:16], text=body, speaker=creator, creator=creator,
                                  source="YouTube Shorts", url="https://www.youtube.com/shorts/"+vid, views=meta.get("view_count"),
                                  surface=surface, upload_date=meta.get("upload_date"), scores=data["scores"], reader=path, formats=formats,
                                  split=cut, theme=str(data.get("theme") or "self-reflection"),
                                  mood=data.get("mood") if data.get("mood") in {"reflective","melancholic","calm resolve","defiant"} else "reflective",
                                  energy=data.get("energy") if type(data.get("energy")) in {int,float} and 0<=data["energy"]<=1 else .4, fit=4, likes=None,
                                  sites=None, state="candidate")
                    status = "accepted"
                    seen.add(key)
                break
        except (OSError, subprocess.SubprocessError) as error:
            status = "retry"
            print("Candidate deferred", vid, type(error).__name__, flush=True)
        with db:
            db.execute("UPDATE videos SET status=?,result=? WHERE id=?", (status,json.dumps(result),vid))
            if status in {"accepted","seed_reproduced"}:
                db.execute("UPDATE channels SET done=CASE WHEN priority<2 THEN 0 ELSE done END,priority=3,misses=0 WHERE url=?", (channel,))
            else:
                db.execute("UPDATE channels SET misses=misses+1 WHERE url=?", (channel,))
        export(db, bank, seed_handles)


if __name__ == "__main__":
    main()
