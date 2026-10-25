"""Tests for the continuous game loop: rematch, then a new opponent.

``CheckersBot._next_game`` is driven through a fake ``Site`` so the real
behaviour is covered without a browser: a rematch that is accepted keeps the
same table, a refused one leaves for the lobby, and ``lobby_walk=False`` stops
after the game.
"""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkersbot.board import RED, Board     # noqa: E402
from checkersbot.bot import CheckersBot      # noqa: E402
from checkersbot.site import SiteState       # noqa: E402


def _raw(finished: bool, step: int, my_turn: bool = True,
         opponent_coins: int = 0, opponent_chat: str = "no",
         opponent_draw: bool = False) -> dict:
    """A board as READ_STATE_JS returns it (ready: 32 squares, 24 stones)."""
    squares = []
    for index in range(64):
        x, y = index % 8 + 1, index // 8 + 1
        if (x + y) % 2 == 0:
            continue
        squares.append({"absolute": index, "x": x, "y": y,
                        "active": False, "playable": True})
    stones = [
        {"id": str(index), "absolute": squares[index]["absolute"], "color": "white",
         "king": False, "clickable": my_turn, "highlighted": False}
        for index in range(24)
    ]
    return {
        "squares": squares,
        "stones": stones,
        "finished": False,
        "waiting": False,
        "my_turn": my_turn,
        "boards": 1,
        "server": {
            "available": True,
            "canAction": "yes",
            "status": "finish" if finished else "play",
            "playerAction": "yes" if my_turn else "no",
            "step": step,
            "playerChat": "no",
            "opponentChat": opponent_chat,
            "opponentCoins": opponent_coins,
            "opponentDraw": opponent_draw,
        },
    }


class FakeSite:
    """Just enough of the Site interface for the game-loop tests."""

    def __init__(self, states, rematch="requested:42", search="none",
                 ready_after=0) -> None:
        self.states = list(states)
        self.rematch = rematch
        self.search = search
        self.ready_after = ready_after
        self.reads = 0
        self.lobby_opened = False
        self.games_entered = 0
        self.coin_accepts = 0
        self.chat_declines = 0
        self.draw_accepts = 0
        self.rematch_requests = 0
        self.searches_cancelled = 0

    def accept_coin_offer(self) -> str:
        self.coin_accepts += 1
        return "clicked"

    def accept_draw_offer(self) -> str:
        self.draw_accepts += 1
        return "clicked"

    def decline_chat_offer(self) -> str:
        self.chat_declines += 1
        return "clicked"

    def read_state(self):
        self.reads += 1
        if self.reads <= self.ready_after:
            return None
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]

    def search_status(self) -> str:
        return self.search

    def cancel_search(self) -> str:
        self.searches_cancelled += 1
        self.search = "none"
        return "clicked"

    def request_rematch(self) -> str:
        self.rematch_requests += 1
        return self.rematch

    def open_lobby(self) -> bool:
        self.lobby_opened = True
        return True

    def enter_game(self, coins: int = 0) -> bool:
        self.games_entered += 1
        return True

    def wait_for_board(self, timeout_ms: int = 90000) -> bool:
        return True

    def fit_view(self, margin: int = 10):
        return {"vw": 400, "vh": 800, "board": 400, "needed": 700, "fits": True}

    # unused in these tests, but part of the interface
    def _wait_can_click(self, timeout_ms: int = 2500) -> bool:
        return True

    def clear_highlights(self) -> None:
        pass

    def click_stone(self, stone_id: str, wait_response: bool = True) -> bool:
        return True

    def click_square(self, absolute: int, wait_response: bool = True) -> bool:
        return True

    def wait_for_active(self, timeout_ms: int = 8000) -> bool:
        return True

    def screenshot(self, path) -> None:
        pass

    def dump_html(self, path) -> None:
        pass

    def has_auth_dialog(self) -> bool:
        return False

    def needs_sign_in(self) -> bool:
        return False

    def sign_in(self, email: str, password: str) -> bool:
        return False


