import shutil
import subprocess
import unittest

from resolve_pipeline.framing import VERTICAL_FRAME


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is unavailable")
class FramingTests(unittest.TestCase):
    def test_landscape_and_anamorphic_inputs(self):
        for source in ("testsrc2=s=320x180", "testsrc2=s=320x240,setsar=2/1"):
            with self.subTest(source=source):
                result = subprocess.run(
                    ["ffmpeg", "-hide_banner", "-loglevel", "info", "-filter_threads", "1",
                     "-f", "lavfi", "-i", source, "-vf", VERTICAL_FRAME,
                     "-frames:v", "1", "-f", "null", "-"],
                    check=True, capture_output=True, text=True, timeout=15,
                )
                output = result.stderr.split("Output #0", 1)[1]
                self.assertRegex(output, r"1080x1920 \[SAR 1:1 DAR 9:16\]")
