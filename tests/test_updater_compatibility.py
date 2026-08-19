import unittest

from main import build_windows_zip_update_batch, parse_github_tag, parse_update_feed


class UpdaterCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.feed = {
            "repo": "Raywonder/ThriveMessenger",
            "tag": "v2026-alpha15.10",
            "zip_url": "https://im.tappedin.fm/downloads/thrive_messenger.zip",
            "win_zip_url": "https://im.tappedin.fm/downloads/thrive_messenger.zip",
            "installer_url": "https://im.tappedin.fm/downloads/thrive_messenger_installer.exe",
            "win_installer_url": "https://im.tappedin.fm/downloads/thrive_messenger_installer.exe",
            "mac_zip_url": "https://im.tappedin.fm/downloads/thrive_messenger_tappedin_macos_x86_64.zip",
            "mac_url": "https://im.tappedin.fm/downloads/thrive_messenger_tappedin_macos_x86_64.zip",
            "mac_x86_64_url": "https://im.tappedin.fm/downloads/thrive_messenger_tappedin_macos_x86_64.zip",
        }

    def test_alpha_15_10_orders_after_supported_older_installs(self):
        newest = parse_github_tag(self.feed["tag"])
        for old_tag in ("v2026-alpha15.5", "v2026-alpha15.6", "v2026-alpha15.8", "v2026-alpha15.9"):
            with self.subTest(old_tag=old_tag):
                self.assertGreater(newest, parse_github_tag(old_tag))
                self.assertIsNotNone(parse_update_feed(self.feed, old_tag, "win32"))

    def test_windows_prefers_windows_zip_and_accepts_legacy_installer_key(self):
        feed = dict(self.feed)
        feed.pop("installer_url")
        update = parse_update_feed(feed, "v2026-alpha15.9", "win32")
        self.assertEqual(update["zip_url"], feed["win_zip_url"])
        self.assertEqual(update["installer_url"], feed["win_installer_url"])

    def test_mac_accepts_historical_mac_url_key_without_using_windows_zip(self):
        feed = dict(self.feed)
        feed.pop("mac_zip_url")
        update = parse_update_feed(feed, "v2026-alpha15.9", "darwin")
        self.assertEqual(update["zip_url"], feed["mac_url"])
        self.assertNotEqual(update["zip_url"], feed["win_zip_url"])

    def test_current_or_newer_install_does_not_offer_downgrade(self):
        self.assertIsNone(parse_update_feed(self.feed, "v2026-alpha15.10", "win32"))
        self.assertIsNone(parse_update_feed(self.feed, "v2026-alpha15.11", "win32"))

    def test_new_windows_updater_accepts_nested_and_flat_portable_archives(self):
        batch = build_windows_zip_update_batch(
            r"C:\Temp\thrive_update.zip",
            r"C:\Apps\Thrive",
            r"C:\Apps\Thrive\thrive_messenger.exe",
            1234,
            r"C:\Temp\thrive_update_extract",
        )
        self.assertIn(r"thrive_update_extract\thrive_messenger\thrive_messenger.exe", batch)
        self.assertIn(r'set "SOURCE_DIR=C:\Temp\thrive_update_extract"', batch)
        self.assertIn(r'if not exist "%SOURCE_DIR%\thrive_messenger.exe" goto failed', batch)
        self.assertIn("if errorlevel 1 goto failed", batch)


if __name__ == "__main__":
    unittest.main()
