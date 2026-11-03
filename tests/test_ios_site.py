"""Tests for the iOS backend of the bot: :class:`checkersbot_ios.site.WebViewSite`.

The bridge only needs an ``evaluate(script)`` callable.  The browser tests use
Playwright's Chromium as a stand-in for WKWebView, so the *real* JavaScript
(``READ_STATE_JS`` and the site's own click handlers) runs exactly as it will on
the phone - only the transport differs.  That is what makes the iOS backend
testable on a Windows machine without a Mac.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:          # the app package, as Briefcase lays it out
    sys.path.insert(0, str(ROOT / "src"))

from checkersbot.bot import CheckersBot        # noqa: E402
from checkersbot.site import SiteError         # noqa: E402
from checkersbot_ios.site import WebViewSite   # noqa: E402

try:                                           # the UI module needs Toga (device only)
    from checkersbot_ios import app as ios_app
except Exception:                              # pragma: no cover - no Toga here
    ios_app = None

try:                                           # optional: only the browser tests need it
    from playwright.sync_api import sync_playwright

    HAVE_PLAYWRIGHT = True
except Exception:                              # pragma: no cover - environment dependent
    sync_playwright = None
    HAVE_PLAYWRIGHT = False

MOCK_PAGE = pathlib.Path(__file__).with_name("mock_checkers.html")


def ready_payload() -> dict:
    """A board as ``READ_STATE_JS`` would return it: 32 squares, 24 stones."""
    squares = []
    for index in range(64):
        x, y = index % 8 + 1, index // 8 + 1
        if (x + y) % 2 == 0:
            continue                            # only the dark squares are in play
        squares.append(
            {"absolute": index, "x": x, "y": y, "active": False, "playable": True}
        )
    stones = [
        {"id": str(index), "absolute": squares[index]["absolute"], "color": "white",
         "king": False, "clickable": True, "highlighted": False}
        for index in range(24)
    ]
    return {
        "squares": squares,
        "stones": stones,
        "finished": False,
        "waiting": False,
        "my_turn": True,
        "boards": 1,
        "server": {"available": True, "canAction": "yes", "playerAction": "yes"},
    }


class FakeWebView:
    """Records the scripts it is given and replays a canned board."""

    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload)
        self.scripts: list[str] = []

    def __call__(self, script: str) -> str:
        self.scripts.append(script)
        if script.startswith("JSON.stringify((function(){return !window.init_game"):
            return "true"                       # only the canAction poll
        return self.payload


class BridgeTests(unittest.TestCase):
    """The JSON transport and the small helpers - no browser needed."""

    def setUp(self) -> None:
        self.view = FakeWebView(ready_payload())
        self.site = WebViewSite(self.view, log=lambda line: None)

    def test_read_state_decodes_the_json_payload(self) -> None:
        state = self.site.read_state()
        self.assertIsNotNone(state)
        self.assertEqual(len(state.squares), 32)
        self.assertEqual(len(state.stones), 24)
        self.assertTrue(state.my_turn)
        self.assertEqual(state.player_action, "yes")

    def test_read_state_returns_none_for_an_empty_page(self) -> None:
        site = WebViewSite(lambda script: "null", log=lambda line: None)
        self.assertIsNone(site.read_state())

    def test_wait_can_click_asks_the_site(self) -> None:
        self.assertTrue(self.site._wait_can_click(200))
        self.assertTrue(any("window.init_game.canAction" in s for s in self.view.scripts))

    def test_click_stone_dispatches_a_dom_click(self) -> None:
        self.assertTrue(self.site.click_stone("7"))
        script = self.view.scripts[-1]
        self.assertIn(".game-stone[stone-id=", script)
        self.assertIn("el.click()", script)

    def test_click_square_uses_the_absolute_square_id(self) -> None:
        self.assertTrue(self.site.click_square(42))
        script = self.view.scripts[-1]
        self.assertIn("item-absolute-id", script)
        self.assertIn("42", script)

    def test_a_dead_webview_becomes_a_site_error(self) -> None:
        def broken(script: str) -> str:
            raise RuntimeError("the WebView is gone")

        site = WebViewSite(broken, log=lambda line: None)
        with self.assertRaises(SiteError):
            site.read_state()

    def test_the_app_never_blocks_on_a_password(self) -> None:
        self.assertFalse(self.site.needs_sign_in())
        self.assertFalse(self.site.sign_in("user", "secret"))
        self.assertFalse(WebViewSite(lambda script: "false").has_auth_dialog())

    def test_the_rematch_offer_is_read_and_accepted(self) -> None:
        offer = '{"opponent": "42", "banner": true, "text": "rematch?"}'
        site = WebViewSite(lambda script: offer)
        self.assertEqual(site.rematch_offer()["opponent"], "42")
        site = WebViewSite(lambda script: "{}")          # nobody offered anything
        self.assertEqual(site.rematch_offer(), {})
        site = WebViewSite(lambda script: '"accepted:42"')
        self.assertEqual(site.accept_rematch(), "accepted:42")
        site = WebViewSite(lambda script: '"none"')
        self.assertEqual(site.accept_rematch(), "none")

    def test_a_disabled_stake_is_reported(self) -> None:
        site = WebViewSite(lambda script: '"disabled"', log=lambda line: None)
        self.assertFalse(site.enter_game(5))


@unittest.skipUnless(HAVE_PLAYWRIGHT, "playwright is not installed")
class MockWebViewTests(unittest.TestCase):
    """End-to-end: the shared bot plays through the JS-only bridge."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:                 # browser binaries missing
            cls.playwright.stop()
            raise unittest.SkipTest(f"chromium is not available: {error}")

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.browser.close()
        finally:
            cls.playwright.stop()

    def setUp(self) -> None:
        self.page = self.browser.new_page()
        self.page.goto(MOCK_PAGE.as_uri())
        self.site = WebViewSite(self.page.evaluate, log=lambda line: None)
        self.assertTrue(self.site.wait_for_board(timeout_ms=10000))

    def tearDown(self) -> None:
        self.page.close()

    def _new_bot(self, **overrides) -> CheckersBot:
        options = dict(
            site_color="white",
            depth=4,
            time_limit=0.5,
            poll=0.2,
            apply_fit=False,
            lobby_walk=False,
            log_dir="logs/test",
            log=lambda line: None,
        )
        options.update(overrides)
        return CheckersBot(self.site, **options)

    def _wait_until_my_turn(self, timeout: int = 20000) -> None:
        self.page.wait_for_function(
            "() => document.querySelectorAll('.game-stone[stone-clickable=\"yes\"]').length > 0",
            timeout=timeout,
        )

    def test_reads_the_mock_board_through_the_bridge(self) -> None:
        state = self.site.read_state()
        self.assertIsNotNone(state)
        self.assertEqual(len(state.squares), 32)
        self.assertEqual(len(state.stones), 24)
        self.assertEqual(state.clickable_colors(), ["white"])

    def test_the_bot_plays_moves_with_plain_dom_clicks(self) -> None:
        bot = self._new_bot()
        for move_number in range(2):
            self._wait_until_my_turn()
            move = bot.play_one_move()
            self.assertIsNotNone(move, f"move {move_number + 1} was not played")
        self.assertEqual(bot.moves_played, 2)
        clicks = [
            line for line in self.page.evaluate("() => window.__mockLog")
            if line.startswith("squareClick")
        ]
        self.assertGreaterEqual(len(clicks), 2)

    def test_the_bot_plays_a_forced_double_jump(self) -> None:
        self.page.goto(MOCK_PAGE.as_uri() + "?scenario=doublejump&forced=1")
        self.assertTrue(self.site.wait_for_board(timeout_ms=10000))
        bot = self._new_bot()
        self._wait_until_my_turn()
        move = bot.play_one_move()
        self.assertIsNotNone(move, "the forced jump was not played")
        self.assertEqual(move.captured_count, 2)

    def test_the_whole_loop_runs_without_playwright(self) -> None:
        bot = self._new_bot()
        self._wait_until_my_turn()
        code = bot.run(max_moves=1)
        self.assertEqual(code, 0)
        self.assertEqual(bot.moves_played, 1)


