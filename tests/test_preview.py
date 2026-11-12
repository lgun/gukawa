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
            with TestClient(create_app(settings), client=('192.0.2.1', 1234)) as remote:
                self.assertEqual(remote.get('/').status_code, 401)
            protected = Settings(Path(temporary), admin_password='test')
            with TestClient(create_app(protected), client=('127.0.0.1', 1234)) as client:
                self.assertEqual(client.get('/').status_code, 401)


if __name__ == '__main__':
    unittest.main()
