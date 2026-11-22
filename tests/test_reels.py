from pathlib import Path
from shutil import which
import subprocess
from tempfile import TemporaryDirectory
import unittest
import json

from resolve_pipeline.captions import Cue
from resolve_pipeline.reels import render_reel


@unittest.skipUnless(which("ffmpeg") and which("ffprobe"), "FFmpeg and ffprobe required")
class ReelTests(unittest.TestCase):
    def test_f2_real_output_and_incomplete_speech_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video, song, output = (root / name for name in ("video.mp4", "song.wav", "reel.mp4"))
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                 "color=blue:s=192x108:r=2:d=22", "-c:v", "libx264", str(video)],
                check=True,
            )
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                 "sine=frequency=330:duration=22", str(song)], check=True,
            )
            render_reel(video, song, output, format_id="F2", hook="Keep going", quote="Show up.")
            probe = json.loads(subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
            ))
            picture, audio = probe["streams"]
            self.assertEqual((picture["width"], picture["height"]), (1080, 1920))
            self.assertEqual(audio["codec_name"], "aac")
            self.assertAlmostEqual(float(picture["duration"]), 22, places=1)
            before = output.read_bytes()
            for words in ([Cue(0, 1000, "unfinished")], [Cue(21000, 23000, "crossing.")]):
                with self.assertRaisesRegex(ValueError, "complete sentence"):
                    render_reel(video, song, output, format_id="F1", hook="Finish", words=words)
                self.assertEqual(output.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, "do not cover"):
                render_reel(video, song, output, format_id="F2", hook="Finish",
                            quote="Show up.", video_start=2)
            self.assertEqual(output.read_bytes(), before)
