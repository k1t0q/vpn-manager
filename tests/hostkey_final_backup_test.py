import importlib.util
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'deploy/hostkey-final-backup.py'
spec = importlib.util.spec_from_file_location('hostkey_final_backup', SCRIPT)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class FinalBackupTests(unittest.TestCase):
    def test_snapshot_is_private_and_contains_consistent_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / 'clients.db'
            with sqlite3.connect(db) as connection:
                connection.execute('CREATE TABLE clients(id INTEGER PRIMARY KEY, name TEXT)')
                connection.execute('INSERT INTO clients VALUES(42, ?)', ('Оксана',))
            secret = root / 'push-private.pem'
            secret.write_text('secret-only-for-backup-test')
            files = {'var/lib/caesar-manager/clients.db': db,
                     'var/lib/caesar-manager/push-private.pem': secret}
            result = backup.create_archive(root / 'backups', files,
                                           project_dir=SCRIPT.parents[1], include_code=True)
            target = Path(result['archive'])
            self.assertTrue(result['ok'])
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(result['visible_clients'], 1)
            with tarfile.open(target, 'r:gz') as archive:
                self.assertEqual(set(archive.getnames()), {*files, 'source/vpn-manager-source.tar', 'manifest.json'})
                manifest = json.load(archive.extractfile('manifest.json'))
                self.assertTrue(manifest['sqlite_copied_consistently'])
                self.assertEqual(archive.extractfile('var/lib/caesar-manager/push-private.pem').read(),
                                 b'secret-only-for-backup-test')
                snapshot_bytes = archive.extractfile('var/lib/caesar-manager/clients.db').read()
            copied = root / 'copied.db'
            copied.write_bytes(snapshot_bytes)
            with sqlite3.connect(copied) as connection:
                self.assertEqual(connection.execute('SELECT name FROM clients WHERE id=42').fetchone()[0], 'Оксана')

    def test_refuses_symlinks_and_creates_no_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / 'real'
            real.write_text('private')
            link = root / 'link'
            link.symlink_to(real)
            output = root / 'backups'
            with self.assertRaises(backup.BackupError):
                backup.create_archive(output, {'secret.txt': link}, include_code=False)
            self.assertEqual(list(output.glob('*.tar.gz')), [])


if __name__ == '__main__':
    unittest.main()
