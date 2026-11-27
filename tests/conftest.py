import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def test_video(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("media") / "test.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)],
        check=True,
    )
    return out
