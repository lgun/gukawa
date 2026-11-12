import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from gukawa.config import Settings
from gukawa.db import Database
from gukawa.subtitles import Cue, write_tracks
from gukawa.subtitles import read_subtitles
from gukawa.web import caption_region, create_app


class CaptionTests(unittest.TestCase):
    def test_mask_region_follows_original_and_both_cropped_resolutions(self):
        metadata = {'extraction': {'method': 'ocr', 'crop_top': .57, 'crop_bottom': .66}}
        self.assertEqual(caption_region(metadata, False), (.57, .66, True))
        for width, y, height in ((720, 452, 388), (1080, 678, 582)):
            with self.subTest(width=width):
                metadata['playback'] = {'crop': {'x': 0, 'y': y, 'width': width, 'height': height}}
                top, bottom, burned = caption_region(metadata, True)
                self.assertAlmostEqual(top, (.57 * 1280 - 452) / 388)
                self.assertEqual(bottom, 1)
                self.assertTrue(burned)

    def test_separate_source_track_does_not_default_to_hiding_the_picture(self):
        self.assertEqual(caption_region({'extraction': {'method': 'embedded_subtitles'}}, True), (.74, 1, False))

    def test_broad_ocr_search_does_not_expand_the_subtitle_mask(self):
        metadata = {'extraction': {'method': 'ocr', 'crop_top': .2, 'crop_bottom': 1}}
        self.assertEqual(caption_region(metadata, False), (.57, .66, True))
        metadata['subtitle_region'] = {'top': .5, 'bottom': .7}
        self.assertEqual(caption_region(metadata, False), (.5, .7, True))

    def test_exported_korean_vtt_has_background_and_preserves_timing(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            cues = [Cue(1.25, 3.5, '한국어 자막\n두 번째 줄')]
            write_tracks(directory, cues, 'ko')
            content = (directory / 'ko.vtt').read_text(encoding='utf-8')
            self.assertIn('background-color: rgb(17, 17, 17)', content)
            self.assertIn('line:85%', content)
            self.assertEqual(read_subtitles(directory / 'ko.vtt'), cues)
            self.assertEqual(read_subtitles(directory / 'ko.srt'), cues)

    def test_korean_track_is_separate_optional_and_protected(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(Path(temporary), admin_password='test-password', auto_sync=False)
            database = Database(settings.database)
            database.enqueue('example', '', 'Episode 382', episode_number=382)
            directory = settings.data_dir / 'episodes' / 'example'
            directory.mkdir()
            (directory / 'metadata.json').write_text(json.dumps({'video_file': 'original.mp4', 'extraction': {'method': 'ocr', 'crop_top': .57, 'crop_bottom': .66}}), encoding='utf-8')
            (directory / 'original.mp4').write_bytes(b'original video bytes')
            with TestClient(create_app(settings)) as client:
                client.auth = ('admin', settings.admin_password)
                without = client.get('/episodes/example').text
                self.assertIn('한국어 자막이 아직 없어요.', without)
                self.assertNotIn('data-korean-track', without)
                before = (directory / 'original.mp4').read_bytes()
                write_tracks(directory, [Cue(1, 3, '한국어 표시 테스트')], 'ko')
                with_track = client.get('/episodes/example').text
                self.assertIn('kind="metadata" label="한국어"', with_track)
                self.assertIn('data-korean-track', with_track)
                self.assertNotIn('data-mask-enabled', with_track)
                self.assertNotIn('data-mask-size', with_track)
                self.assertIn('data-korean-enabled', with_track)
                self.assertNotIn('data-translation-select', with_track)
                self.assertEqual(client.get('/files/example/ko.vtt').status_code, 200)
                self.assertEqual(client.get('/static/player.js').status_code, 200)
                self.assertEqual(client.get('/static/player.css').status_code, 200)
                self.assertEqual((directory / 'original.mp4').read_bytes(), before)
                client.auth = None
                self.assertEqual(client.get('/files/example/ko.vtt').status_code, 401)


if __name__ == '__main__':
    unittest.main()
