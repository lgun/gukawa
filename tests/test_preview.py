from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from gukawa.config import Settings
from gukawa.db import Database
from gukawa.web import create_app


class PreviewTests(unittest.TestCase):
    def test_loopback_preview_reads_without_auth_but_cannot_run_admin_actions(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(Path(temporary), admin_password='test', local_preview=True)
            database = Database(settings.database)
            database.enqueue('sample', '', 'Episode 360', episode_number=360)
            directory = settings.data_dir / 'episodes' / 'sample'
            directory.mkdir()
            (directory / 'source.txt').write_text('English source', encoding='utf-8')
            with TestClient(create_app(settings), client=('127.0.0.1', 1234)) as client:
                for path in ('/', '/episodes/sample', '/files/sample/source.txt'):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200, path)
                    self.assertNotIn('www-authenticate', response.headers)
                self.assertEqual(client.get('/api/admin/status').status_code, 401)
                self.assertEqual(client.post('/api/admin/scan', json={'mode': 'latest'},
                                             headers={'X-Gukawa-Action': 'admin'}).status_code, 401)
                self.assertFalse(database.get_state('sync_requested'))
            with TestClient(create_app(settings), client=('192.0.2.1', 1234), follow_redirects=False) as remote:
                self.assertEqual(remote.get('/').headers['location'], '/login?next=%2F')
                self.assertEqual(remote.get('/files/sample/source.txt').status_code, 401)
            protected = Settings(Path(temporary), admin_password='test')
            with TestClient(create_app(protected), client=('127.0.0.1', 1234), follow_redirects=False) as client:
                self.assertEqual(client.get('/').status_code, 303)


    def test_watch_only_account_sees_viewer_but_not_admin(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(Path(temporary), admin_password='admin-secret', viewer_username='watcher', viewer_password='pw')
            database = Database(settings.database)
            database.enqueue('sample', '', 'Episode 360', episode_number=360, status='review')
            directory = settings.data_dir / 'episodes' / 'sample'
            directory.mkdir()
            (directory / 'source.txt').write_text('English source', encoding='utf-8')
            with TestClient(create_app(settings), client=('192.0.2.1', 1234), follow_redirects=False) as client:
                client.auth = ('watcher', 'pw')
                home = client.get('/')
                self.assertEqual(home.status_code, 200)
                self.assertNotIn('href="/admin"', home.text)
                self.assertEqual(client.get('/episodes/sample').status_code, 200)
                self.assertEqual(client.get('/files/sample/source.txt').status_code, 200)
                self.assertEqual(client.get('/admin').status_code, 303)
                self.assertEqual(client.get('/api/admin/status').status_code, 401)
                client.auth = ('watcher', 'wrong')
                self.assertEqual(client.get('/').status_code, 303)
                client.auth = ('admin', 'admin-secret')
                self.assertIn('href="/admin"', client.get('/').text)
                self.assertEqual(client.get('/admin').status_code, 200)

    def test_login_page_sets_a_session_cookie_without_basic_auth(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(Path(temporary), admin_password='admin-secret', viewer_username='watcher', viewer_password='pw')
            database = Database(settings.database)
            database.enqueue('sample', '', 'Episode 360', episode_number=360, status='review')
            directory = settings.data_dir / 'episodes' / 'sample'
            directory.mkdir()
            (directory / 'source.txt').write_text('English source', encoding='utf-8')
            with TestClient(create_app(settings), client=('192.0.2.1', 1234), follow_redirects=False) as client:
                first = client.get('/episodes/sample')
                self.assertEqual(first.headers['location'], '/login?next=%2Fepisodes%2Fsample')
                self.assertNotIn('www-authenticate', first.headers)
                page = client.get(first.headers['location'])
                self.assertEqual(page.status_code, 200)
                self.assertIn('name="next" value="/episodes/sample"', page.text)
                wrong = client.post('/login', data={'username': 'watcher', 'password': 'nope', 'next': '/'})
                self.assertEqual(wrong.status_code, 401)
                self.assertIn('맞지 않아요', wrong.text)
                evil = client.post('/login', data={'username': 'watcher', 'password': 'pw', 'next': '//evil.example'})
                self.assertEqual(evil.headers['location'], '/')
                login = client.post('/login', data={'username': 'watcher', 'password': 'pw', 'next': '/episodes/sample'})
                self.assertEqual(login.headers['location'], '/episodes/sample')
                self.assertIn('httponly', login.headers['set-cookie'].lower())
                self.assertEqual(client.get('/episodes/sample').status_code, 200)
                self.assertEqual(client.get('/files/sample/source.txt').status_code, 200)
                self.assertIn('로그아웃', client.get('/').text)
                self.assertEqual(client.get('/admin').status_code, 303)
                self.assertEqual(client.get('/api/admin/status').status_code, 401)
                client.post('/logout')
                client.cookies.clear()
                self.assertEqual(client.get('/').status_code, 303)
                client.post('/login', data={'username': 'admin', 'password': 'admin-secret', 'next': '/admin'})
                self.assertEqual(client.get('/admin').status_code, 200)
                self.assertEqual(client.get('/api/admin/status').status_code, 200)
                # Changing a password invalidates sessions issued with the old one.
            changed = Settings(Path(temporary), admin_password='new-secret', viewer_username='watcher', viewer_password='pw')
            with TestClient(create_app(changed), client=('192.0.2.1', 1234), follow_redirects=False) as again:
                again.cookies.set('gukawa_session', client.cookies.get('gukawa_session'))
                self.assertEqual(again.get('/admin').status_code, 303)
            with TestClient(create_app(settings), client=('192.0.2.2', 1234), follow_redirects=False) as guesser:
                for _ in range(10):
                    guesser.post('/login', data={'username': 'watcher', 'password': 'x'})
                self.assertEqual(guesser.post('/login', data={'username': 'watcher', 'password': 'pw'}).status_code, 429)


if __name__ == '__main__':
    unittest.main()
