import hashlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import updater


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def info(self):
        return {'Content-Length': str(len(self.getbuffer()))}


class _InterruptedResponse(_Response):
    def __init__(self, value):
        super().__init__(value)
        self._reads = 0

    def read1(self, size=-1):
        self._reads += 1
        if self._reads > 1:
            raise ConnectionResetError('read interrupted')
        return super().read1(2)


class UpdaterTests(unittest.TestCase):
    def test_check_for_update_returns_asset_verification_metadata(self):
        payload = json.dumps({
            'tag_name': 'v2.1.1',
            'body': 'notes',
            'assets': [{
                'name': 'MouseBattery-v2.1.1.exe',
                'browser_download_url': 'https://github.com/example/app.exe',
                'size': 12345,
                'digest': 'sha256:' + 'a' * 64,
                'updated_at': '2026-07-14T00:00:00Z',
            }],
        }).encode()

        with mock.patch.object(updater, '_urlopen', return_value=_Response(payload)):
            result = updater.check_for_update('2.1.0')

        self.assertEqual(
            result,
            (True, 'v2.1.1', 'https://github.com/example/app.exe', 'notes', 12345, 'sha256:' + 'a' * 64),
        )

    def test_official_failure_falls_back_to_verified_mirror(self):
        content = b'MZ' + b'x' * (1024 * 1024)
        digest = hashlib.sha256(content).hexdigest()
        statuses = []

        with tempfile.TemporaryDirectory() as temp_dir:
            exe_path = os.path.join(temp_dir, 'MouseBattery.exe')

            def fake_download(url, target_path, **kwargs):
                if url.startswith('https://github.com/'):
                    raise ConnectionResetError('official reset')
                with open(target_path, 'wb') as output:
                    output.write(content)
                return len(content), digest

            with (
                mock.patch.object(updater.sys, 'frozen', True, create=True),
                mock.patch.object(updater.sys, 'executable', exe_path),
                mock.patch.object(updater.tempfile, 'gettempdir', return_value=temp_dir),
                mock.patch.object(updater, '_download_to_path', side_effect=fake_download) as download,
                mock.patch.object(updater, 'request_process_shutdown', return_value=False),
                mock.patch.object(updater.subprocess, 'Popen') as popen,
            ):
                success = updater.download_and_install(
                    'https://github.com/example/app.exe',
                    expected_size=len(content),
                    expected_digest='sha256:' + digest,
                    on_status=lambda stage, detail='': statuses.append((stage, detail)),
                )

            self.assertTrue(success)
            self.assertEqual(download.call_count, 2)
            self.assertEqual(download.call_args_list[1].args[0], updater.DOWNLOAD_MIRROR_PREFIX + 'https://github.com/example/app.exe')
            self.assertIn(('fallback', 'official reset'), statuses)
            self.assertIn(('verifying', ''), statuses)
            popen.assert_called_once()

    def test_truncated_official_download_falls_back_to_mirror(self):
        content = b'MZ' + b'x' * (1024 * 1024)
        digest = hashlib.sha256(content).hexdigest()
        statuses = []

        with tempfile.TemporaryDirectory() as temp_dir:
            exe_path = os.path.join(temp_dir, 'MouseBattery.exe')

            def fake_download(url, target_path, **kwargs):
                downloaded = b'MZ' if url.startswith('https://github.com/') else content
                with open(target_path, 'wb') as output:
                    output.write(downloaded)
                return len(downloaded), hashlib.sha256(downloaded).hexdigest()

            with (
                mock.patch.object(updater.sys, 'frozen', True, create=True),
                mock.patch.object(updater.sys, 'executable', exe_path),
                mock.patch.object(updater.tempfile, 'gettempdir', return_value=temp_dir),
                mock.patch.object(updater, '_download_to_path', side_effect=fake_download) as download,
                mock.patch.object(updater, 'request_process_shutdown', return_value=False),
                mock.patch.object(updater.subprocess, 'Popen'),
            ):
                success = updater.download_and_install(
                    'https://github.com/example/app.exe',
                    expected_size=len(content),
                    expected_digest='sha256:' + digest,
                    on_status=lambda stage, detail='': statuses.append((stage, detail)),
                )

            self.assertTrue(success)
            self.assertEqual(download.call_count, 2)
            self.assertTrue(any(stage == 'fallback' and '过小' in detail for stage, detail in statuses))

    def test_hash_mismatch_rejects_update_and_removes_new_file(self):
        content = b'MZ' + b'x' * (1024 * 1024)
        statuses = []

        with tempfile.TemporaryDirectory() as temp_dir:
            exe_path = os.path.join(temp_dir, 'MouseBattery.exe')

            def fake_download(url, target_path, **kwargs):
                with open(target_path, 'wb') as output:
                    output.write(content)
                return len(content), '0' * 64

            with (
                mock.patch.object(updater.sys, 'frozen', True, create=True),
                mock.patch.object(updater.sys, 'executable', exe_path),
                mock.patch.object(updater.tempfile, 'gettempdir', return_value=temp_dir),
                mock.patch.object(updater, '_download_to_path', side_effect=fake_download),
                mock.patch.object(updater.subprocess, 'Popen') as popen,
            ):
                success = updater.download_and_install(
                    'https://github.com/example/app.exe',
                    expected_size=len(content),
                    expected_digest='sha256:' + 'a' * 64,
                    on_status=lambda stage, detail='': statuses.append((stage, detail)),
                )

            self.assertFalse(success)
            self.assertFalse(os.path.exists(exe_path + '.new'))
            self.assertEqual(statuses[-1][0], 'error')
            popen.assert_not_called()

    def test_missing_official_digest_rejects_update_before_download(self):
        statuses = []
        with tempfile.TemporaryDirectory() as temp_dir:
            exe_path = os.path.join(temp_dir, 'MouseBattery.exe')
            with (
                mock.patch.object(updater.sys, 'frozen', True, create=True),
                mock.patch.object(updater.sys, 'executable', exe_path),
                mock.patch.object(updater.tempfile, 'gettempdir', return_value=temp_dir),
                mock.patch.object(updater, '_download_to_path') as download,
            ):
                success = updater.download_and_install(
                    'https://github.com/example/app.exe',
                    expected_size=1024 * 1024,
                    expected_digest='',
                    on_status=lambda stage, detail='': statuses.append((stage, detail)),
                )

        self.assertFalse(success)
        self.assertEqual(statuses[-1][0], 'error')
        download.assert_not_called()

    def test_download_retry_restarts_after_interrupted_read(self):
        content = b'MZ-update-content'
        retries = []
        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, 'update.new')
            with mock.patch.object(
                updater,
                '_urlopen',
                side_effect=[_InterruptedResponse(content), _Response(content)],
            ):
                size, digest = updater._download_to_path(
                    'https://mirror.example/app.exe',
                    target,
                    expected_size=len(content),
                    retries=1,
                    on_retry=lambda attempt, total, error: retries.append((attempt, total)),
                )

            self.assertEqual((size, digest), (len(content), hashlib.sha256(content).hexdigest()))
            self.assertEqual(retries, [(1, 1)])
            with open(target, 'rb') as downloaded:
                self.assertEqual(downloaded.read(), content)

    def test_startup_cleanup_removes_old_and_incomplete_update_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            exe_path = os.path.join(temp_dir, 'MouseBattery.exe')
            for suffix in ('.old', '.new'):
                with open(exe_path + suffix, 'wb') as stale:
                    stale.write(b'stale')

            with (
                mock.patch.object(updater.sys, 'frozen', True, create=True),
                mock.patch.object(updater.sys, 'executable', exe_path),
            ):
                updater.clean_old_version()

            self.assertFalse(os.path.exists(exe_path + '.old'))
            self.assertFalse(os.path.exists(exe_path + '.new'))


if __name__ == '__main__':
    unittest.main()
