"""Tests for the GUI layer: settings, command line building, packaging hooks.

The window itself needs a display, so only the pure logic is exercised here -
that is exactly the part the .exe depends on.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui
from gui import Settings, parse_settings_argv, stake_label


class SettingsTests(unittest.TestCase):
    def test_defaults_match_the_cli_defaults(self):
        settings = Settings()
        argv = settings.to_argv()
        self.assertIn("--rules", argv)
        self.assertEqual(argv[argv.index("--rules") + 1], "brazilian")
        self.assertEqual(argv[argv.index("--coins") + 1], "0")
        # the parser must accept whatever the panel builds
        args = parse_settings_argv(settings)
        self.assertEqual(args.rules, "brazilian")
        self.assertEqual(args.coins, 0)
        self.assertTrue(args.lobby_walk)

    def test_stake_is_forwarded(self):
        for stake in (0, 1, 2, 3, 4, 5):
            args = parse_settings_argv(Settings(stake=stake))
            self.assertEqual(args.coins, stake)

    def test_board_mode_never_walks_the_lobby(self):
        args = parse_settings_argv(Settings(mode="board"))
        self.assertFalse(args.lobby_walk)

    def test_lobby_mode_walks_the_lobby(self):
        args = parse_settings_argv(Settings(mode="lobby"))
        self.assertTrue(args.lobby_walk)

    def test_sign_in_credentials_reach_the_cli(self):
        settings = Settings(mode="auth_lobby", email="me@example.com", password="secret")
        args = parse_settings_argv(settings)
        self.assertEqual(args.email, "me@example.com")
        self.assertEqual(args.password, "secret")

    def test_credentials_are_dropped_outside_the_sign_in_mode(self):
        settings = Settings(mode="lobby", email="me@example.com", password="secret")
        argv = settings.to_argv()
        self.assertNotIn("--email", argv)
        self.assertNotIn("--password", argv)

    def test_engine_and_browser_options(self):
        settings = Settings(depth=12, time=2.5, poll=0.4, max_moves=17,
                            rules="russian", headless=True, close_browser=True,
                            trace_board=True, channel="chrome", window_size="1280x800")
        args = parse_settings_argv(settings)
        self.assertEqual(args.depth, 12)
        self.assertAlmostEqual(args.time, 2.5)
        self.assertAlmostEqual(args.poll, 0.4)
        self.assertEqual(args.max_moves, 17)
        self.assertEqual(args.rules, "russian")
        self.assertTrue(args.headless)
        self.assertTrue(args.close_browser)
        self.assertTrue(args.trace_board)
        self.assertEqual(args.channel, "chrome")
        self.assertEqual(args.window_size, "1280x800")

    def test_browser_is_kept_open_by_default(self):
        args = parse_settings_argv(Settings())
        self.assertFalse(args.close_browser)

    def test_diagnose_flag(self):
        args = parse_settings_argv(Settings(diagnose=True))
        self.assertTrue(args.diagnose)

    def test_password_is_never_saved(self):
        settings = Settings(email="me@example.com", password="secret")
        self.assertNotIn("password", settings.to_dict())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            settings.save(path)
            self.assertNotIn("secret", path.read_text(encoding="utf-8"))
            loaded = Settings.load(path)
        self.assertEqual(loaded.email, "me@example.com")
        self.assertEqual(loaded.password, "")

    def test_settings_round_trip(self):
        original = Settings(mode="board", stake=4, depth=11, time=1.5, rules="english",
                            headless=True, close_browser=True, trace_board=True,
                            dry_run=True, no_probe=True, no_fit=True,
                            max_moves=9, channel="msedge")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "nested" / "settings.json"
            original.save(path)
            loaded = Settings.load(path)
        self.assertEqual(loaded.to_dict(), original.to_dict())

    def test_unknown_json_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text('{"mode": "board", "nonsense": 1, "password": "x"}',
                            encoding="utf-8")
            loaded = Settings.load(path)
        self.assertEqual(loaded.mode, "board")
        self.assertEqual(loaded.password, "")
        self.assertFalse(hasattr(loaded, "nonsense"))

    def test_missing_settings_file_uses_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            loaded = Settings.load(Path(folder) / "nothing.json")
        self.assertEqual(loaded, Settings())


class LabelTests(unittest.TestCase):
    def test_stake_labels_cover_the_whole_ladder(self):
        self.assertEqual(stake_label(0), "0 — 50 coins")
        self.assertEqual(stake_label(2), "2 — 50 000 coins")
        self.assertEqual(stake_label(5), "5 — 10 000 000 coins")
        self.assertEqual(stake_label(99), "0 — 50 coins")

    def test_code_to_text(self):
        self.assertIn("წარმატებით", gui.code_to_text(0))
        self.assertIn("Playwright", gui.code_to_text(2))
        self.assertIn("7", gui.code_to_text(7))

    def test_masked_argv_hides_the_password(self):
        settings = Settings(mode="auth_lobby", email="a@b.c", password="topsecret")
        masked = gui.BotWindow._masked_argv(settings)
        self.assertNotIn("topsecret", masked)
        self.assertIn("•••", masked)


if __name__ == "__main__":
    unittest.main()
