import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('staging_gate',Path(__file__).resolve().parents[1]/'deploy/staging_gate.py')
gate=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class GateTests(unittest.TestCase):
    def archive(self,name,kind=tarfile.REGTYPE):
        out=io.BytesIO()
        with tarfile.open(fileobj=out,mode='w:gz') as tar:
            entry=tarfile.TarInfo(name);entry.type=kind
            if kind==tarfile.REGTYPE:
                entry.size=2;tar.addfile(entry,io.BytesIO(b'ok'))
            else:
                entry.linkname='/etc/passwd';tar.addfile(entry)
        return out.getvalue()
    def test_traversal_links_and_devices_rejected(self):
        for name,kind in [('repo/../../escape',tarfile.REGTYPE),('/root/escape',tarfile.REGTYPE),('repo/link',tarfile.SYMTYPE),('repo/device',tarfile.CHRTYPE)]:
            with self.subTest(name=name,kind=kind),tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):gate.unpack(self.archive(name,kind),Path(folder)/'release')
    def test_regular_repository_file_is_extracted(self):
        with tempfile.TemporaryDirectory() as folder:
            dest=Path(folder)/'release'
            gate.unpack(self.archive('repo/server/app.py'),dest)
            self.assertEqual((dest/'server/app.py').read_text(),'ok')
    def test_release_id_cannot_be_a_shell_command_or_path(self):
        for value in ['main','../current','0'*40+';id','$(id)']:
            with self.assertRaises(ValueError):gate.release_path(value)


if __name__=='__main__':unittest.main()
