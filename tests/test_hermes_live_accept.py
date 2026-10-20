"""Private configuration fixtures for locale, consent and concurrent generation."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


defaults = load('fixture_telemetry_defaults', ROOT / 'integrations/hermes/telemetry_defaults.py')
fixtures = load('fixture_thin_config', ROOT / 'tests/test_hermes_thin_config.py')
try:
    from ruamel.yaml import YAML
    read_yaml = YAML(typ='safe').load
except ModuleNotFoundError:
    from yaml import safe_load as read_yaml


class TelemetryDefaults(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.profile, self.state = self.root / 'profile', self.root / 'state'
        self.profile.mkdir()
        self.path = self.profile / 'config.yaml'

    def settle(self, raw):
        self.path.write_bytes(raw.encode('utf-8'))
        self.assertTrue(defaults.settle_unanswered(self.profile, self.state))
        return self.path.read_bytes().decode('utf-8')

    def test_only_answer_values_change_in_yaml_and_json(self):
        for raw in [
                '# preserve comments\nmodel: "quoted model" # preserve spaces\ntelemetry:\n  other: 3 # preserve me\n  shared_metrics:\n    enabled: false # previous default\n    send: false\n    offer_version: 1 # old offer\nmemory: {enabled: true}\n',
                '{ "model" : "quoted model", "telemetry": {"other": 3, "shared_metrics": {"enabled": false, "send": false, "offer_version": 1}}, "memory": {"enabled": true} }\r\n']:
            with self.subTest(raw=raw):
                expected = raw.replace('offer_version: 1', 'offer_version: 2').replace('"offer_version": 1', '"offer_version": 2')
                self.assertEqual(self.settle(raw), expected)
        self.assertEqual(len((self.state / 'telemetry-defaults.log').read_text().splitlines()), 2)

    def test_missing_answer_is_added_without_reformatting_other_keys(self):
        for raw in ['', '# header\n', 'model: "keep" # comment\n', '{"model" : "keep"}',
                    'telemetry:\n  other: 7\nmodel: "keep"',
                    'telemetry:\n  shared_metrics: {}\nmodel: "keep"',
                    'telemetry:\n  shared_metrics:\n    enabled: false # keep\nmodel: "keep"\n']:
            with self.subTest(raw=raw):
                revised = self.settle(raw)
                data = read_yaml(revised)
                self.assertEqual(data['telemetry']['shared_metrics'], defaults.ANSWER)
                if '"keep"' in raw:
                    # The complete unrelated key token/comment survives byte for byte.
                    unchanged = next(line for line in raw.splitlines() if 'model:' in line) if 'model:' in raw else '"model" : "keep"'
                    self.assertIn(unchanged, revised)
                self.assertFalse(defaults.settle_unanswered(self.profile, self.state))

    def test_explicit_choices_are_byte_identical(self):
        for section in [dict(enabled=True, send=True), dict(enabled=True, send=False),
                        dict(enabled=False, send=True), dict(enabled=False, send=False, offer_version=2)]:
            with self.subTest(section=section):
                raw = json.dumps({'model': {'default': 'keep'}, 'telemetry': {'shared_metrics': section}}, indent=3)
                self.path.write_bytes(raw.encode())
                self.assertFalse(defaults.settle_unanswered(self.profile, self.state))
                self.assertEqual(self.path.read_bytes(), raw.encode())
        self.assertFalse(self.state.exists())

    def test_foreign_config_symlink_is_not_read_or_replaced(self):
        foreign = self.root / 'foreign.yaml'
        foreign.write_text('model: private\n')
        self.path.symlink_to(foreign)
        with self.assertRaisesRegex(ValueError, 'owned_link_conflict'):
            defaults.settle_unanswered(self.profile, self.state)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(foreign.read_bytes(), b'model: private\n')


NATIVE_DATA_SCRIPT = r'''
import importlib.util,json,os,shutil,sys,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
source=Path(sys.argv[1]); sys.path.insert(0,str(source))
def audit(event,args):
    if event in ('socket.connect','socket.getaddrinfo','subprocess.Popen'):
        raise AssertionError('native data fixture forbids network/children')
sys.addaudithook(audit)
with tempfile.TemporaryDirectory() as temporary:
    root=Path(temporary); profile=root/'profile';profile.mkdir()
    os.environ['HERMES_HOME']=str(profile)
    workspace=root/'workspace/agent';workspace.mkdir(parents=True)
    shutil.copyfile(source/'agent/i18n.py',workspace/'i18n.py')
    spec=importlib.util.spec_from_file_location('fixture_packaged_i18n',workspace/'i18n.py')
    i18n=importlib.util.module_from_spec(spec);spec.loader.exec_module(i18n)
    keys=['cli.chat.initializing_agent','cli.tui.placeholder_busy','cli.stream.tool_preparing',
          'cli.shared.duration_s','display.review.summary_cli']
    assert all(i18n.t(key,lang='en')==key for key in keys)
    os.environ['HERMES_BUNDLED_LOCALES']=str(source/'locales')
    i18n.reset_language_cache()
    assert all(i18n.t(key,lang='en')!=key for key in keys)
    from hermes_cli.observability import shared_metrics_consent as consent
    # Exercise the actual native offer predicate without a terminal UI or setup imports.
    import hermes_cli.config as config
    spec=importlib.util.spec_from_file_location('fixture_generator',str(Path(sys.argv[2]).with_name('thin_config.py')))
    generator=importlib.util.module_from_spec(spec);spec.loader.exec_module(generator)
    merge_cases=[({'url':'https://stale.invalid/mcp'},{'command':'/selected/server'}),
                 ({'command':'/old','args':['old']},{'command':'/selected','args':['selected']}),
                 ({'env':{'old':'keep'}},{'env':None})]
    for old,selected in merge_cases:
        effective=config._deep_merge({'server':old},{'server':selected})['server']
        assert generator._effective_mcp(old,selected)==effective
    setup=SimpleNamespace(is_noninteractive=lambda:False)
    cases=[{'enabled':False,'send':False},
           {'enabled':True,'send':True}, {'enabled':True,'send':False},
           {'enabled':False,'send':False,'offer_version':2}]
    for section in cases:
        before={'telemetry':{'shared_metrics':section}}
        (profile/'config.yaml').write_text(json.dumps(before))
        spec=importlib.util.spec_from_file_location('fixture_defaults',sys.argv[2])
        defaults=importlib.util.module_from_spec(spec);spec.loader.exec_module(defaults)
        defaults.settle_unanswered(profile,root/'state')
        after=json.loads((profile/'config.yaml').read_text())
        assert consent.consent_state(after)['decided'] is True
        calls=[]
        with patch.dict(sys.modules,{'hermes_cli.setup':setup}), \
             patch.object(config,'is_managed',return_value=False), \
             patch.object(consent,'_current_state',return_value=consent.consent_state(after)), \
             patch.object(consent,'offer_consent',side_effect=lambda **kwargs:calls.append(kwargs)), \
             patch.object(consent.sys,'stdin',SimpleNamespace(isatty=lambda:True)), \
             patch.object(consent.sys,'stdout',SimpleNamespace(isatty=lambda:True)):
            consent.offer_consent_before_chat(SimpleNamespace())
        assert not calls,calls
    print(json.dumps({'translated_keys':len(keys),'native_consent_cases':len(cases),'mcp_merge_cases':len(merge_cases),'network':0}))
'''


class NativeAcceptanceData(unittest.TestCase):
    def native(self):
        source, python = os.environ.get('ORRERY_HERMES_TEST_SOURCE'), os.environ.get('ORRERY_HERMES_TEST_PYTHON')
        if not source or not python:
            self.skipTest('Explicit installed dependency interpreter/source required')
        return source, python

    def test_real_native_locale_fallback_and_consent_offer(self):
        source, python = self.native()
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run([python, '-I', '-B', '-c', NATIVE_DATA_SCRIPT, source,
                str(ROOT / 'integrations/hermes/telemetry_defaults.py')],
                cwd=home, env={'HOME':home,'PATH':os.defpath,'UV_OFFLINE':'1','HERMES_DISABLE_LAZY_INSTALLS':'1'},
                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['translated_keys'], 5)

    def test_two_generations_keep_other_yaml_bytes_and_log_only_one_default(self):
        _, python = self.native()
        real_popen = subprocess.Popen
        fixture = fixtures.ThinConfig()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        profile = fixture.runtime / 'profiles/deepseek'
        profile.mkdir(parents=True)
        raw = '# unchanged\nmodel: "preserve" # verbatim\ntelemetry:\n  shared_metrics:\n    enabled: false\n    send: false\n    offer_version: 1 # old\nmemory: {enabled: true}\n'
        (profile / 'config.yaml').write_text(raw)
        documents=[]
        for name in ('first','second'):
            document={**fixture.document,'state_dir':str(fixture.base/name)}
            path=fixture.base/(name+'.json');path.write_text(json.dumps(document));documents.append(path)
        worker='import json,sys;sys.path.insert(0,sys.argv[1]);from integrations.hermes.thin_config import generate;generate(json.load(open(sys.argv[2])))'
        processes=[]
        try:
            with patch.object(subprocess,'Popen',real_popen):
                for document in documents:
                    processes.append(subprocess.Popen([python,'-I','-B','-c',worker,str(ROOT),str(document)],
                        env={'HOME':str(fixture.base),'PATH':os.defpath,'UV_OFFLINE':'1','HERMES_DISABLE_LAZY_INSTALLS':'1'},
                        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
                for process in processes:
                    stdout,stderr=process.communicate(timeout=20)
                    self.assertEqual(process.returncode,0,stdout+stderr)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate();process.wait(timeout=5)
        self.assertEqual((profile/'config.yaml').read_text(),raw.replace('offer_version: 1','offer_version: 2'))
        logs=list(fixture.base.glob('*/telemetry-defaults.log'))
        self.assertEqual(len(logs),1)
        self.assertEqual(len(logs[0].read_text().splitlines()),1)


if __name__ == '__main__':
    unittest.main()
