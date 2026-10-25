"""Tests for the Playwright bridge in ``web_bot.py``.

The last two tests launch Chromium against ``tests/mock_checkers.html`` - a local
copy of the jokergame.cc DOM contract (same attributes/classes, same click
protocol).  They are skipped automatically when Playwright or its Chromium
build is missing.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from board import BLACK, RED, Board, parse_square, square_name  # noqa: E402
from web_bot import (  # noqa: E402
    CheckersBot,
    JokerGameSite,
    SiteState,
    first_landing,
    move_landing_path,
    site_legal_moves,
    square_from_xy,
    xy_from_square,
)

try:
    from playwright.sync_api import sync_playwright

    HAVE_PLAYWRIGHT = True
except Exception:                            # pragma: no cover
    sync_playwright = None
    HAVE_PLAYWRIGHT = False

MOCK_PAGE = pathlib.Path(__file__).with_name("mock_checkers.html")


class HelperTests(unittest.TestCase):
    def test_coordinate_mapping_round_trip(self):
        self.assertEqual(square_from_xy(1, 1), 0)
        self.assertEqual(square_from_xy(8, 8), 63)
        for index in range(64):
            self.assertEqual(square_from_xy(*xy_from_square(index)), index)

    def test_move_landing_path_for_a_double_jump(self):
        board = Board.from_rows([
            "........",
            "........",
            "........",
            "....b...",
            "........",
            "..b.....",
            ".r......",
            "........",
        ])
        moves = board.generate_moves()
        self.assertEqual(len(moves), 1)
        move = moves[0]
        self.assertEqual(move.notation(), "b2xf6")
        self.assertEqual([square_name(s) for s in move_landing_path(move)], ["d4", "f6"])

    def test_move_landing_path_for_a_quiet_move(self):
        board = Board()
        move = board.parse_move("c3-d4")
        self.assertEqual([square_name(s) for s in move_landing_path(move)], ["d4"])

    def test_first_landing_of_a_chained_jump(self):
        board = Board.from_rows([
            "........",
            "........",
            "........",
            "....b...",
            "........",
            "..b.....",
            ".r......",
            "........",
        ])
        move = board.generate_moves()[0]
        self.assertEqual(move.notation(), "b2xf6")
        self.assertEqual(square_name(first_landing(move)), "d4")
        self.assertEqual(square_name(move.to_square), "f6")

    def test_site_legal_moves_keeps_only_the_offered_destinations(self):
        board = Board()                      # seven legal opening moves
        probe = {                            # the site only offers two of them
            parse_square("c3"): [parse_square("d4")],
            parse_square("e3"): [parse_square("d4")],
        }
        moves = site_legal_moves(board, probe)
        self.assertEqual(sorted(m.notation() for m in moves), ["c3-d4", "e3-d4"])

    def test_site_legal_moves_can_force_a_capture(self):
        # a position where a jump is available: the site offering only the jump
        # leaves exactly that jump in the engine's candidate list
        board = Board.from_rows([
            "........",
            "........",
            "........",
            "....b...",
            "...r....",
            "........",
            "..b.....",
            "........",
        ])
        jump = board.generate_moves()[0]
        probe = {
            jump.from_square: [first_landing(jump)],
            parse_square("c3"): [parse_square("b4")],     # a decoy square
        }
        moves = site_legal_moves(board, probe)
        self.assertEqual([m.notation() for m in moves], ["d4xf6"])
        self.assertTrue(moves[0].captured)

    def test_site_state_parsing(self):
        raw = {
            "squares": [
                {"absolute": 1, "x": 1, "y": 1, "active": False, "playable": True},
                {"absolute": 2, "x": 2, "y": 1, "active": True, "playable": True},
                {"absolute": 10, "x": 2, "y": 2, "active": False, "playable": True},
            ],
            "stones": [
                {"id": "7", "absolute": 2, "color": "white", "king": True,
                 "clickable": True, "highlighted": False},
                {"id": "8", "absolute": 10, "color": "black", "king": False,
                 "clickable": False, "highlighted": True},
            ],
            "finished": False,
            "waiting": False,
            "my_turn": True,
        }
        state = SiteState(raw)
        self.assertFalse(state.is_ready())          # only three squares in the sample
        self.assertEqual(state.active_squares(), [square_from_xy(2, 1)])
        self.assertEqual(state.clickable_colors(), ["white"])
        self.assertEqual(state.highlighted_colors(), ["black"])
        self.assertEqual(state.absolute_for(square_from_xy(2, 2)), 10)
        self.assertEqual(state.stone_at(square_from_xy(2, 1))["id"], "7")
        self.assertEqual(state.stone_at(square_from_xy(1, 1)), None)


@unittest.skipUnless(HAVE_PLAYWRIGHT, "playwright is not installed")
class MockBoardIntegrationTests(unittest.TestCase):
    """End-to-end: the bot reads the DOM, picks a move and clicks it."""

    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:           # browser binaries missing
            cls.playwright.stop()
            raise unittest.SkipTest(f"chromium is not available: {error}")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
        finally:
            cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.goto(MOCK_PAGE.as_uri())
        self.site = JokerGameSite(self.page)
        self.assertTrue(self.site.wait_for_board(timeout_ms=10000))

    def tearDown(self):
        self.page.close()

    def test_reads_the_mock_board(self):
        state = self.site.read_state()
        self.assertIsNotNone(state)
        self.assertEqual(len(state.squares), 32)
        self.assertEqual(len(state.stones), 24)
        self.assertEqual(state.clickable_colors(), ["white"])
        self.assertEqual(state.active_squares(), [])

    def _wait_until_my_turn(self, timeout: int = 20000) -> None:
        self.page.wait_for_function(
            "() => document.querySelectorAll('.game-stone[stone-clickable=\"yes\"]').length > 0",
            timeout=timeout,
        )

    def _new_bot(self, **overrides) -> CheckersBot:
        options = dict(
            site_color="white",
            depth=4,
            time_limit=1.0,
            poll=0.2,
            log_dir="logs/test",
        )
        options.update(overrides)
        return CheckersBot(self.site, **options)

    def test_bot_plays_moves_against_the_scripted_opponent(self):
        bot = self._new_bot()
        for move_number in range(3):
            self._wait_until_my_turn()
            move = bot.play_one_move()
            self.assertIsNotNone(move, f"move {move_number + 1} was not played")
            state = self.site.read_state()
            self.assertIsNotNone(state)
            stone = state.stone_at(move.from_square)
            self.assertFalse(
                stone is not None and stone["color"] == "white",
                f"the stone is still standing on {square_name(move.from_square)}",
            )
        self.assertEqual(bot.moves_played, 3)

    def test_bot_calibrates_the_orientation_on_a_flipped_board(self):
        # A rotated board must not break the bot: the site's own answers tell it
        # that its white men sit on top and therefore move downwards (engine BLACK).
        self.page.goto(MOCK_PAGE.as_uri() + "?flip=1")
        self.assertTrue(self.site.wait_for_board(timeout_ms=10000))
        bot = self._new_bot()
        self._wait_until_my_turn()
        move = bot.play_one_move()
        self.assertIsNotNone(move, "the bot did not play on the flipped board")
        self.assertEqual(bot.my_engine_color, BLACK)
        self.assertEqual(bot.moves_played, 1)

    def test_bot_plays_a_forced_double_jump(self):
        # ?forced=1 makes the mock enforce mandatory captures, and the scenario
        # only allows b2xc3xd4xe5xf6, i.e. two landings clicked in one turn.
        self.page.goto(MOCK_PAGE.as_uri() + "?scenario=doublejump&forced=1")
        self.assertTrue(self.site.wait_for_board(timeout_ms=10000))
        black_before = self.page.evaluate(
            "() => stones.filter((s) => s.color === 'black').length"
        )
        bot = self._new_bot()
        self._wait_until_my_turn()
        move = bot.play_one_move()
        self.assertIsNotNone(move, "the forced jump was not played")
        self.assertEqual(move.captured_count, 2)
        self.assertEqual([square_name(s) for s in move_landing_path(move)], ["d4", "f6"])
        black_after = self.page.evaluate(
            "() => stones.filter((s) => s.color === 'black').length"
        )
        self.assertEqual(black_after, black_before - 2)
        clicks = [
            line
            for line in self.page.evaluate("() => window.__mockLog")
            if line.startswith("squareClick")
        ]
        self.assertGreaterEqual(len(clicks), 2, "both jump landings must be clicked")


if __name__ == "__main__":
    unittest.main(verbosity=2)
