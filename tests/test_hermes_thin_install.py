"""Hermes plain receipt fields: synthetic acquisition, no package/network calls."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('thin_installer', ROOT / 'scripts/lib/managed_harness_install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class ThinInstall(unittest.TestCase):
    def test_native_v2_install_details_are_plain_without_source_patch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'runtime'
            root.mkdir()
            calls = []
            def acquire(argv, *, cwd, env):
                calls.append(argv)
                if argv[:2] == ['git', 'clone']:
                    (root / 'source').mkdir()
                if argv[:2] == ['git', 'rev-parse']:
                    return installer.HERMES_COMMIT
                if argv[1:3] == ['-m', 'pm.cli']:
                    source = root / 'source'
                    store = root / 'installs' / hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:16]
                    venv = store / 'environments/fixture'
                    (venv / 'bin').mkdir(parents=True, exist_ok=True)
                    (venv / 'bin/hermes').write_text('fixture')
                    installer.save(store / 'facts.json', {'packages': {'venv': {'environment': str(venv)}}})
                return ''
            with patch.object(installer, 'run', side_effect=acquire), patch.object(installer, 'hermes_stamp_details', return_value={'install_stamp': 'fixture'}):
                details = installer.install_hermes(ROOT, root, '/fixture/python')
            self.assertEqual(details['variant'], 'plain')
            self.assertEqual(details['thin_config_contract'], 1)
            self.assertTrue(installer.generation_id(ROOT, 'hermes').endswith('-v2'))
            self.assertFalse(any('patch' in arg for argv in calls for arg in argv))
            self.assertEqual([argv[3:] for argv in calls if argv[1:3] == ['-m', 'pm.cli']],
                [['install', '--without', 'agent-browser', '--without', 'cua-driver'], ['install', '--extra', 'anthropic']])
            self.assertNotIn('provider_request_limit', json.dumps(details))

    def test_existing_owned_v2_adopts_only_after_stamp_runtime_and_source_checks(self):
        for failure in (None, 'stamp', 'source'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                install = Path(temporary) / 'product'
                root = install / 'harnesses/hermes' / installer.generation_id(ROOT, 'hermes')
                source = root / 'source'
                source.mkdir(parents=True)
                receipt = {'harness': 'hermes', 'complete': True, 'details': {
                    'hermes_source': str(source), 'managed_root': str(root), 'hermes_executable': str(root / 'bin/hermes')}}
                installer.save(root / installer.OWNER, receipt)
                original = (root / installer.OWNER).read_bytes()
                events = []
                def validate(*_):
                    events.append('validate-stamp-and-runtime')
                    if failure == 'stamp':
                        raise ValueError('stamp mismatch')
                def run(argv, **_):
                    events.append(argv)
                    if failure == 'source':
                        raise subprocess.CalledProcessError(1, argv)
                    return ''
                with patch.object(installer, 'validate', side_effect=validate), patch.object(installer, 'run', side_effect=run), patch.object(installer, 'install_hermes') as acquire:
                    if failure:
                        with self.assertRaises(ValueError):
                            installer.provision(ROOT, install, 'hermes', choice='enable')
                        self.assertEqual((root / installer.OWNER).read_bytes(), original)
                    else:
                        installer.provision(ROOT, install, 'hermes', choice='enable')
                        upgraded = installer.read(root / installer.OWNER)
                        self.assertEqual(upgraded['variant'], 'plain')
                        self.assertEqual(upgraded['details']['thin_config_contract'], 1)
                        self.assertEqual(events, ['validate-stamp-and-runtime', ['git', 'diff', '--quiet', installer.HERMES_COMMIT, '--', '.']])
                    acquire.assert_not_called()


if __name__ == '__main__':
    unittest.main()
