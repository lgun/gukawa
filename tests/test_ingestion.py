from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from gukawa.cli import verify
from gukawa.config import Settings
from gukawa.db import Database
from gukawa.pipeline import process_episode, reconcile, sha256
from gukawa.source import InstagramSource, SourceError, episode_number, parse_instagram_url
from gukawa.subtitles import Cue, merge_observations, order_ocr_lines, read_cues, read_subtitles, validate_cues, write_tracks
from gukawa.web import create_app
from gukawa.worker import sync


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(Path(self.temp.name), auto_sync=False, admin_password="test-password", max_attempts=2, create_playback=False)
        self.db = Database(self.settings.database)

    def seed(self, episode_id="example", subtitles=True):
        directory = self.settings.data_dir / "episodes" / episode_id
        directory.mkdir()
        (directory / "input.mp4").write_bytes(b"sample-video-bytes")
        if subtitles:
            (directory / "input.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nLet's go home!\n\n2\n00:00:01,500 --> 00:00:02,500\nOkay!\n", encoding="utf-8")
        self.db.enqueue(episode_id, "", "Episode 382", episode_number=382)
        return directory

    def process_seed(self):
        directory = self.seed()
        with patch("gukawa.pipeline.probe_duration", return_value=3.0), patch("gukawa.pipeline.translate") as translator:
            process_episode(self.db.claim(), self.db, self.settings)
            translator.assert_not_called()
        return directory

    def test_source_only_saves_original_text_timing_and_checksum_without_key(self):
        directory = self.process_seed()
        self.assertEqual(self.db.get("example")["status"], "source_ready")
        self.assertEqual((directory / "original.mp4").read_bytes(), b"sample-video-bytes")
        self.assertEqual((directory / "source.txt").read_text(encoding="utf-8"), "Let's go home!\nOkay!\n")
        self.assertEqual(len(read_cues(directory / "source.json")), 2)
        self.assertEqual(len(read_subtitles(directory / "en.srt")), 2)
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["video_sha256"], sha256(directory / "original.mp4"))
        self.assertEqual(metadata["extraction"]["method"], "provided_subtitles")
        self.assertEqual(verify(self.db, self.settings, checksum=True)["issues"], [])

    def test_no_duplicate_download_or_paid_call_on_retry(self):
        directory = self.process_seed()
        self.db.transition("example", ("source_ready",), "queued", reset=True)
        with patch("gukawa.pipeline.probe_duration", return_value=3), patch("gukawa.pipeline.InstagramSource") as source:
            process_episode(self.db.claim(), self.db, self.settings)
            source.assert_not_called()
        self.assertEqual(len(list(directory.glob("original.*"))), 1)

    def test_automatic_translation_saves_api_version_and_reuses_it_on_retry(self):
        directory = self.seed()
        translated = [Cue(0, 1, '집에 가자!'), Cue(1.5, 2.5, '응!')]
        settings = replace(self.settings, processing_mode='translate')
        with patch('gukawa.pipeline.probe_duration', return_value=3), patch('gukawa.pipeline.translate', return_value=('382화', translated)) as translator:
            process_episode(self.db.claim(), self.db, settings)
            self.assertEqual(self.db.get('example')['status'], 'ready')
            manifest = json.loads((directory / 'translation.api.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['method'], 'openai_api')
            self.assertEqual(manifest['model'], settings.translation_model)
            self.assertEqual(read_cues(directory / 'ko.api.json'), translated)
            self.db.transition('example', ('ready',), 'queued', reset=True)
            process_episode(self.db.claim(), self.db, settings)
            self.assertEqual(translator.call_count, 1)

    def test_metadata_checkpoint_is_recovered_after_video_rename(self):
        directory = self.seed()
        (directory / "input.mp4").replace(directory / "original.mp4")
        with patch("gukawa.pipeline.probe_duration", return_value=3):
            process_episode(self.db.claim(), self.db, self.settings)
        self.assertEqual(verify(self.db, self.settings, checksum=True)["issues"], [])

    def test_no_cues_is_failure_but_video_is_retained(self):
        directory = self.seed(subtitles=False)
        with patch("gukawa.pipeline.probe_duration", return_value=3), patch("gukawa.pipeline.extract_english", side_effect=ValueError("no text")):
            process_episode(self.db.claim(), self.db, self.settings)
        self.assertTrue((directory / "original.mp4").is_file())
        self.assertEqual(self.db.get("example")["status"], "queued")
        self.assertFalse((directory / "source.txt").exists())

    def test_max_attempts_marks_failed(self):
        self.seed(subtitles=False)
        with patch("gukawa.pipeline.probe_duration", side_effect=ValueError("bad video")):
            process_episode(self.db.claim(), self.db, self.settings)
            self.db.update("example", available_at=0)
            process_episode(self.db.claim(), self.db, self.settings)
        self.assertEqual(self.db.get("example")["status"], "failed")
        self.assertEqual(self.db.get("example")["attempts"], 2)

    def test_deleted_file_requeues_episode(self):
        directory = self.process_seed()
        (directory / "source.txt").unlink()
        self.assertEqual(reconcile(self.db, self.settings), 1)
        self.assertEqual(self.db.get("example")["status"], "queued")

    def test_checksum_detects_corrupted_video(self):
        directory = self.process_seed()
        (directory / "original.mp4").write_bytes(b"changed-video-data!")
        self.assertTrue(verify(self.db, self.settings, checksum=True)["issues"])

    def test_dedup_and_worker_recovery_preserve_ready_work(self):
        self.db.enqueue("one", "", "one", caption="original caption")
        self.assertFalse(self.db.enqueue("one", "", "changed"))
        self.db.claim()
        self.db.recover()
        self.assertEqual(self.db.get("one")["status"], "queued")
        self.assertEqual(self.db.get("one")["caption"], "original caption")

    def test_backfill_enqueues_all_and_remembers_only_complete_scan(self):
        posts = [dict(episode_id=f"post{i}", source_url="", title=f"Episode {360 + i}", episode_number=360 + i) for i in range(30)]
        with patch("gukawa.worker.InstagramSource") as source:
            source.return_value.discover.return_value = iter(posts)
            self.assertEqual(sync(self.db, self.settings, backfill=True), 30)
            source.return_value.discover.assert_called_once_with(backfill=True, known_ids=set())
            self.assertTrue(self.db.get_state("backfill_complete"))
            source.return_value.discover.return_value = iter(posts)
            self.assertEqual(sync(self.db, self.settings, backfill=True), 0)

    def test_interrupted_backfill_can_repeat_without_duplicates(self):
        def incomplete():
            yield dict(episode_id="first", source_url="", title="first", episode_number=360)
            raise SourceError("rate limited")
        with patch("gukawa.worker.InstagramSource") as source:
            source.return_value.discover.return_value = incomplete()
            self.assertIsNone(sync(self.db, self.settings, backfill=True))
        self.assertFalse(self.db.get_state("backfill_complete"))
        self.assertEqual(self.db.counts(), {"queued": 1})

    def test_backfill_includes_reels_missing_from_grid_and_deduplicates(self):
        from types import SimpleNamespace
        from datetime import datetime
        def post(name):
            return SimpleNamespace(shortcode=name, is_video=True, owner_username="chiikawa.anime", caption="Episode 382", date_utc=datetime(2026, 1, 1))
        source = InstagramSource(self.settings)
        with patch.object(source, "loader"), patch("gukawa.source.instaloader.Profile.from_username") as profile:
            profile.return_value.get_posts.return_value = iter([post("grid")])
            profile.return_value.get_reels.return_value = iter([post("grid"), post("reels_only")])
            found = list(source.discover(backfill=True))
        self.assertEqual([item["episode_id"] for item in found], ["grid", "reels_only"])

    def test_latest_scan_catches_up_beyond_window_after_downtime(self):
        from types import SimpleNamespace
        from datetime import datetime
        posts = [SimpleNamespace(shortcode=f"post{i}", is_video=True, owner_username="chiikawa.anime", caption="Episode 382", date_utc=datetime(2026, 1, 1)) for i in range(25)]
        source = InstagramSource(self.settings)
        with patch.object(source, "loader"), patch("gukawa.source.instaloader.Profile.from_username") as profile:
            profile.return_value.get_posts.return_value = iter(posts)
            profile.return_value.get_reels.return_value = iter([])
            found = list(source.discover(known_ids={"post20"}))
        self.assertEqual(len(found), 21)

    def test_ocr_does_not_merge_repeated_dialogue_across_silence(self):
        cues = merge_observations([(0, "Hello", .9), (.25, "Hello", .9), (.5, "", 0), (.75, "Hello", .9), (1, "Hello", .9)], .25, 1.5)
        self.assertEqual([(cue.start, cue.end) for cue in cues], [(0, .5), (.75, 1.25)])

    def test_discovery_only_accepts_360_and_later_even_after_old_pinned_posts(self):
        from datetime import datetime
        from types import SimpleNamespace
        captions = ["第三五九話", "第三六〇話", "第３６１話", "Special announcement"]
        for backfill in (False, True):
            with self.subTest(backfill=backfill):
                posts = [SimpleNamespace(shortcode=f"post{i}", is_video=True, owner_username="chiikawa.anime", caption=caption, date_utc=datetime(2026, 1, 1)) for i, caption in enumerate(captions)]
                source = InstagramSource(self.settings)
                with patch.object(source, "loader"), patch("gukawa.source.instaloader.Profile.from_username") as profile:
                    profile.return_value.get_posts.return_value = iter(posts)
                    profile.return_value.get_reels.return_value = iter([])
                    found = list(source.discover(backfill=backfill))
                self.assertEqual([item["episode_number"] for item in found], [360, 361])

    def test_backfill_queue_rejects_old_and_unnumbered_posts(self):
        posts = [dict(episode_id=f"range{i}", source_url="", title="test", episode_number=number) for i, number in enumerate((359, 360, 361, None))]
        with patch("gukawa.worker.InstagramSource") as source:
            source.return_value.discover.return_value = iter(posts)
            self.assertEqual(sync(self.db, self.settings, backfill=True), 2)
        self.assertEqual({item["episode_number"] for item in self.db.list(public=False)}, {360, 361})

    def test_previously_queued_old_episode_never_downloads_or_retries(self):
        self.db.enqueue("old", "https://www.instagram.com/p/old/", "Episode 359", episode_number=359)
        with patch("gukawa.pipeline.InstagramSource") as source, patch("gukawa.pipeline.probe_duration") as probe:
            process_episode(self.db.claim(), self.db, self.settings)
            source.assert_not_called()
            probe.assert_not_called()
        self.assertEqual(self.db.get("old")["status"], "skipped")
        self.assertIsNone(self.db.claim())

    def test_manually_added_post_is_checked_before_download(self):
        for index, number in enumerate((359, None, 360)):
            with self.subTest(number=number):
                post_id = f"manual{index}"
                self.db.enqueue(post_id, f"https://www.instagram.com/p/{post_id}/", "pending")
                with patch("gukawa.pipeline.InstagramSource") as source:
                    source.return_value.inspect.return_value = {"episode_id": post_id, "source_url": "", "title": "test", "episode_number": number}
                    # Stop after the accepted post reaches the downloader; no network used.
                    source.return_value.download.side_effect = SourceError("test download stopped")
                    process_episode(self.db.claim(), self.db, self.settings)
                    self.assertEqual(source.return_value.download.call_count, 1 if number == 360 else 0)
                if number != 360:
                    self.assertEqual(self.db.get(post_id)["status"], "skipped")

    def test_cached_old_episode_is_excluded_before_reprocessing(self):
        directory = self.seed()
        (directory / "input.mp4").replace(directory / "original.mp4")
        # Recreate a record queued before the collection boundary was introduced.
        with self.db.connect() as connection:
            connection.execute("UPDATE episodes SET source_url=?, episode_number=359 WHERE id='example'", ("https://www.instagram.com/p/example/",))
        with patch("gukawa.pipeline.InstagramSource") as source, patch("gukawa.pipeline.probe_duration") as probe:
            process_episode(self.db.claim(), self.db, self.settings)
            source.assert_not_called()
            probe.assert_not_called()
        self.assertEqual(self.db.get("example")["status"], "skipped")

    def test_ocr_rejects_one_frame_noise(self):
        cues = merge_observations([(0, "Noise", .8), (.25, "", 0), (.5, "Hello", .95), (.75, "Hello", .95)], .25, 1)
        self.assertEqual([cue.text for cue in cues], ["Hello"])

    def test_ocr_reads_words_in_same_row_from_left_to_right(self):
        lines = [(21, 200, "let's eat", .95, 25), (25, 100, "Hey,", .94, 25), (70, 100, "second line", .95, 25)]
        self.assertEqual([line[2] for line in order_ocr_lines(lines)], ["Hey,", "let's eat", "second line"])

    def test_invalid_timing_and_html_safety(self):
        with self.assertRaises(ValueError):
            validate_cues([Cue(0, 2, "first"), Cue(1, 3, "second")])
        write_tracks(self.settings.data_dir, [Cue(0, 1, "<script>alert(1)</script> -->")], "en")
        content = (self.settings.data_dir / "en.vtt").read_text(encoding="utf-8")
        self.assertNotIn("<script>", content)
        self.assertIn("&lt;script&gt;", content)

    def test_instagram_url_rejects_other_hosts_and_credentials(self):
        self.assertEqual(parse_instagram_url("https://www.instagram.com/reel/ABC-123/?igsh=123"), "ABC-123")
        for url in ("http://www.instagram.com/p/a/", "https://instagram.com.evil.example/p/a/", "https://admin@instagram.com/p/a/", "https://instagram.com:444/p/a/", "https://instagram.com/p/../../secrets/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                parse_instagram_url(url)

    def test_episode_numbers_include_japanese_digit_titles(self):
        for title, expected in [("Episode 382", 382), ("第３８２話", 382), ("ちいかわー第三八二話", 382), ("第三百八十二話", 382), ("A new story", None)]:
            self.assertEqual(episode_number(title), expected)

    def test_admin_auth_and_cross_site_mutation_protection(self):
        with TestClient(create_app(self.settings)) as client:
            self.assertEqual(client.get("/healthz").status_code, 200)
            self.assertEqual(client.get("/", follow_redirects=False).headers["location"], "/login?next=%2F")
            self.assertEqual(client.get("/api/admin/status").status_code, 401)
            client.auth = ("admin", self.settings.admin_password)
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.post("/api/admin/scan", json={"mode": "backfill"}).status_code, 403)
            self.assertEqual(client.post("/api/admin/scan", headers={"X-Gukawa-Action": "admin"}, json={"mode": "backfill"}).status_code, 202)
            self.assertEqual(self.db.get_state("sync_requested"), "backfill")

    def test_file_access_and_http_range(self):
        self.process_seed()
        with TestClient(create_app(self.settings)) as client:
            self.assertEqual(client.get("/files/example/source.txt").status_code, 401)
            client.auth = ("admin", self.settings.admin_password)
            self.assertEqual(client.get("/episodes/example").status_code, 200)
            self.assertEqual(client.get("/files/example/metadata.json").status_code, 200)
            response = client.get("/files/example/original.mp4", headers={"Range": "bytes=0-5"})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.content, b"sample")
            self.assertEqual(client.get("/files/example/gukawa.sqlite3").status_code, 404)

    @unittest.skipUnless(shutil.which("ffprobe"), "FFmpeg required; run this integration test in Docker")
    def test_real_video_is_probed_and_saved_without_reencoding(self):
        from gukawa.media import run_media
        directory = self.seed()
        video = directory / "input.mp4"
        run_media(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=640x360:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)])
        original_hash = sha256(video)
        process_episode(self.db.claim(), self.db, self.settings)
        self.assertEqual(self.db.get("example")["status"], "source_ready")
        self.assertEqual(sha256(directory / "original.mp4"), original_hash)

    @unittest.skipUnless(os.getenv("RUN_OCR_INTEGRATION") == "1" and shutil.which("ffprobe"), "Opt-in OCR model download and actual recognition")
    def test_real_ocr_from_video(self):
        from gukawa.media import run_media
        directory = self.seed(subtitles=False)
        video = directory / "input.mp4"
        run_media(["ffmpeg", "-nostdin", "-y", "-v", "error", "-filter_threads", "2", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=12:d=2",
                   "-vf", "drawtext=text='Let us go home!':fontcolor=white:fontsize=32:x=100:y=275",
                   "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", str(video)])
        process_episode(self.db.claim(), self.db, self.settings)
        self.assertEqual(self.db.get("example")["status"], "source_ready", self.db.get("example")["error"])
        text = (directory / "source.txt").read_text(encoding="utf-8").lower()
        self.assertIn("go home", text)
        self.assertTrue((directory / "ocr-observations.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
