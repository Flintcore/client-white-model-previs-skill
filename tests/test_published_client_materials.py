"""Actual bundled original-byte checks, not visual/client acceptance."""
import importlib.util,json,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SKILL=ROOT/'skills/client-white-model-previs'
spec=importlib.util.spec_from_file_location('published_client_asset_helper',ROOT/'tools/client_assets.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)

class PublishedMaterialTests(unittest.TestCase):
    def setUp(self):
        self.manifest_path=ROOT/'client-materials/manifest.json'
        self.manifest=helper.load_manifest(self.manifest_path)

    def test_repository_and_installed_manifests_have_identical_bytes(self):
        self.assertEqual(self.manifest_path.read_bytes(),(SKILL/'assets/client/manifest.json').read_bytes())

    def test_repository_and_installed_helpers_have_identical_bytes(self):
        self.assertEqual((ROOT/'tools/client_assets.py').read_bytes(),(SKILL/'scripts/client_assets.py').read_bytes())

    def test_actual_seven_bundled_originals_match_size_and_sha(self):
        items=[a for a in self.manifest['assets'] if a['storage']['kind']=='git']
        self.assertEqual(len(items),7)
        report=helper.execute('verify',self.manifest_path,ids=[a['id'] for a in items])
        self.assertTrue(report['passed'])
        self.assertEqual(len(report['assets']),7)
        installed=helper.execute('verify',SKILL/'assets/client/manifest.json',ids=[a['id'] for a in items])
        self.assertTrue(installed['passed'])

    def test_frozen_rules_sha_and_current_source_template_are_explicit(self):
        self.assertEqual(self.manifest['standards_sha256'],helper.file_digest(SKILL/'references/standards.json')[0])
        self.assertEqual(self.manifest['standards_version'],'1.0.0')
        self.assertEqual(self.manifest['default_template_id'],'person-template')
        self.assertEqual(self.manifest['default_source_id'],'0927-01')
        self.assertEqual(self.manifest['default_sample']['frames'],240)

    def test_five_release_urls_are_fixed_and_none_claims_gate_approval(self):
        items=[a for a in self.manifest['assets'] if a['storage']['kind']=='github-release']
        self.assertEqual(len(items),5)
        for item in items:
            self.assertIn('/releases/download/v1.1.0/',helper.github_url(item['storage']['url']))
        for item in self.manifest['assets']:self.assertIs(item['current_gate_approval'],False)
        comparison=next(a for a in items if a['id']=='practice-comparison-01')
        self.assertEqual(comparison['media']['codec'],'hevc')
        self.assertIn('not_current_gate_approval',comparison['role'])

    def test_portable_manifest_has_no_original_machine_account_paths(self):
        text=self.manifest_path.read_text(encoding='utf8')
        for token in ['wxid_','UserData','C:\\Users','F:\\','ASUS']:
            self.assertNotIn(token,text)
        self.assertEqual(len(self.manifest['archive_members']),2)
        self.assertEqual(sum(len(a['members']) for a in self.manifest['archive_members']),15)

if __name__=='__main__':unittest.main()