class AutoLoopTests(unittest.TestCase):
    def _bot(self, site, **overrides) -> CheckersBot:
        options = dict(
            site_color="auto",
            side="auto",
            depth=2,
            time_limit=None,
            poll=0.1,
            rematch_wait=1.0,
            log=lambda line: None,
            log_dir="logs/test",
        )
        options.update(overrides)
        return CheckersBot(site, **options)

    def test_an_accepted_rematch_keeps_the_table(self):
        finished = SiteState(_raw(True, 40))
        fresh = SiteState(_raw(False, 2))
        site = FakeSite([finished, fresh, fresh, fresh])
        bot = self._bot(site, rematch_wait=2.0)
        bot.calibrated = True
        bot.my_engine_color = RED

        self.assertTrue(bot._next_game(finished))
        self.assertEqual(bot.games_played, 1)
        self.assertFalse(site.lobby_opened)
        # a rematch can swap the colours, so the side is calibrated again
        self.assertFalse(bot.calibrated)
        self.assertIsNone(bot.my_engine_color)

    def test_without_a_rematch_a_new_opponent_is_sought(self):
        finished = SiteState(_raw(True, 40))
        fresh = SiteState(_raw(False, 1))
        site = FakeSite([finished, fresh], rematch="none")
        bot = self._bot(site)

        self.assertTrue(bot._next_game(finished))
        self.assertTrue(site.lobby_opened)

    def test_no_lobby_walk_stops_after_the_game(self):
        finished = SiteState(_raw(True, 40))
        site = FakeSite([finished], rematch="none")
        bot = self._bot(site, lobby_walk=False)

        self.assertFalse(bot._next_game(finished))
        self.assertFalse(site.lobby_opened)

    def test_run_stops_when_max_games_is_reached(self):
        finished = SiteState(_raw(True, 40))
        site = FakeSite([finished], rematch="none")
        bot = self._bot(site, lobby_walk=False)
        bot.games_played = 1
        self.assertEqual(bot.run(max_games=1), 0)

    def test_a_bigger_stake_is_accepted_once(self):
        offered = SiteState(_raw(False, 10, opponent_coins=5000))
        site = FakeSite([offered])
        bot = self._bot(site)

        self.assertFalse(bot._answer_offers(offered))      # answered, play on
        self.assertEqual(site.coin_accepts, 1)
        self.assertEqual(bot._coin_offer_answered, 5000)
        # the server may keep the amount for a moment: do not answer twice
        self.assertFalse(bot._answer_offers(offered))
        self.assertEqual(site.coin_accepts, 1)

    def test_a_chat_invitation_is_declined_after_the_delay(self):
        chat = SiteState(_raw(False, 10, opponent_chat="yes"))
        site = FakeSite([chat])
        bot = self._bot(site)

        # first sight: the bot waits (so you can accept it yourself)
        self.assertTrue(bot._answer_offers(chat))
        self.assertEqual(site.chat_declines, 0)
        # after the delay it declines and keeps playing
        bot._chat_offer_since = time.time() - bot.chat_wait - 0.1
        self.assertTrue(bot._answer_offers(chat))
        self.assertEqual(site.chat_declines, 1)
        self.assertIsNone(bot._chat_offer_since)


    def test_the_lobby_walk_opens_a_table_quickly(self):
        ready = SiteState(_raw(False, 1))
        site = FakeSite([ready], ready_after=4)
        bot = self._bot(site)
        started = time.time()
        self.assertTrue(bot._enter_lobby_game(timeout=15))
        self.assertLess(time.time() - started, 5.0)
        self.assertGreaterEqual(site.games_entered, 1)

    def test_a_failed_opponent_search_is_cancelled(self):
        ready = SiteState(_raw(False, 1))
        site = FakeSite([ready], search="error", ready_after=4)
        bot = self._bot(site)
        self.assertTrue(bot._enter_lobby_game(timeout=15))
        self.assertGreaterEqual(site.searches_cancelled, 1)


    def test_rank_moves_orders_every_move(self):
        board = Board()
        bot = self._bot(FakeSite([SiteState(_raw(False, 1))]))
        ranked = bot.ai.rank_moves(board, depth=3)
        self.assertEqual(len(ranked), len(board.generate_moves()))
        scores = [score for score, _ in ranked]
        self.assertEqual(scores, sorted(scores))          # worst first

    def test_the_weak_move_is_legal_and_not_a_blunder(self):
        board = Board()
        site = FakeSite([SiteState(_raw(False, 1))])
        bot = self._bot(site, should_lose=lambda: True)
        self.assertTrue(bot._lose_requested())

        legal = board.generate_moves()
        ranked = bot.ai.rank_moves(board, depth=4)          # worst first
        best = ranked[-1][0]
        score_of = {str(move): value for value, move in ranked}
        picks = set()
        for _ in range(8):
            weak = bot._choose_weak_move(board, legal)
            self.assertIsNotNone(weak)
            self.assertIn(weak, legal)
            # an average player misses the best move but does not give a piece
            # away: every pick stays within the "medium" window of the best one
            self.assertGreaterEqual(score_of[str(weak)], best - bot.MEDIUM_WINDOW)
            picks.add(str(weak))
        self.assertGreater(len(picks), 0)

    def test_losing_is_off_by_default(self):
        bot = self._bot(FakeSite([SiteState(_raw(False, 1))]))
        self.assertFalse(bot._lose_requested())

    def test_a_draw_offer_is_agreed_at_once(self):
        offered = SiteState(_raw(False, 12, opponent_draw=True))
        site = FakeSite([offered])
        bot = self._bot(site)

        # the dialog covers the board and the clock runs: agree and wait
        self.assertTrue(bot._answer_offers(offered))
        self.assertEqual(site.draw_accepts, 1)
        self.assertTrue(bot._draw_offer_answered)
        # the server flag may linger a moment: never click twice, and do not keep
        # blocking the loop - the game is over now, the rematch logic takes over
        self.assertFalse(bot._answer_offers(offered))
        self.assertEqual(site.draw_accepts, 1)

    def test_a_draw_offer_is_answered_while_the_engine_thinks(self):
        board = Board()
        offered = SiteState(_raw(False, 12, opponent_draw=True))
        site = FakeSite([offered])
        bot = self._bot(site, depth=6, time_limit=1.0)

        move = bot._search_move(board)
        self.assertIsNotNone(move)
        self.assertGreaterEqual(site.draw_accepts, 1)

    def test_a_new_draw_offer_is_answered_after_the_old_one_cleared(self):
        offered = SiteState(_raw(False, 12, opponent_draw=True))
        cleared = SiteState(_raw(False, 13))
        site = FakeSite([offered, cleared])
        bot = self._bot(site)

        self.assertTrue(bot._answer_offers(offered))
        self.assertEqual(site.draw_accepts, 1)
        self.assertFalse(bot._answer_offers(cleared))       # nothing pending
        self.assertFalse(bot._draw_offer_answered)
        self.assertTrue(bot._answer_offers(offered))
        self.assertEqual(site.draw_accepts, 2)

    def test_an_unclickable_draw_offer_does_not_stall_the_bot(self):
        offered = SiteState(_raw(False, 12, opponent_draw=True))
        site = FakeSite([offered])
        site.accept_draw_offer = lambda: "none"             # dialog not found
        bot = self._bot(site)

        for _ in range(14):
            self.assertTrue(bot._answer_offers(offered))
        # after 15 tries the bot stops waiting so the game cannot hang forever
        self.assertFalse(bot._answer_offers(offered))

    def test_the_bot_offers_a_rematch_itself(self):
        finished = SiteState(_raw(True, 40))
        fresh = SiteState(_raw(False, 2))
        site = FakeSite([finished, fresh, fresh, fresh])
        bot = self._bot(site, rematch_wait=2.0)
        lines = []
        bot._log = lines.append

        self.assertTrue(bot._next_game(finished))
        self.assertGreaterEqual(site.rematch_requests, 1)
        self.assertTrue(any("rematch offered" in line for line in lines), lines)


    def test_an_offer_is_answered_while_the_engine_thinks(self):
        board = Board()
        offered = SiteState(_raw(False, 10, opponent_coins=5000))
        site = FakeSite([offered])
        bot = self._bot(site, depth=6, time_limit=1.0)

        move = bot._search_move(board)              # the search runs in a thread
        self.assertIsNotNone(move)
        self.assertIn(move, board.generate_moves())
        # the page is polled while the engine thinks, so the 10 s stake dialog
        # is answered instead of expiring
        self.assertEqual(site.coin_accepts, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
