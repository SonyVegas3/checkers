"""Game-record tests (added 2026-10-27).

The record must be honest: the result comes from the site's own finish dialog
(``gf-won`` / ``gf-lost``); when the site does not say, it stays ``"unknown"`` -
an evaluation sign is not a result.  No account data may ever be written.
"""

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from checkersbot.board import BLACK, RED, Board  # noqa: E402
from checkersbot.record import GameRecord, MoveRecord, move_record, rows_of  # noqa: E402
from checkersbot.site import SiteState  # noqa: E402


class RowsTests(unittest.TestCase):
    def test_the_start_position_round_trips(self):
        rows = rows_of(Board())
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0], ".b.b.b.b")
        self.assertEqual(rows[7], "r.r.r.r.")
        self.assertEqual(Board.from_rows(rows).cells, Board().cells)
        self.assertEqual(Board.from_rows(rows, turn=BLACK).turn, BLACK)


class ResultTests(unittest.TestCase):
    def test_the_site_result_is_mapped_from_the_dialog_class(self):
        for raw, expected in (("win", "win"), ("loss", "loss"), ("draw", "draw"),
                              ("", None), (None, None), ("weird", None)):
            state = SiteState({"finish_result": raw})
            self.assertEqual(state.finish_result, expected)

    def test_an_unknown_result_stays_unknown(self):
        record = GameRecord()
        record.finish(None)
        self.assertEqual(record.result, "unknown")
        self.assertEqual(record.result_source, "unknown")

    def test_a_known_result_records_its_source(self):
        record = GameRecord()
        record.finish("win", source="site finish dialog")
        self.assertEqual(record.result, "win")
        self.assertEqual(record.result_source, "site finish dialog")


class RecordFileTests(unittest.TestCase):
    def build(self):
        board = Board.from_rows(
            ["........", "........", "........", "....b...",
             "........", "..b.....", ".r......", "........"],
            turn=RED, rules="joker",
        )
        record = GameRecord(rules="joker", our_site_color="white",
                            our_engine_color="red", depth_cap=18,
                            initial_rows=rows_of(board))
        for move in board.generate_moves()[:1]:
            record.add_move(move_record(1, board, move, budget=None, confirmed=True))
        record.mismatch("the site did not offer our move - played a site-legal one")
        record.finish("loss", source="site finish dialog")
        return record, board

    def test_the_file_has_the_documented_fields(self):
        record, board = self.build()
        data = record.to_dict()
        self.assertEqual(data["format"], "checkersbot-game/v1")
        self.assertEqual(data["rules"], "joker")
        self.assertEqual(data["our_site_color"], "white")
        self.assertEqual(data["result"], "loss")
        self.assertEqual(len(data["initial_rows"]), 8)
        move = data["moves"][0]
        for key in ("ply", "side", "move", "from", "to", "captures", "score",
                    "depth", "nodes", "seconds", "budget", "confirmed"):
            self.assertIn(key, move)
        self.assertTrue(move["captures"])            # the fixture is a capture
        self.assertTrue(data["mismatches"])

    def test_it_writes_json_and_no_account_data(self):
        record, _ = self.build()
        with tempfile.TemporaryDirectory() as folder:
            path = record.save(folder)
            text = path.read_text(encoding="utf-8")
            data = json.loads(text)
        self.assertEqual(data["result"], "loss")
        lowered = text.lower()
        for forbidden in ("password", "email", "@", "token", "cookie", "photo",
                          "user_id", "coins_win"):
            self.assertNotIn(forbidden, lowered)

    def test_the_move_record_carries_the_engine_numbers(self):
        class Stats:
            score = -222
            depth = 9
            nodes = 4242
            elapsed = 1.25
            note = "time limit"

        board = Board.from_rows(
            ["........", "........", "........", "....b...",
             "........", "..b.....", ".r......", "........"],
            turn=RED, rules="joker",
        )
        move = board.generate_moves()[0]
        entry = move_record(3, board, move, stats=Stats, confirmed=False,
                            site_note="probe disagreed")
        self.assertEqual(entry.ply, 3)
        self.assertEqual(entry.side, "red")
        self.assertEqual(entry.score, -222)
        self.assertEqual(entry.depth, 9)
        self.assertEqual(entry.nodes, 4242)
        self.assertEqual(entry.seconds, 1.25)
        self.assertEqual(entry.note, "time limit")
        self.assertFalse(entry.confirmed)
        self.assertEqual(entry.site_note, "probe disagreed")
        self.assertEqual(len(entry.captures), move.captured_count)


class BotRecordTests(unittest.TestCase):
    """The bot's own record path: start -> moves -> result -> file."""

    class State:
        def __init__(self, result):
            self.finish_result = result

    def make_bot(self, folder):
        from checkersbot.bot import CheckersBot

        return CheckersBot(site=object(), verbose=False, log_dir=folder)

    def fixture(self):
        return Board.from_rows(
            ["........", "........", "........", "....b...",
             "........", "..b.....", ".r......", "........"],
            turn=RED, rules="joker",
        )

    def test_the_record_is_written_with_the_result_the_site_reported(self):
        from checkersbot.time_manager import plan_budget

        with tempfile.TemporaryDirectory() as folder:
            bot = self.make_bot(folder)
            board = self.fixture()
            bot._start_record(board)
            bot._budget = plan_budget(60)
            move = board.generate_moves()[0]
            bot.record.add_move(move_record(1, board, move, stats=bot.ai.stats,
                                            budget=bot._budget, confirmed=True))
            bot._finish_record(self.State("win"))
            files = list(pathlib.Path(folder).glob("game_record_*.json"))
            self.assertEqual(len(files), 1)
            data = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(data["result"], "win")
        self.assertEqual(data["result_source"], "site finish dialog")
        self.assertEqual(len(data["moves"]), 1)
        self.assertEqual(len(data["initial_rows"]), 8)
        self.assertLessEqual(data["moves"][0]["budget"]["hard_s"], 60.0)
        self.assertIsNone(bot.record)

    def test_an_unknown_result_is_not_invented(self):
        with tempfile.TemporaryDirectory() as folder:
            bot = self.make_bot(folder)
            bot._start_record(self.fixture())
            bot._finish_record(self.State(None))
            files = list(pathlib.Path(folder).glob("game_record_*.json"))
            data = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(data["result"], "unknown")

    def test_a_new_game_drops_the_previous_record(self):
        bot = self.make_bot(tempfile.gettempdir())
        bot._start_record(self.fixture())
        self.assertIsNotNone(bot.record)
        bot._reset_for_new_game()
        self.assertIsNone(bot.record)


if __name__ == "__main__":
    unittest.main()
