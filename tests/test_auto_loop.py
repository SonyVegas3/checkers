"""Tests for the continuous game loop: rematch, then a new opponent.

``CheckersBot._next_game`` is driven through a fake ``Site`` so the real
behaviour is covered without a browser: a rematch that is accepted keeps the
same table, a refused one leaves for the lobby, and ``lobby_walk=False`` stops
after the game.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkersbot.board import RED            # noqa: E402
from checkersbot.bot import CheckersBot      # noqa: E402
from checkersbot.site import SiteState       # noqa: E402


def _raw(finished: bool, step: int, my_turn: bool = True,
         opponent_coins: int = 0, opponent_chat: str = "no") -> dict:
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
        },
    }


class FakeSite:
    """Just enough of the Site interface for the game-loop tests."""

    def __init__(self, states, rematch="requested:42") -> None:
        self.states = list(states)
        self.rematch = rematch
        self.lobby_opened = False
        self.games_entered = 0
        self.coin_accepts = 0
        self.chat_declines = 0

    def accept_coin_offer(self) -> str:
        self.coin_accepts += 1
        return "clicked"

    def decline_chat_offer(self) -> str:
        self.chat_declines += 1
        return "clicked"

    def read_state(self):
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]

    def request_rematch(self) -> str:
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
        site = FakeSite([finished], rematch="none")
        bot = self._bot(site)

        self.assertTrue(bot._next_game(finished))
        self.assertTrue(site.lobby_opened)
        self.assertGreaterEqual(site.games_entered, 1)

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
        import time

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
