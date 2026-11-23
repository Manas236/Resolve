from contextlib import closing
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from resolve_pipeline.caption_style import line_boundaries, music_markers, styled_ass
from resolve_pipeline.control import Pipeline
from resolve_pipeline import dashboard_view
from resolve_pipeline.reel_text import BANK, checked_text, choose_text, gemini_text, normalized
from tools.pc_acquisition import check_deno


class ReelTextTests(unittest.TestCase):
    def test_bank_sustains_100_reels_without_repeat(self):
        bank = BANK.read_text(encoding="utf-8").splitlines()
        self.assertGreater(len(bank), 500)
        self.assertEqual(len(bank), len(set(map(normalized, bank))))
        for line in bank:
            checked_text(line, [], "F1")
        used = set(map(normalized, bank[:500]))
        text = choose_text("F2", used, {"hook": bank[0], "lines": bank[1:3]})
        self.assertEqual(text["source"], "bank")
        self.assertTrue(all(normalized(line) not in used for line in [text["hook"], *text["lines"]]))

    def test_gemini_and_quota_fallback(self):
        payload = {"hook": "Set your stance", "lines": ["Brace before moving", "Drive through midfoot"]}
        body = json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]})
        with patch("resolve_pipeline.reel_text.urlopen", return_value=io.BytesIO(body.encode())) as api:
            text = gemini_text("fake-key", "F2", "Squats", set())
            request = api.call_args.args[0]
            self.assertEqual(request.get_header("X-goog-api-key"), "fake-key")
            self.assertNotIn("fake-key", request.full_url)
        self.assertEqual(choose_text("F2", set(), text)["source"], "gemini")
        with patch("resolve_pipeline.reel_text.urlopen", side_effect=HTTPError("", 429, "", {}, None)):
            self.assertIsNone(gemini_text("key", "F2", "", set()))
        with self.assertRaises(ValueError):
            checked_text("#Train hard", [], "F1")

    def test_persists_generated_text_and_replaces_preview_for_edits(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            clip, song = root / "clip.mp4", root / "song.wav"
            clip.touch()
            song.touch()
            builder = Mock()
            builder.generate_captions, builder.gemini_key = True, ""
            builder.format_requirements.return_value = {"F1": "missing", "F2": None, "F3": "missing"}
            pipeline = Pipeline(root / "state", [clip], reel_builder=builder)
            pipeline.add_song(song)
            first, second = pipeline.make_now(2, "F2")
            texts = pipeline.video_texts()
            self.assertTrue(set([texts[first]["hook"], *texts[first]["lines"]]).isdisjoint(
                [texts[second]["hook"], *texts[second]["lines"]]))
            def form(action, fields, label):
                return f'<form action="/{action}">{fields}<button>{label}</button></form>'

            html = dashboard_view.review(pipeline, form)
            self.assertIn('action="/edit-video-text"', html)
            self.assertIn(texts[first]["hook"], html.replace("&#x27;", "'"))
            self.assertIn('name="caption_preset"', dashboard_view.settings(pipeline, form, "UTC"))
            third = pipeline.edit_video_text(first, "Set the bar", ["Hold your stance", "Finish the pull"])
            self.assertIsNone(pipeline.video_texts().get(first))
            self.assertFalse(pipeline.telegram_review("stale", first, "approve"))
            self.assertEqual(pipeline.video_texts()[third]["hook"], "Set the bar")
            self.assertEqual(builder.call_args.kwargs["video_text"]["source"], "edited")
            with closing(sqlite3.connect(pipeline.database)) as db:
                self.assertEqual(db.execute("SELECT status FROM reels WHERE id=?", (third,)).fetchone()[0], "review")

    def test_deno_preflight_rejects_old_binary(self):
        with patch("tools.pc_acquisition.subprocess.run", return_value=Mock(stdout="deno 2.2.9")):
            with self.assertRaisesRegex(ValueError, "Deno 2.3"):
                check_deno()
        with patch("tools.pc_acquisition.subprocess.run", return_value=Mock(stdout="deno 2.3.0")):
            check_deno()


class CaptionStyleTests(unittest.TestCase):
    def test_line_changes_follow_onsets_and_style_contains_pop_and_accent(self):
        markers = [(1550, 1), (8540, 2), (15000, 1)]
        self.assertEqual(line_boundaries(markers, 3, 22000), [1550, 8540, 15000, 22000])
        style = styled_ass("Quiet first rep", ["Keep your balance", "Stand up straight", "Reset between sets"],
                           (), "montserrat", 22000, markers)
        self.assertIn("0:00:08.54", style)
        self.assertIn("Resolve Montserrat Black", style)
        self.assertIn(r"\pos(540,1210)", style)
        self.assertIn(r"\t(0,85,", style)
        self.assertIn("BALANCE{", style)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_detects_markers_from_fixture_audio(self):
        with TemporaryDirectory() as folder:
            track = Path(folder) / "pulse.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            r"aevalsrc=0.7*sin(2*PI*70*t)*exp(-25*mod(t\,0.5)):s=22050:d=7",
                            str(track)], check=True)
            markers = music_markers(track, 1, 5)
            self.assertTrue(any(abs(when - 2000) < 150 for when, _ in markers))
            self.assertTrue(all(when >= 0 for when, _ in markers))
