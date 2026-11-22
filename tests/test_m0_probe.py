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

class NestedReplayMarkerTests(unittest.TestCase):
    def test_replay_reads_nested_marker_and_picks_peak(self):
        from tools.m0_probe import replay_result

        data = (
            '{"heatMarkerRenderer":{"timeRangeStartMillis":"2000",'
            '"metadata":{"nested":true},"heatMarkerIntensityScoreNormalized":0.4}},'
            '{"heatMarkerRenderer":{"timeRangeStartMillis":5000,'
            '"heatMarkerIntensityScoreNormalized":0.9}}'
        )
        result = replay_result(data)
        self.assertEqual(result["status"], "peak")
        self.assertEqual(result["start_seconds"], 5)
        self.assertEqual(result["marker_count"], 2)


class ServerJobSelectionTests(unittest.TestCase):
    def test_runner_validates_provider_subset_and_rejects_unknown_options(self):
        import json
        from pathlib import Path
        import subprocess
        import sys
        from tempfile import TemporaryDirectory

        root = Path(__file__).resolve().parents[1]
        runner = (root / "tools/server_runner.sh").read_text()
        checker = runner.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        job = {"id": "fixture", "limit": 2, "no_browser": False,
               "providers": ["mp7", "cnvmp3", "ythd"]}
        with TemporaryDirectory() as directory:
            path = Path(directory) / "job.json"
            for providers in (["mp7", "cnvmp3", "ythd"], ["--output"], ["mp7", "mp7"], [None]):
                with self.subTest(providers=providers):
                    path.write_text(json.dumps(job | {"providers": providers}))
                    checked = subprocess.run([sys.executable, "-", str(path)], input=checker,
                                             capture_output=True, text=True, check=False)
                    self.assertEqual(checked.returncode == 0, providers == job["providers"])
                    if not checked.returncode:
                        self.assertEqual(checked.stdout.split()[1:], ["2", "0", *providers])

    def test_preflight_failures_keep_specific_report_reasons(self):
        from tools.m0_probe import browser_failure_reason

        self.assertEqual(browser_failure_reason(RuntimeError("socket() failed")), "browser_socket_denied")
        self.assertEqual(browser_failure_reason(RuntimeError("No usable sandbox")), "sandbox_unavailable")
        self.assertEqual(browser_failure_reason(RuntimeError("Executable doesn't exist")), "browser_missing")


class ProviderJobTests(unittest.TestCase):
    def test_mp7_accepts_integer_job_id_and_recovers_from_transient_poll(self):
        from pathlib import Path
        from urllib.error import HTTPError
        from unittest.mock import patch

        from tools.m0_probe import mp7_convert

        job = {"success": True, "status": "queued", "job_id": 42}
        complete = {"success": True, "status": "complete", "download_url": "/download/42.mp3"}
        transient = HTTPError("https://mp7.dev/status/42", 503, "Temporary", {}, None)
        with patch("tools.m0_probe.read_json", side_effect=[job, transient, complete]) as read, \
             patch("tools.m0_probe.time.sleep"), \
             patch("tools.m0_probe.run_sample", return_value={"status": "verified"}):
            result = mp7_convert({"id": "example", "kind": "audio", "reference_seconds": 9},
                                 "https://www.youtube.com/watch?v=gCESZzZ4Z38", Path("."))
        self.assertEqual(result["status"], "verified")
        self.assertEqual([call.args[0] for call in read.call_args_list[1:]],
                         ["https://mp7.dev/status/42", "https://mp7.dev/status/42"])

    def test_ythd_allows_the_public_widget_but_not_impostors(self):
        from tools.m0_probe import provider_url

        self.assertTrue(provider_url("https://clickapi.net/api/widgetplus?url=example", "ythd"))
        self.assertFalse(provider_url("https://clickapi.net.example.com/api/widgetplus", "ythd"))
        self.assertFalse(provider_url("https://challenges.cloudflare.com/widget", "ythd"))


class CurrentReplayMarkerTests(unittest.TestCase):
    def test_current_watch_payload_filters_video_and_marker_type(self):
        import json

        def entity(video_id, marker_type, score, start):
            return {"payload": {"macroMarkersListEntity": {
                "externalVideoId": video_id,
                "markersList": {
                    "markerType": marker_type,
                    "markers": [{"startMillis": str(start), "intensityScoreNormalized": score}],
                },
            }}}

        page = json.dumps({"frameworkUpdates": {"entityBatchUpdate": {"mutations": [
            entity("requested", "MARKER_TYPE_HEATMAP", 0.9, 42000),
            entity("other", "MARKER_TYPE_HEATMAP", 1, 3000),
            entity("requested", "MARKER_TYPE_CHAPTERS", 1, 1000),
        ]}}})
        result = replay_result(page, video_id="requested")
        self.assertEqual((result["start_seconds"], result["marker_count"], result["marker_schema"]),
                         (42, 1, "macro_markers"))
        self.assertEqual(replay_result(page, video_id="unknown")["status"], "no_heatmap_in_html")
        self.assertEqual(replay_result(page + " verify you are human")["status"], "challenge_detected")
