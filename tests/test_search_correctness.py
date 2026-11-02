"""Correctness audit of the rules/search boundary (added 2026-10-27).

Each test pins one behaviour the automatic play depends on:

* capture chains: a taken piece leaves the board at once, no piece is taken twice,
  a chain is finished where it can continue, and **the longest capture is not
  compulsory** - every completed path is legal (JokerGame); a man crowned inside a
  capture keeps going;
* ``make_move`` / ``unmake_move`` restore the board and the side to move and
  leave the caller's repetition history alone - also after a clock cut-off;
* repetition: the search scores an already-seen position as a draw, while the
  rules themselves have no draw (the site decides that);
* the transposition table is cleared between moves and never stores a value that
  came from a repetition cutoff;
* quiescence really follows a forced capture at the horizon.

No browser, no network: positions are plain boards.
"""

import unittest

from checkersbot.ai import INF, CheckersAI, evaluate
from checkersbot.board import BLACK, PLAYABLE_SQUARES, RED, Board


def board(rows, turn=RED, rules="joker"):
    return Board.from_rows(rows, turn=turn, rules=rules)


class CaptureChainTests(unittest.TestCase):
    def test_a_taken_piece_stays_on_the_board_until_the_move_ends(self):
        start = board([
            "........",
            "........",
            "........",
            "....b...",      # e5
            "........",
            "..R.....",      # c3 takes e5
            "........",
            "........",
        ])
        move = start.generate_moves()[0]
        self.assertEqual(move.captured_count, 1)
        for landing in move.landing_path:
            self.assertNotIn(landing, move.captured)

    def test_the_same_piece_can_never_be_taken_twice(self):
        start = board([
            "........",
            "........",
            "........",
            "...b....",      # d6
            "........",
            "........",
            "..R.....",      # c3
            "........",
        ])
        for move in start.generate_moves():
            self.assertEqual(len(set(move.captured)), len(move.captured))
            self.assertEqual(move.captured_count, len(move.captured))

    def test_a_crowned_man_may_continue_but_nothing_is_forced_by_length(self):
        start = board([
            "........",
            "..b.b...",      # c7, e7
            ".....r..",      # f6
            "........",
            "........",
            "........",
            "........",
            "........",
        ])
        moves = start.generate_moves()
        self.assertEqual(sorted(m.notation() for m in moves), ["f6xa5", "f6xb6"])
        # both routes take both men and both are legal - no length filter exists
        self.assertTrue(all(m.captured_count == 2 for m in moves))
        self.assertEqual({len(m.captured) for m in moves}, {2})

    def test_the_shorter_path_survives_a_longer_alternative(self):
        # the position of the report: b2 → d4 → b6 (2 pieces) next to
        # b2 → d4 → f2 → h4 (3 pieces)
        start = board([
            ".....b..",
            "......b.",
            ".....b.b",
            "..b...b.",
            "........",
            "..b.b.b.",
            ".r......",
            "........",
        ])
        moves = {m.notation(): m for m in start.generate_moves()}
        self.assertEqual(sorted(moves), ["b2xb6", "b2xh4"])
        self.assertEqual(moves["b2xb6"].captured_count, 2)
        self.assertEqual(moves["b2xh4"].captured_count, 3)
        # the chain cannot be stopped on d4, where two continuations exist
        self.assertNotIn("b2xd4", moves)

    def test_a_crowned_man_keeps_capturing_as_a_king(self):
        start = board([
            "........",
            "..b.b.b.",      # c7, e7, g7
            ".....r..",      # f6 jumps e7, is crowned on d8 and continues
            "........",
            "........",
            "........",
            "........",
            "........",
        ])
        moves = start.generate_moves()
        self.assertTrue(moves)
        best = max(moves, key=lambda m: m.captured_count)
        self.assertGreaterEqual(best.captured_count, 2)
        self.assertTrue(best.promotes)
        after = start.copy()
        after.apply_move(best)
        self.assertEqual(after.piece_count(BLACK),
                         start.piece_count(BLACK) - best.captured_count)


