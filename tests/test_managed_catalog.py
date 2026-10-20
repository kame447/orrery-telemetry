"""Pure native catalog fixtures; no machine configuration or network."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.managed_catalog import catalog_providers
from dashboard.managed_catalog import load_standard_inputs


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('dashboard.managed_catalog.sys.platform', 'darwin'))
        self.backends = {
            'deepseek': {'model': 'deepseek-flash', 'efforts': ['low', 'high'], 'default_effort': 'low'},
            'strata': {'model': 'fixture-qwen', 'configured_context': 131072,
                       'server': 'Configured local server', 'efforts': ['low', 'high'], 'default_effort': 'high'},
        }
        self.availability = {name: {h: {'available': True, 'reason': None} for h in ('dsh', 'hermes')}
                             for name in self.backends}

    def build(self, **kwargs):
        return catalog_providers(self.backends, self.availability, mode=kwargs.get('mode', 'legacy'), enabled=kwargs.get('enabled', True))

    def test_disabled_or_missing_configuration_adds_no_choices(self):
        self.assertEqual(self.build(enabled=False), [])
        self.assertEqual(catalog_providers({}, {}, mode='legacy', enabled=True), [])

    def test_four_combinations_keep_route_and_harness_separate(self):
        rows = self.build()
        self.assertEqual([r['id'] for r in rows], ['deepseek', 'strata'])
        self.assertEqual(sum(h['available'] for r in rows for h in r['models'][0]['harnesses']), 4)
        for row in rows:
            self.assertNotIn('managed', row)
            self.assertEqual([h['id'] for h in row['models'][0]['harnesses'] if h['default']], ['dsh'])

    def test_public_default_catalog_strings_are_ascii_and_english(self):
        for mode in ('legacy', 'global'):
            with self.subTest(mode=mode):
                rows = self.build(mode=mode)
                self.assertTrue(json.dumps(rows, ensure_ascii=False).isascii())
                for row in rows:
                    self.assertEqual(row['context_note'], 'Server-reported context not available yet')

    def test_global_mode_is_never_advertised_as_legacy(self):
        for mode in ('global', 'unknown', None):
            for row in self.build(mode=mode):
                self.assertTrue(all(not h['available'] and not h['default'] and h['reason'] == 'unsupported_mode'
                                    for h in row['models'][0]['harnesses']))

    def test_missing_runtime_is_unavailable_and_default_moves_to_ready_harness(self):
        self.availability['strata']['dsh'] = {'available': False, 'reason': 'plain_generation_required'}
        row = self.build()[1]['models'][0]['harnesses']
        self.assertEqual(row[0]['reason'], 'plain_generation_required')
        self.assertFalse(row[0]['default'])
        self.assertTrue(row[1]['default'])
        self.availability.clear()
        self.assertTrue(all(not h['available'] for r in self.build() for h in r['models'][0]['harnesses']))

    def test_context_is_unreported_and_private_fields_are_not_copied(self):
        self.backends['strata'].update(base_url='https://private.invalid', keychain={'account': 'private-account'},
                                        executable='/private/runtime', token='dummy-private-token')
        result = self.build()
        self.assertIsNone(result[1]['context'])
        self.assertEqual(result[1]['context_source'], 'unreported')
        self.assertEqual(result[1]['context_note'], 'Server-reported context not available yet')
        text = json.dumps(result)
        for private in ['131072', 'private.invalid', 'private-account', '/private/runtime', 'dummy-private-token']:
            self.assertNotIn(private, text)

    def test_harness_cache_is_the_only_source_of_effort_choices_and_default(self):
        self.availability['deepseek']['dsh'].update(
            efforts=['off', 'low', 'high', 'max'], default_effort='high', effort_source='harness')
        result = self.build()
        self.assertEqual(result[0]['efforts'], ['off', 'low', 'high', 'max'])
        self.assertEqual(result[0]['effort_default'], 'high')
        dsh, hermes = result[0]['models'][0]['harnesses']
        self.assertEqual(dsh['effort_source'], 'harness')
        self.assertIsNone(hermes['efforts'])
        self.assertIsNone(hermes['default_effort'])
        self.assertEqual(hermes['effort_source'], 'unknown')
        self.assertEqual(result[1]['efforts'], [])  # Handwritten low/high is ignored.
        self.assertNotIn('effort_default', result[1])

    def test_inputs_are_unchanged(self):
        before = copy.deepcopy((self.backends, self.availability))
        self.build()
        self.assertEqual((self.backends, self.availability), before)

    def test_private_loader_is_off_without_opt_in(self):
        self.assertEqual(load_standard_inputs(None, mode='legacy'), ({}, {}, False))
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / 'manifest.json'
            manifest.write_text('{"enabled":false}')
            self.assertEqual(load_standard_inputs(manifest, mode='legacy'), ({}, {}, False))
            manifest.write_text('{broken')
            self.assertEqual(load_standard_inputs(manifest, mode='legacy'), ({}, {}, False))



if __name__ == '__main__':
    unittest.main()
