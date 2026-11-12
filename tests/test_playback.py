import json
from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from gukawa.config import Settings
from gukawa.db import Database
from gukawa.media import MediaError, run_media
from gukawa.pipeline import sha256
from gukawa.playback import prepare_playback, video_stream
from gukawa.web import create_app


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    @unittest.skipUnless(shutil.which("ffprobe"), "FFmpeg integration runs in Docker")
    def test_crop_keeps_center_audio_timing_and_original(self):
        source = self.directory / "original.mp4"
        run_media(["ffmpeg", "-nostdin", "-y", "-v", "error", "-filter_threads", "2",
                   "-f", "lavfi", "-i", "color=c=red:s=720x1280:r=12:d=2",
                   "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                   "-vf", "drawbox=x=0:y=440:w=720:h=400:color=white:t=fill,drawbox=x=0:y=840:w=720:h=440:color=blue:t=fill",
                   "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)])
        original_hash = sha256(source)
        info = prepare_playback(source, self.directory, 600)
        target = self.directory / "playback.mp4"
        self.assertEqual((info["width"], info["height"]), (720, 388))
        self.assertAlmostEqual(info["duration"], 2, delta=.15)
        self.assertEqual(sha256(source), original_hash)
        rgb = run_media(["ffmpeg", "-nostdin", "-v", "error", "-i", str(target), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]).stdout
        self.assertEqual(len(rgb), 720 * 388 * 3)
        self.assertGreater(min(rgb), 240)  # White center only; red/blue outer panels are absent.
        def audio_hash(path):
            return run_media(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0", "-c:a", "copy", "-f", "hash", "-hash", "sha256", "-"]).stdout
        self.assertEqual(audio_hash(source), audio_hash(target))
        self.assertTrue((self.directory / "playback.jpg").is_file())
        before = target.stat().st_mtime_ns
        self.assertEqual(prepare_playback(source, self.directory, 600), info)
        self.assertEqual(target.stat().st_mtime_ns, before)

    @unittest.skipUnless(shutil.which("ffprobe"), "FFmpeg integration runs in Docker")
    def test_unverified_layout_is_not_cropped(self):
        source = self.directory / "original.mp4"
        run_media(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=640x360:d=1",
                   "-c:v", "libx264", "-threads", "2", str(source)])
        before = sha256(source)
        with self.assertRaises(MediaError):
            prepare_playback(source, self.directory, 600)
        self.assertEqual(sha256(source), before)
        self.assertFalse((self.directory / "playback.mp4").exists())

    @unittest.skipUnless(shutil.which("ffprobe"), "FFmpeg integration runs in Docker")
    def test_1080_source_is_cropped_at_native_resolution(self):
        source = self.directory / "original.mp4"
        run_media(["ffmpeg", "-nostdin", "-y", "-v", "error", "-filter_threads", "2",
                   "-f", "lavfi", "-i", "color=c=red:s=1080x1920:r=12:d=1",
                   "-vf", "drawbox=x=0:y=660:w=1080:h=600:color=white:t=fill",
                   "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", str(source)])
        info = prepare_playback(source, self.directory, 600)
        self.assertEqual((info["width"], info["height"]), (1080, 582))
        self.assertFalse(info["upscaled"])
        rgb = run_media(["ffmpeg", "-nostdin", "-v", "error", "-i", str(self.directory / "playback.mp4"),
                         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]).stdout
        self.assertEqual(len(rgb), 1080 * 582 * 3)
        self.assertGreater(min(rgb), 240)

    def test_player_prefers_viewing_copy_and_can_open_original(self):
        settings = Settings(self.directory, admin_password="test-password", auto_sync=False)
        database = Database(settings.database)
        database.enqueue("example", "", "Episode 360", episode_number=360)
        directory = self.directory / "episodes" / "example"
        directory.mkdir()
        (directory / "metadata.json").write_text(json.dumps({"video_file": "original.mp4"}), encoding="utf-8")
        (directory / "original.mp4").write_bytes(b"original-bytes")
        (directory / "playback.mp4").write_bytes(b"cropped-bytes")
        (directory / "playback.json").write_text("{}", encoding="utf-8")
        (directory / "playback.jpg").write_bytes(b"poster")
        with TestClient(create_app(settings)) as client:
            self.assertEqual(client.get("/files/example/playback.mp4").status_code, 401)
            client.auth = ("admin", settings.admin_password)
            self.assertIn('src="/files/example/playback.mp4"', client.get("/episodes/example").text)
            self.assertIn('src="/files/example/original.mp4"', client.get("/episodes/example?view=original").text)
            response = client.get("/files/example/playback.mp4", headers={"Range": "bytes=0-6"})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.content, b"cropped")
            (directory / "playback.mp4").unlink()
            self.assertIn('src="/files/example/original.mp4"', client.get("/episodes/example").text)


if __name__ == "__main__":
    unittest.main()
