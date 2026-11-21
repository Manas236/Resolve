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
