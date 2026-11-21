from array import array
import unittest

from tools.m0_probe import replay_result, strongest_window, validate_media


class ProbeValidationTests(unittest.TestCase):
    def test_audio_duration_boundary(self):
        metadata = {
            "streams": [{"codec_type": "audio"}],
            "format": {"duration": "23", "format_name": "mp3"},
        }
        self.assertEqual(validate_media(metadata, "audio", 20)["duration_seconds"], 23)
        with self.assertRaisesRegex(ValueError, "wrong_duration"):
            validate_media(metadata, "audio", 19.9)

    def test_rejects_nonfinite_duration_and_nonmedia_streams(self):
        metadata = {
            "streams": [{"codec_type": "audio"}],
            "format": {"duration": "nan", "format_name": "mp3"},
        }
        with self.assertRaisesRegex(ValueError, "invalid_duration"):
            validate_media(metadata, "audio")
        metadata["format"]["duration"] = "20"
        metadata["streams"].append({"codec_type": "data"})
        with self.assertRaisesRegex(ValueError, "unexpected_stream"):
            validate_media(metadata, "audio")

    def test_portrait_video_threshold_and_cover_art(self):
        video = {"codec_type": "video", "width": 720, "height": 1280}
        metadata = {
            "streams": [video],
            "format": {"duration": "20", "format_name": "mov,mp4"},
        }
        self.assertTrue(validate_media(metadata, "video")["meets_720p"])
        video["width"] = 719
        self.assertFalse(validate_media(metadata, "video")["meets_720p"])
        video["disposition"] = {"attached_pic": 1}
        with self.assertRaisesRegex(ValueError, "expected_video"):
            validate_media(metadata, "video")

    def test_energy_window_and_silence(self):
        samples = array("h", [0, 0, 7, -7, 1, 2])
        self.assertEqual(strongest_window(samples, 2, seconds=1)["start_seconds"], 1)
        self.assertIsNone(strongest_window(array("h", [0, 0]), 2, seconds=1))
        self.assertIsNone(strongest_window(array("h", [7]), 2, seconds=1))

    def test_replay_peak_absence_and_challenge(self):
        page = (
            '"heatMarkerRenderer":{"timeRangeStartMillis":4000,'
            '"heatMarkerIntensityScoreNormalized":0.9},'
            '"heatMarkerRenderer":{"timeRangeStartMillis":8000,'
            '"heatMarkerIntensityScoreNormalized":0.3}'
        )
        self.assertEqual(replay_result(page)["start_seconds"], 4)
        self.assertEqual(replay_result("")["status"], "no_heatmap_in_html")
        self.assertEqual(replay_result(page + " Verify you are human")["status"], "challenge_detected")

    def test_corpus_and_matrix_preserve_denominator(self):
        from tools.m0_probe import provider_summary, read_corpus

        corpus = read_corpus()
        self.assertEqual(len(corpus), 20)
        results = [
            {"kind": "audio", "converters": {"source": {"status": "verified"}}},
            {"kind": "audio", "converters": {"source": {"status": "skipped_provider_block"}}},
        ]
        summary = provider_summary(["source"], results)["source"]["audio"]
        self.assertEqual(summary, dict(target=10, observed=2, verified=1, attempted=1, corpus_success_rate=0.1))

    def test_timestamp_requires_matching_source_and_nearby_peak(self):
        from tools.m0_probe import check_offset, chosen_timestamp, social_offset

        watch = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
        self.assertIsNone(social_offset([watch + "&t=9s"], "wrong"))
        self.assertEqual(social_offset([watch + "&t=9s"], "aqz-KE-bpKQ"), 9)
        control = {"duration_seconds": 50, "energy": {
            "status": "energy_peak", "start_seconds": 9, "strong_local_peaks_seconds": [9],
        }}
        self.assertEqual(check_offset(40, control)["status"], "rejected_no_nearby_audio_peak")
        self.assertEqual(check_offset(float("inf"), control)["status"], "invalid_or_unverified_offset")
        empty = {"status": "no_data"}
        selected = chosen_timestamp(empty, empty, check_offset(10, control), empty, control)
        self.assertEqual(selected, {"method": "social", "start_seconds": 10})

    def test_provider_origins_are_restricted(self):
        from tools.m0_probe import provider_url

        self.assertTrue(provider_url("https://s42.notube.net/download", "notube"))
        self.assertFalse(provider_url("https://s42.notube.net.invalid/download", "notube"))
        self.assertFalse(provider_url("https://mp7.dev@169.254.169.254/file", "mp7"))
        self.assertFalse(provider_url("file:///tmp/file", "mp7"))

    def test_timestamp_reference_can_use_verified_provider(self):
        from tools.m0_probe import timestamp_reference

        failed = {"status": "rejected"}
        media = {"status": "verified", "duration_seconds": 40}
        self.assertEqual(timestamp_reference(failed, {"provider": media}), ("provider", media))
        self.assertEqual(timestamp_reference(media, {"provider": media}), ("publisher", media))
        self.assertEqual(timestamp_reference(failed, {"provider": failed}), (None, {}))

    def test_target_and_baseline_separation_and_provider_coverage(self):
        from tools.m0_probe import DIRECT_MP3, PROVIDERS, matrix_report, read_corpus
        from argparse import Namespace

        targets, baseline = read_corpus(), read_corpus(baseline=True)
        self.assertEqual(len({item["youtube_id"] for item in targets + baseline}), 40)
        self.assertGreaterEqual(len(set(PROVIDERS) - DIRECT_MP3), 8)
        self.assertTrue(all(item["watch_url"].endswith(item["youtube_id"]) for item in targets))
        row = {"corpus": "baseline", "kind": "audio", "publisher_control": {"status": "verified"},
               "timestamps": {"selected": None}, "converters": {"mp7": {"status": "verified"}}}
        report = matrix_report(Namespace(limit=None, no_browser=False), [row], {"mp7": {}},
                               "ready", None, True, {})
        self.assertEqual(report["provider_summary"]["mp7"]["audio"]["verified"], 0)
        self.assertEqual(report["baseline_summary"]["audio"]["publisher_verified"], 1)

    def test_old_python_message_precedes_imports(self):
        import ast
        import runpy
        from pathlib import Path
        import sys
        from unittest.mock import patch

        script = Path(__file__).resolve().parents[1] / "tools/m0_probe.py"
        ast.parse(script.read_text(encoding="utf-8"), feature_version=(3, 6))
        with patch.object(sys, "version_info", (3, 10)):
            with self.assertRaisesRegex(SystemExit, "requires Python 3.11 or newer"):
                runpy.run_path(str(script), run_name="__main__")
