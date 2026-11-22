from pathlib import Path
from shutil import which
import subprocess
from tempfile import TemporaryDirectory
import unittest
import wave

from resolve_pipeline.tts import synthesize


class SpeechTests(unittest.TestCase):
    def test_invalid_input_leaves_existing_sample_intact(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "sample.wav"
            output.write_bytes(b"existing sample")
            for script, engine in [(" ", "flite"), ("x\x00y", "flite"), ("test", "missing")]:
                with self.assertRaises(ValueError):
                    synthesize(script, output, engine=engine)
                self.assertEqual(output.read_bytes(), b"existing sample")

    @unittest.skipUnless(which("ffmpeg"), "FFmpeg required")
    def test_flite_reads_literal_text_and_writes_audio(self):
        filters = subprocess.check_output(["ffmpeg", "-hide_banner", "-filters"], text=True)
        if " flite " not in filters:
            self.skipTest("FFmpeg needs Flite support")
        with TemporaryDirectory() as directory:
            output = Path(directory) / "speech, example.wav"
            properties = synthesize("Don't stop. Here's why: keep moving.", output, engine="flite")
            with wave.open(str(output), "rb") as sample:
                self.assertGreater(properties["audio_seconds"], 1)
                self.assertEqual(properties["sample_rate"], sample.getframerate())
