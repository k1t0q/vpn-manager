import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'deploy'))
from diagnose_promotion import file_info,runtime_info,journal_summary


class DiagnosticTests(unittest.TestCase):
    def test_journal_report_excludes_exception_messages_and_secrets(self):
        text=json.dumps({'MESSAGE':'  File "/private/app.py", line 42, in main\nPermissionError: Permission denied SECRET_TOKEN_VALUE'})
        result=journal_summary(text)
        self.assertEqual(result['frames'],[{'file':'app.py','line':42}])
        self.assertEqual(result['exceptions'],['PermissionError'])
        self.assertEqual(result['categories'],['permission_denied'])
        self.assertNotIn('SECRET_TOKEN_VALUE',json.dumps(result))

    def test_report_uses_metadata_and_does_not_read_secret_file_contents(self):
        import os
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/'token.env').write_text('SHOULD_NEVER_APPEAR_IN_REPORT')
            (path/'public.py').write_text('ordinary code');(path/'public.py').chmod(0o666)
            report=runtime_info(path,expected_uid=os.geteuid())
            self.assertEqual(report['non_root_owned'],0)
            self.assertEqual(report['writable_examples'],['public.py'])
            self.assertNotIn('SHOULD_NEVER_APPEAR_IN_REPORT',json.dumps(report))
    def test_missing_preparation_is_reported_without_creating_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'missing'
            self.assertEqual(file_info(path),{'exists':False})
            self.assertEqual(runtime_info(path),{'exists':False})
            self.assertFalse(path.exists())