class _FakeBot:
    """Just enough of ``CheckersBot`` for the live-settings test."""

    class _AI:
        max_depth = 18

    def __init__(self) -> None:
        self.ai = self._AI()
        self.time_limit = 6.0
        self.soft_time = 3.0
        self.poll = 1.0
        self.rematch_wait = 30.0
        self.chat_wait = 5.0
        self.coins = 0
        self.dry_run = False
        self.trace_board = False
        self.use_probe = True
        self.accept_stake = False
        self.rematch = True


@unittest.skipIf(ios_app is None, "toga is not installed in this environment")
class IosSettingsPageTests(unittest.TestCase):
    """The settings page's settings really reach a *running* bot (no restart)."""

    def test_live_settings_are_written_into_the_bot(self):
        bot = _FakeBot()
        settings = dict(ios_app.DEFAULT_SETTINGS)
        settings.update({"strength": 12, "seconds": 8.5, "soft": 4.0, "poll": 0.5,
                         "rematch_wait": 12, "chat_wait": 3, "stake": 2,
                         "dry_run": 1, "trace": 1, "probe": 0,
                         "accept_stake": 1, "auto_rematch": 0})
        applied = ios_app.push_live_settings(bot, settings)
        self.assertEqual(bot.ai.max_depth, 12)
        self.assertEqual(bot.time_limit, 8.5)
        self.assertEqual(bot.soft_time, 4.0)
        self.assertEqual(bot.poll, 0.5)
        self.assertEqual(bot.rematch_wait, 12.0)
        self.assertEqual(bot.chat_wait, 3.0)
        self.assertEqual(bot.coins, 2)
        self.assertTrue(bot.dry_run)
        self.assertTrue(bot.trace_board)
        self.assertFalse(bot.use_probe)
        self.assertTrue(bot.accept_stake)
        self.assertFalse(bot.rematch)
        for key in ("strength", "seconds", "soft", "poll", "dry_run", "trace"):
            self.assertIn(key, applied)

    def test_restart_only_settings_are_not_pretended_to_be_live(self):
        bot = _FakeBot()
        settings = dict(ios_app.DEFAULT_SETTINGS)
        settings.update({"max_games": 5, "max_moves": 40})
        applied = ios_app.push_live_settings(bot, settings)
        self.assertNotIn("max_games", applied)
        self.assertNotIn("max_moves", applied)
        self.assertFalse(hasattr(bot, "max_games"))     # nothing was written
        for key in ios_app.RESTART_KEYS:
            self.assertTrue(ios_app.restart_only(key))

    def test_every_setting_from_the_brief_is_exposed(self):
        for key in ("strength", "seconds", "poll", "max_games", "max_moves",
                    "dry_run", "trace", "think", "lose", "accept_stake",
                    "auto_rematch", "probe", "rematch_wait", "chat_wait", "stake"):
            self.assertIn(key, ios_app.DEFAULT_SETTINGS)
        # the defaults keep the current behaviour
        self.assertEqual(ios_app.DEFAULT_SETTINGS["strength"], float(ios_app.DEPTH))
        self.assertEqual(ios_app.DEFAULT_SETTINGS["seconds"], ios_app.SECONDS)
        self.assertEqual(ios_app.DEFAULT_SETTINGS["accept_stake"], 0.0)
        self.assertEqual(ios_app.DEFAULT_SETTINGS["auto_rematch"], 1.0)

    def test_the_status_indicator_follows_the_bot(self):
        self.assertEqual(ios_app.state_text("running"), "● მუშაობს")
        self.assertEqual(ios_app.state_text("stopped"), "● გაჩერებული")
        self.assertEqual(ios_app.state_from_log("! boom"), "error")
        self.assertEqual(ios_app.state_from_log("waiting for the opponent"), "waiting")
        self.assertEqual(ios_app.state_from_log("thinking 3.0s ... (thinking)"),
                         "waiting")
        self.assertEqual(ios_app.state_from_log("our move: c3-d4"), "running")
        self.assertIsNone(ios_app.state_from_log("ლოგის ხაზი"))

    @unittest.skipUnless(os.environ.get("CHECKERSBOT_UI_TESTS") == "1",
                         "set CHECKERSBOT_UI_TESTS=1 to build the real Toga UI")
    def test_switching_pages_keeps_the_same_webview(self):
        """🎮 -> ⚙ -> 🎮 must never re-create, reload or resize the WebView."""
        try:
            app = ios_app.CheckersBotApp()
            app._build_ui()
        except Exception as error:                     # no display / app already made
            self.skipTest(f"cannot build the UI here: {error}")
        webview = app.webview
        self.assertEqual(app.stage.children[0], webview)      # full-height stage
        self.assertEqual(app.stage.style.flex, 1)
        self.assertEqual(list(app.pages.children), [app.game_page])
        app.on_show_settings()
        self.assertEqual(list(app.pages.children), [app.settings_page])
        app.on_show_game()
        self.assertEqual(list(app.pages.children), [app.game_page])
        self.assertIs(app.webview, webview)                   # same object, no reload
        self.assertEqual(app.stage.children[0], webview)
        self.assertEqual(app._page, "game")
        app.on_toggle_log()
        self.assertEqual(app.log_view.style.display, "pack")
        app.on_toggle_log()
        self.assertEqual(app.log_view.style.display, "none")


if __name__ == "__main__":
    unittest.main(verbosity=2)