class MakeUnmakeTests(unittest.TestCase):
    ROWS = [
        ["........", "..b.b...", ".....r..", "........",
         "........", "........", "........", "........"],
        ["...b...b", "b.......", "...b...b", "..b...b.",
         ".....r..", "..r.....", ".......r", "r.r.r.r."],
    ]

    def test_every_move_round_trips(self):
        for rows in self.ROWS:
            start = board(rows)
            for move in start.generate_moves():
                work = start.copy()
                undo = work.make_move(move)
                self.assertEqual(work.turn, -start.turn)
                work.unmake_move(move, undo)
                self.assertEqual(work.cells, start.cells, move.notation())
                self.assertEqual(work.turn, start.turn, move.notation())
                self.assertEqual(work.position_key(), start.position_key())

    def test_a_search_never_mutates_the_caller_board(self):
        start = board(self.ROWS[1])
        snapshot = start.copy()
        ai = CheckersAI(max_depth=4, time_limit=None)
        move = ai.choose_move(start)
        self.assertIn(move, snapshot.generate_moves())
        self.assertEqual(start.cells, snapshot.cells)
        self.assertEqual(start.turn, snapshot.turn)

    def test_a_timed_out_search_restores_the_board(self):
        start = board(self.ROWS[1])
        snapshot = start.copy()
        ai = CheckersAI(max_depth=60, time_limit=0.05)
        move = ai.choose_move(start)
        self.assertIsNotNone(move)
        self.assertIn(move, snapshot.generate_moves())
        self.assertEqual(start.cells, snapshot.cells)
        self.assertEqual(start.turn, snapshot.turn)
        self.assertEqual(ai.stats.note, "time limit")

    def test_the_caller_history_is_not_modified(self):
        start = board(self.ROWS[1])
        history = [start.position_key()]
        before = list(history)
        CheckersAI(max_depth=3, time_limit=None).choose_move(start, history=history)
        self.assertEqual(history, before)


class RepetitionTests(unittest.TestCase):
    ROWS = MakeUnmakeTests.ROWS[1]

    def test_a_seen_position_is_scored_as_a_draw_inside_the_search(self):
        start = board(self.ROWS)
        ai = CheckersAI(max_depth=4, time_limit=None)
        ai._deadline = float("inf")
        ai._history = None
        plain = ai._negamax(start.copy(), 0, -INF, INF, 0)
        ai._history = {start.position_key()}
        repeated = ai._negamax(start.copy(), 0, -INF, INF, 0)
        self.assertEqual(repeated, 0)
        self.assertNotEqual(plain, 0)

    def test_the_rules_themselves_have_no_draw(self):
        start = board(self.ROWS)
        self.assertTrue(start.generate_moves())
        self.assertFalse(start.is_terminal())
        self.assertIsNone(start.winner())
        self.assertFalse(hasattr(start, "is_draw"))

    def test_a_repetition_cutoff_is_not_stored_in_the_table(self):
        start = board(self.ROWS)
        ai = CheckersAI(max_depth=6, time_limit=None)
        ai._deadline = float("inf")
        ai._history = {start.position_key()}
        ai._table.clear()
        self.assertEqual(ai._negamax(start.copy(), 3, -INF, INF, 0), 0)
        self.assertEqual(ai._table, {})

    def test_the_table_is_cleared_between_moves(self):
        start = board(self.ROWS)
        ai = CheckersAI(max_depth=5, time_limit=None)
        ai.choose_move(start)
        self.assertTrue(ai._table)
        ai._table[("stale", 0)] = (1, 0, 0, None)
        ai.choose_move(start)
        self.assertNotIn(("stale", 0), ai._table)


class QuiescenceTests(unittest.TestCase):
    ROWS = ["........", "..b.b...", ".....r..", "........",
            "........", "........", "........", "........"]

    def test_quiescence_follows_a_forced_capture_at_the_horizon(self):
        start = board(self.ROWS)
        static = evaluate(start)
        ai = CheckersAI(quiescence_depth=0)
        ai._deadline = float("inf")
        without = ai._negamax(start.copy(), 0, -INF, INF, 0)
        ai = CheckersAI(quiescence_depth=12)
        ai._deadline = float("inf")
        with_q = ai._negamax(start.copy(), 0, -INF, INF, 0)
        self.assertEqual(without, static * start.turn)   # no search happened
        self.assertGreater(with_q, without)              # the capture is seen

    def test_a_quiet_position_is_evaluated_statically(self):
        start = board(MakeUnmakeTests.ROWS[1])
        ai = CheckersAI(quiescence_depth=12)
        ai._deadline = float("inf")
        self.assertEqual(ai._negamax(start.copy(), 0, -INF, INF, 0),
                         evaluate(start) * start.turn)

    def test_the_horizon_does_not_hide_a_forced_capture(self):
        start = board(self.ROWS)
        shallow = CheckersAI(max_depth=1, time_limit=None).choose_move(start)
        deep = CheckersAI(max_depth=4, time_limit=None).choose_move(start)
        self.assertIsNotNone(shallow)
        self.assertEqual(shallow.notation(), deep.notation())
        self.assertGreater(deep.captured_count, 0)


if __name__ == "__main__":
    unittest.main()

