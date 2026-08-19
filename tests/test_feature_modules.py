import unittest

import json
import tempfile
from pathlib import Path

from srv import feature_modules, module_installer


class FeatureModuleTests(unittest.TestCase):
    def test_all_bundled_modules_have_feature_mapping(self):
        for module_id, metadata in feature_modules.MODULES.items():
            with self.subTest(module=module_id):
                self.assertTrue(metadata["features"])

    def test_bots_are_external_and_experimental(self):
        self.assertFalse(feature_modules.MODULES["bots"]["bundled"])
        self.assertTrue(feature_modules.MODULES["bots"]["experimental"])

    def test_catalog_reports_enabled_only_when_all_features_enabled(self):
        catalog = feature_modules.module_catalog(lambda key: {"enabled": key != "group_policy"})
        groups = next(item for item in catalog if item["module_id"] == "groups")
        self.assertFalse(groups["enabled"])
        self.assertTrue(groups["installed"])

    def test_enabling_voice_also_enables_groups_dependency(self):
        self.assertEqual(feature_modules.modules_for_state_change("voice", True), ["groups", "voice"])

    def test_disabling_groups_also_disables_voice_dependent(self):
        self.assertEqual(feature_modules.modules_for_state_change("groups", False), ["groups", "voice"])

    def test_installed_module_discovery_requires_manifest(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name); module = root / "bots"; module.mkdir()
            (module / "module.json").write_text(json.dumps({"module_id": "bots"}), encoding="utf-8")
            self.assertEqual(module_installer.installed_module_ids(root), {"bots"})

    def test_module_sources_require_https_and_allowlisted_host(self):
        with self.assertRaises(module_installer.ModuleInstallError): module_installer._validate_url("http://example.com/catalog.json", None)
        with self.assertRaises(module_installer.ModuleInstallError): module_installer._validate_url("https://other.example/catalog.json", {"modules.example"})

    def test_module_id_cannot_escape_server_modules_directory(self):
        for module_id in ("../bots", "Bots", "bad/module", ".hidden", ""):
            with self.subTest(module_id=module_id):
                with self.assertRaises(module_installer.ModuleInstallError):
                    module_installer._validate_module_id(module_id)
        module_installer._validate_module_id("experimental_bots-2")


if __name__ == "__main__":
    unittest.main()
