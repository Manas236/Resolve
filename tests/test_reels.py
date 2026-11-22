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
            self.assertEqual((audio["codec_name"], audio["sample_rate"]), ("aac", "48000"))
            self.assertEqual(picture["avg_frame_rate"], "30/1")
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

    def test_f3_burns_word_during_its_cue_and_ducks_music(self):
        from array import array
        from math import cos, pi, sin

        with TemporaryDirectory() as directory:
            root = Path(directory)
            video, song, speech, output = (root / name for name in (
                "video.mp4", "song.wav", "speech.wav", "reel.mp4",
            ))
            for source, target, extra in (
                ("color=blue:s=192x108:r=2:d=22", video, ["-c:v", "libx264"]),
                ("sine=frequency=330:duration=22", song, []),
                ("aevalsrc=if(gte(t\\,2)\\,0.6*sin(2*PI*600*t)\\,0):d=4", speech, []),
            ):
                subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi",
                                "-i", source, *extra, str(target)], check=True, timeout=20)
            render_reel(video, song, output, format_id="F3", hook="Start", voiceover=speech,
                        words=[Cue(2200, 2800, "Keep"), Cue(2900, 3700, "going.")])

            def yellow_pixels(at):
                rgb = subprocess.check_output(
                    ["ffmpeg", "-nostdin", "-v", "error", "-ss", str(at), "-i", str(output),
                     "-frames:v", "1", "-vf", "crop=440:180:320:1440",
                     "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], timeout=20,
                )
                return sum(r > 170 and g > 130 and b < 100 for r, g, b in
                           zip(rgb[0::3], rgb[1::3], rgb[2::3]))

            def music_level(at):
                raw = subprocess.check_output(
                    ["ffmpeg", "-nostdin", "-v", "error", "-ss", str(at), "-i", str(output),
                     "-t", "0.4", "-map", "0:a:0", "-ac", "1", "-ar", "12000",
                     "-f", "f32le", "pipe:1"], timeout=20,
                )
                samples = array("f", raw)
                phase = 2 * pi * 330 / 12000
                return abs(sum(value * complex(cos(phase * i), sin(phase * i))
                               for i, value in enumerate(samples))) / len(samples)

            self.assertGreater(yellow_pixels(2.5), 100)
            self.assertEqual(yellow_pixels(4.5), 0)
            self.assertLess(music_level(3), 0.75 * music_level(1))
