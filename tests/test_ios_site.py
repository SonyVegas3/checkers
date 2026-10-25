"""Tests for the iOS backend of the bot: :class:`checkersbot_ios.site.WebViewSite`.

The bridge only needs an ``evaluate(script)`` callable.  The browser tests use
Playwright's Chromium as a stand-in for WKWebView, so the *real* JavaScript
(``READ_STATE_JS`` and the site's own click handlers) runs exactly as it will on
the phone - only the transport differs.  That is what makes the iOS backend
testable on a Windows machine without a Mac.
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:          # the app package, as Briefcase lays it out
    sys.path.insert(0, str(ROOT / "src"))

from checkersbot.bot import CheckersBot        # noqa: E402
from checkersbot.site import SiteError         # noqa: E402
from checkersbot_ios.site import WebViewSite   # noqa: E402

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
        self.assertFalse(self.site.enter_game(0))
        self.assertFalse(WebViewSite(lambda script: "false").has_auth_dialog())


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


if __name__ == "__main__":
    unittest.main(verbosity=2)

