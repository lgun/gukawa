from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from gukawa.config import Settings
from gukawa.db import Database
from gukawa.subtitles import Cue, read_cues, read_subtitles, write_cues
from gukawa.translation import TranslationError, post_process_korean, post_process_title, translate
from gukawa.translation_archive import comparison_data, digest, save_json, save_translation, translation_input
from gukawa.web import create_app


class TranslationArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.settings = Settings(Path(self.temporary.name), api_key='unit-test-key', translation_model='gpt-6-luna',
                                 admin_password='test', local_preview=True, auto_sync=False)
        self.directory = self.settings.data_dir / 'episodes' / 'example'
        self.directory.mkdir()
        self.source = [Cue(1, 3, 'You okay?'), Cue(4, 6, 'Level 5?')]
        write_cues(self.directory / 'source.json', self.source)
        self.direct = [Cue(1, 3, '괜찮아? 직접 번역 기준문'), Cue(4, 6, '5급?')]

    def test_api_is_separate_default_is_retained_and_source_is_bound(self):
        source_before = (self.directory / 'source.json').read_bytes()
        save_translation(self.directory, 'direct', self.direct, '382화', make_default=True)
        default_before = (self.directory / 'ko.vtt').read_bytes()
        korean = [Cue(1, 3, '괜찮니?'), Cue(4, 6, '5급이야?')]
        save_translation(self.directory, 'api', korean, '382화', model='gpt-6-luna', audit=[])
        comparison = comparison_data(self.directory)
        self.assertTrue(comparison['complete'])
        self.assertEqual(comparison['rows'][0]['direct'], self.direct[0].text)
        self.assertEqual(comparison['rows'][0]['api'], korean[0].text)
        self.assertEqual((self.directory / 'ko.vtt').read_bytes(), default_before)
        self.assertEqual((self.directory / 'source.json').read_bytes(), source_before)
        self.assertEqual(read_subtitles(self.directory / 'ko.api.vtt'), korean)
        self.assertIn('background-color:', (self.directory / 'ko.api.vtt').read_text(encoding='utf-8'))
        write_cues(self.directory / 'source.json', [Cue(1, 3, 'Different words'), self.source[1]])
        with self.assertRaises(ValueError):
            comparison_data(self.directory)

    def test_reviewed_input_fingerprint_and_timing_are_checked(self):
        reviewed = [self.source[0], Cue(4, 6, 'Passing level 5?')]
        write_cues(self.directory / 'translation-input.json', reviewed)
        save_json(self.directory / 'translation-input.meta.json', {
            'input_file': 'translation-input.json', 'input_sha256': digest(self.directory / 'translation-input.json'),
            'original_sha256': digest(self.directory / 'source.json')})
        self.assertEqual(translation_input(self.directory)[0], reviewed)
        with self.assertRaises(ValueError):
            save_translation(self.directory, 'direct', [self.direct[0]], '382화')
        (self.directory / 'translation-input.json').write_text('[]', encoding='utf-8')
        with self.assertRaises(ValueError):
            translation_input(self.directory)

    def test_raw_automation_trial_keeps_its_own_input_binding(self):
        original = [self.source[0], Cue(4, 6, 'Level ?')]
        write_cues(self.directory / 'source.json', original)
        write_cues(self.directory / 'translation-input.json', self.source)
        save_json(self.directory / 'translation-input.meta.json', {
            'input_file': 'translation-input.json', 'input_sha256': digest(self.directory / 'translation-input.json'),
            'original_sha256': digest(self.directory / 'source.json')})
        save_translation(self.directory, 'direct', self.direct, '382화', make_default=True)
        save_translation(self.directory, 'api', self.direct, '382화', model='gpt-6-luna')
        raw = [self.direct[0], Cue(4, 6, '레벨 ?')]
        manifest = save_translation(self.directory, 'raw', raw, '382화', model='gpt-6-luna')
        self.assertEqual(manifest['input_file'], 'source.json')
        data = comparison_data(self.directory)
        self.assertTrue(data['complete'])
        self.assertEqual(data['rows'][1]['text'], 'Level 5?')
        self.assertEqual(data['rows'][1]['raw_source'], 'Level ?')
        self.assertEqual(data['rows'][1]['raw'], '레벨 ?')

    def test_api_sends_only_source_and_records_exact_request_and_provider_model(self):
        save_translation(self.directory, 'direct', self.direct, '382화', make_default=True)
        requests = []
        def respond(request):
            payload = json.loads(request.content)
            requests.append(payload)
            return httpx.Response(200, headers={'x-request-id': 'test-request'}, json={
                'id': 'test-response', 'model': 'gpt-6-luna-test-snapshot', 'status': 'completed',
                'usage': {'input_tokens': 100, 'output_tokens': 50},
                'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps({
                    'title_ko': '치이카와 382화', 'cues': [{'id': 0, 'text': '괜찮니?'}, {'id': 1, 'text': '5급이야?'}]})}]}]})
        audit = []
        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch('gukawa.translation.httpx.Client', return_value=client):
            title, korean = translate(self.source, 'Episode 382', self.settings, audit=audit)
        serialized = json.dumps(requests, ensure_ascii=False)
        self.assertNotIn('직접 번역 기준문', serialized)
        self.assertEqual(requests[0]['model'], 'gpt-6-luna')
        self.assertFalse(requests[0]['store'])
        self.assertEqual(json.loads(requests[0]['input'])['cues'][1]['text'], 'Level 5?')
        self.assertNotIn(self.settings.api_key, json.dumps(audit))
        manifest = save_translation(self.directory, 'api', korean, title, model='gpt-6-luna', audit=audit)
        self.assertEqual(manifest['response_models'], ['gpt-6-luna-test-snapshot'])
        self.assertEqual(audit[0]['request_id'], 'test-request')

    def test_incorrect_schema_and_order_cannot_produce_translation(self):
        for data in [None, {'title_ko': '제목', 'cues': [{'id': True, 'text': '오류'}]},
                     {'title_ko': '제목', 'cues': [{'id': 1, 'text': '오류'}, {'id': 0, 'text': '오류'}]},
                     {'title_ko': '제목', 'cues': [{'id': 0, 'text': ''}, {'id': 1, 'text': '문장'}]}]:
            with self.subTest(data=data), patch('gukawa.translation.request_batch', return_value=data):
                with self.assertRaises(TranslationError):
                    translate(self.source, 'Episode', self.settings)
        with patch('gukawa.translation.httpx.Client') as client, self.assertRaises(TranslationError):
            translate(self.source, 'Episode', replace(self.settings, api_key=''))
        client.assert_not_called()

    def test_player_uses_only_default_track_and_variant_files_remain_protected_remotely(self):
        database = Database(self.settings.database)
        database.enqueue('example', '', 'Episode 382', episode_number=382, status='review')
        database.enqueue('previous', '', 'Episode 381', episode_number=381, status='review')
        save_json(self.directory / 'metadata.json', {'video_file':'original.mp4'})
        save_translation(self.directory, 'direct', self.direct, '382화', make_default=True)
        save_translation(self.directory, 'api', [Cue(1,3,'괜찮니?'),Cue(4,6,'5급?')], '382화', model='gpt-6.1-terra', make_default=True)
        app = create_app(self.settings)
        with TestClient(app, client=('127.0.0.1', 1234)) as client:
            page = client.get('/episodes/example').text
            # One Korean track (the default ko.vtt) with an on/off switch; no version picker or comparison table.
            self.assertEqual(page.count('data-korean-track'), 1)
            self.assertIn('src="/files/example/ko.vtt"', page)
            self.assertNotIn('data-translation-select', page)
            self.assertNotIn('번역 비교', page)
            self.assertIn('data-prev-url="/episodes/previous"', page)
            self.assertIn('data-next-url=""', page)
            self.assertEqual(client.get('/files/example/ko.direct.vtt').status_code, 200)
            self.assertEqual(client.get('/files/example/translation.api.audit.json').status_code, 404)
        with TestClient(app, client=('192.0.2.1', 1234)) as remote:
            self.assertEqual(remote.get('/files/example/ko.direct.vtt').status_code,401)

    def test_post_processing_only_normalises_punctuation(self):
        self.assertEqual(post_process_korean("…이상한 질문인데…"), "이상한 질문인데...")
        self.assertEqual(post_process_korean("그리고… 있잖아…"), "그리고... 있잖아...")
        self.assertEqual(post_process_korean("맞아!! 다행이다.."), "맞아!! 다행이다...")
        self.assertEqual(post_process_korean("..이제... 집에 갈까?"), "이제... 집에 갈까?")
        self.assertEqual(post_process_korean("·그건... 통나무잖아?"), "그건... 통나무잖아?")
        self.assertEqual(post_process_korean("…"), "...")
        self.assertEqual(post_process_korean("하! 야하!"), "하! 야하!")
        # Context-dependent wording chosen by the translator must survive (ep. 370 '야!', ep. 363 '정답!').
        self.assertEqual(post_process_korean("야!"), "야!")
        self.assertEqual(post_process_korean("정답!"), "정답!")
        self.assertEqual(post_process_title("치이카와 제382화"), "치이카와 382화")
        self.assertEqual(post_process_title("제382화"), "382화")

    def test_japanese_row_is_picked_from_stacked_subtitles(self):
        from gukawa.subtitles import choose_japanese, japanese_from_lines, read_japanese, write_japanese
        rows = [(10, 50, '1111', 0.95), (40, 50, 'What was the gift for passing Level 5?', 0.99),
                (70, 50, '升到5级時我收到的禮物是什麼？', 0.96), (100, 50, '5級のお祝いでくれたものってなんでしようか', 0.95)]
        self.assertEqual(japanese_from_lines(rows), '5級のお祝いでくれたものってなんでしようか')
        # Kanji-only lines: the lower CJK row is Japanese; a single CJK row is assumed Chinese.
        self.assertEqual(japanese_from_lines([(70, 50, '合格！', 0.9), (100, 50, '合格！！', 0.9)]), '合格！！')
        self.assertEqual(japanese_from_lines([(70, 50, '合格！', 0.9)]), '')
        self.assertEqual(japanese_from_lines([(100, 50, 'もしも…', 0.4)]), '')
        self.assertEqual(choose_japanese([('もしも…', 0.8), ('もしも…', 0.7), ('もしもさ…', 0.99)]), 'もしも…')
        self.assertEqual(choose_japanese([]), '')
        path = self.directory / 'source.ja.json'
        self.assertIsNone(read_japanese(path, self.source))
        write_japanese(path, self.source, ['大丈夫？', ''])
        self.assertEqual(read_japanese(path, [self.source[1], Cue(9, 10, 'new')]), ['', ''])
        self.assertEqual(read_japanese(path, self.source), ['大丈夫？', ''])

    def test_japanese_source_is_sent_with_english(self):
        requests = []
        def respond(request):
            requests.append(json.loads(json.loads(request.content)['input']))
            return httpx.Response(200, json={'model': 'gpt-6-luna', 'status': 'completed', 'output': [
                {'content': [{'type': 'output_text', 'text': json.dumps({'title_ko': '치이카와 382화', 'cues': [
                    {'id': 0, 'text': '괜찮아?'}, {'id': 1, 'text': '5급?'}]})}]}]})
        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch('gukawa.translation.httpx.Client', return_value=client):
            translate(self.source, 'Episode', self.settings, japanese=['大丈夫？', ''])
        self.assertEqual(requests[0]['cues'][0], {'id': 0, 'start': 1, 'end': 3, 'ja': '大丈夫？', 'text': 'You okay?'})
        with self.assertRaises(TranslationError):
            translate(self.source, 'Episode', self.settings, japanese=['大丈夫？'])

    def test_previous_episode_context_is_sent_separately(self):
        database = Database(self.settings.database)
        database.enqueue('previous', '', 'Episode 381', episode_number=381, status='review', title_ko='치이카와 381화')
        previous = self.settings.data_dir / 'episodes' / 'previous'
        previous.mkdir()
        write_cues(previous / 'source.json', [Cue(1, 2, 'Sorry!')])
        write_cues(previous / 'ko.json', [Cue(1, 2, '미안!')])
        from gukawa.translation import episode_context
        context = episode_context(database, self.settings, {'episode_number': 382})
        self.assertEqual(context, [{'episode': 381, 'title_ko': '치이카와 381화', 'lines': [{'en': 'Sorry!', 'ko': '미안!'}]}])
        self.assertEqual(episode_context(database, self.settings, {'episode_number': None}), [])
        requests = []
        def respond(request):
            requests.append(json.loads(json.loads(request.content)['input']))
            return httpx.Response(200, json={'model': 'gpt-6-luna', 'status': 'completed', 'output': [
                {'content': [{'type': 'output_text', 'text': json.dumps({'title_ko': '치이카와 제382화', 'cues': [
                    {'id': 0, 'text': '…어디 아파? 괜찮아?'}, {'id': 1, 'text': '5급?'}]})}]}]})
        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch('gukawa.translation.httpx.Client', return_value=client):
            title, translated = translate(self.source, 'ちいかわー第三八二話', self.settings, context=context)
        self.assertEqual(requests[0]['previous_episodes'], context)
        self.assertEqual(len(requests[0]['cues']), 2)
        self.assertEqual(title, '치이카와 382화')
        self.assertEqual(translated[0].text, '어디 아파? 괜찮아?')


if __name__ == '__main__':
    unittest.main()
