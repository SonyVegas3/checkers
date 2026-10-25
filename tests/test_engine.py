"""Unit tests for the checkers rules engine and the search.

Run from the project root::

    python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai import MATE_THRESHOLD, CheckersAI, evaluate
from board import (
    BLACK,
    BLACK_MAN,
    RED,
    RED_KING,
    RED_MAN,
    Board,
    parse_square,
    square_name,
)


def _rotate_and_swap(board: Board) -> Board:
    """Rotate the position 180 degrees and swap the colours."""
    cells = [0] * 64
    for square in range(64):
        piece = board.cells[square]
        row, col = divmod(square, 8)
        cells[(7 - row) * 8 + (7 - col)] = -piece if piece else 0
    return Board(cells, -board.turn)


class BoardRulesTests(unittest.TestCase):
    def test_starting_position(self):
        board = Board()
        self.assertEqual(board.piece_count(RED), 12)
        self.assertEqual(board.piece_count(BLACK), 12)
        self.assertEqual(board.king_count(), 0)
        self.assertEqual(board.turn, RED)
        self.assertEqual(len(board.generate_moves()), 7)  # the 7 standard openings
        self.assertFalse(board.is_terminal())

    def test_perft_matches_known_english_draughts_values(self):
        board = Board()
        snapshot = board.copy()
        for depth, nodes in ((1, 7), (2, 49), (3, 302), (4, 1469), (5, 7361)):
            with self.subTest(depth=depth):
                self.assertEqual(board.perft(depth), nodes)
        self.assertEqual(board, snapshot)  # perft must leave the board untouched

    def test_capturing_is_mandatory(self):
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
        moves = board.generate_moves()
        self.assertEqual(len(moves), 1)
        self.assertTrue(moves[0].is_capture)
        self.assertEqual(moves[0].notation(), "d4xf6")
        # d4-c5 would be a quiet move, but the jump takes priority
        self.assertNotIn("d4-c5", [m.notation() for m in moves])

    def test_multi_jump_is_a_single_move(self):
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
        self.assertEqual(len(move.captured), 2)
        board.apply_move(move)
        self.assertEqual(board.piece_count(BLACK), 0)
        self.assertEqual(board.piece_at(parse_square("f6")), RED_MAN)

    def test_promotion_ends_the_jump(self):
        board = Board.from_rows([
            "........",
            "..b.b...",
            ".r......",
            "........",
            "........",
            "........",
            "........",
            "........",
        ])
        moves = board.generate_moves()
        self.assertEqual(len(moves), 1)
        move = moves[0]
        self.assertTrue(move.promotes)
        self.assertEqual(len(move.captured), 1)
        self.assertEqual(square_name(move.to_square), "d8")
        board.apply_move(move)
        self.assertEqual(board.piece_at(parse_square("d8")), RED_KING)
        self.assertEqual(board.turn, BLACK)

    def test_quiet_step_promotes(self):
        board = Board.from_rows([
            "........",
            "..r.....",
            "........",
            "........",
            "........",
            "........",
            ".b......",
            "........",
        ])
        move = board.parse_move("c7-d8")
        self.assertTrue(move.promotes)
        board.apply_move(move)
        self.assertEqual(board.piece_at(parse_square("d8")), RED_KING)

    def test_make_and_unmake_roundtrip(self):
        positions = [
            Board(),
            Board.from_rows([
                "........",
                "........",
                "........",
                "....b...",
                "........",
                "..b.....",
                ".r......",
                "........",
            ]),
        ]
        for board in positions:
            for move in board.generate_moves():
                snapshot = board.copy()
                undo = board.make_move(move)
                self.assertNotEqual(board, snapshot)
                board.unmake_move(move, undo)
                self.assertEqual(board, snapshot)

    def test_winner_detection(self):
        board = Board.from_rows([
            "........",
            "........",
            "........",
            "....b...",
            "...r....",
            "........",
            "........",
            "........",
        ])
        self.assertIsNone(board.winner())
        board.apply_move(board.parse_move("d4xf6"))
        self.assertEqual(board.piece_count(BLACK), 0)
        self.assertTrue(board.is_terminal())
        self.assertEqual(board.winner(), RED)

    def test_men_cannot_jump_backwards(self):
        board = Board.from_rows([
            "........",
            "........",
            "........",
            "....r...",
            "...b....",
            "........",
            "........",
            "........",
        ], turn=BLACK)
        moves = board.generate_moves()
        self.assertTrue(moves)
        self.assertTrue(all(not move.captured for move in moves))

    def test_kings_jump_in_every_direction(self):
        board = Board.from_rows([
            "........",
            "........",
            "........",
            "....b...",
            "...R....",
            "..b.....",
            "........",
            "........",
        ])
        moves = board.generate_moves()
        self.assertEqual(sorted(m.notation() for m in moves), ["d4xb2", "d4xf6"])

    def test_king_multi_jump(self):
        board = Board.from_rows([
            "........",
            "......b.",
            "........",
            "....b...",
            "...R....",
            "........",
            "........",
            "........",
        ])
        moves = board.generate_moves()
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0].notation(), "d4xh8")
        self.assertEqual(len(moves[0].captured), 2)

    def test_parse_move_accepts_loose_notation(self):
        board = Board()
        self.assertEqual(board.parse_move("c3-d4").notation(), "c3-d4")
        self.assertEqual(board.parse_move("C3 D4").notation(), "c3-d4")
        self.assertEqual(board.parse_move("c3xd4").notation(), "c3-d4")
        with self.assertRaises(ValueError):
            board.parse_move("a1-c3")
        with self.assertRaises(ValueError):
            board.parse_move("hello")


class RussianRulesTests(unittest.TestCase):
    """Russian draughts (``damka``): what jokergame.cc actually plays."""

    def test_kings_fly_along_the_whole_diagonal(self):
        board = Board.from_rows([
            ".b......",
            "........",
            "........",
            "........",
            "...R....",
            "........",
            "........",
            "........",
        ], turn=BLACK, rules="russian")
        # the site forces captures, so ask for the quiet moves explicitly
        targets = sorted(square_name(m.to_square) for m in board.quiet_moves(RED))
        self.assertEqual(targets, [
            "a1", "a7", "b2", "b6", "c3", "c5", "e3", "e5", "f2", "f6", "g1", "g7", "h8",
        ])

    def test_english_kings_still_step_one_square(self):
        board = Board.from_rows([
            ".b......",
            "........",
            "........",
            "........",
            "...R....",
            "........",
            "........",
            "........",
        ], turn=BLACK, rules="english")
        targets = sorted(square_name(m.to_square) for m in board.quiet_moves(RED))
        self.assertEqual(targets, ["c3", "c5", "e3", "e5"])

    def test_flying_king_captures_from_a_distance(self):
        # the exact position of the live game where the site's king swept two men
        rows = [
            "...b...b",
            "b.b.....",
            ".....r.b",
            "....r...",
            ".......r",
            "....r...",
            "........",
            "r.B.....",
        ]
        board = Board.from_rows(rows, turn=BLACK, rules="russian")
        moves = {(m.notation(), tuple(square_name(s) for s in m.captured),
                  tuple(square_name(s) for s in m.landing_path))
                 for m in board.generate_moves()}
        self.assertEqual(moves, {
            ("c1xd6", ("e3", "e5"), ("f4", "d6")),
            ("c1xe7", ("e3", "f6"), ("g5", "e7")),
        })

    def test_english_rules_cannot_reach_that_capture(self):
        rows = [
            "...b...b",
            "b.b.....",
            ".....r.b",
            "....r...",
            ".......r",
            "....r...",
            "........",
            "r.B.....",
        ]
        board = Board.from_rows(rows, turn=BLACK, rules="english")
        notations = {m.notation() for m in board.generate_moves()}
        self.assertNotIn("c1xe7", notations)
        self.assertNotIn("c1xd6", notations)

    def test_flying_king_may_land_on_any_square_behind(self):
        board = Board.from_rows([
            "........",
            "........",
            ".....b..",
            "........",
            "...R....",
            "........",
            "........",
            "........",
        ], rules="russian")
        moves = board.generate_moves()
        landings = sorted(square_name(m.to_square) for m in moves)
        # behind f6 the diagonal continues to g7 and h8
        self.assertEqual(landings, ["g7", "h8"])
        self.assertEqual({len(m.captured) for m in moves}, {1})

    def test_king_cannot_pass_over_a_piece_it_already_took(self):
        board = Board.from_rows([
            "........",
            "........",
            ".....b..",
            "........",
            "...R....",
            "........",
            "........",
            "........",
        ], rules="russian")
        for move in board.generate_moves():
            self.assertEqual(len(set(move.captured)), len(move.captured),
                             f"{move.notation()} takes a piece twice")

    def test_men_still_capture_forwards_only(self):
        board = Board.from_rows([
            "........",
            "........",
            "........",
            "........",
            "...r....",
            "..b.....",
            "........",
            "........",
        ], turn=RED, rules="russian")
        # the black man on c3 sits behind the red man on d4: no backward capture
        self.assertEqual(sorted(m.notation() for m in board.generate_moves()),
                         ["d4-c5", "d4-e5"])

    def test_promotion_ends_the_move_under_russian_rules(self):
        board = Board.from_rows([
            "........",
            "..b.....",
            ".r......",
            "........",
            "........",
            "........",
            "........",
            "........",
        ], turn=RED, rules="russian")
        moves = board.generate_moves()
        self.assertEqual([m.notation() for m in moves], ["b6xd8"])
        self.assertTrue(moves[0].promotes)
        self.assertEqual([square_name(s) for s in moves[0].captured], ["c7"])
        self.assertEqual([square_name(s) for s in moves[0].landing_path], ["d8"])


class BrazilianRulesTests(unittest.TestCase):
    """Brazilian draughts: flying kings + men capture backwards + longest capture.

    This is the rule set jokergame.cc actually plays - the position below is the
    exact board of a live game in which the site answered a double capture
    ``f6xe5xc5`` that English and Russian rules cannot produce.
    """

    ROWS = [
        ".b.b.b.b",
        "b...b.b.",
        ".....b.b",
        "b.r.r...",
        ".......r",
        "....r...",
        "...r.r.r",
        "r.r.r.r.",
    ]

    def test_men_capture_backwards_like_the_site_did(self):
        board = Board.from_rows(self.ROWS, turn=BLACK, rules="brazilian")
        moves = board.generate_moves()
        self.assertEqual([m.notation() for m in moves], ["f6xb6"])
        self.assertEqual([square_name(s) for s in moves[0].captured], ["e5", "c5"])
        self.assertEqual([square_name(s) for s in moves[0].landing_path], ["d4", "b6"])

    def test_russian_rules_only_allow_the_forward_jump(self):
        board = Board.from_rows(self.ROWS, turn=BLACK, rules="russian")
        self.assertEqual([m.notation() for m in board.generate_moves()], ["f6xd4"])

    def test_the_longest_capture_is_compulsory(self):
        rows = [
            "........",
            "........",
            ".......b",
            "......r.",
            "........",
            "..r.r...",
            ".b......",
            "........",
        ]
        brazilian = Board.from_rows(rows, turn=BLACK, rules="brazilian")
        # h6 x g5 -> f4 x e3 -> d2 x c3 -> b4: three men, and only three counts
        moves = brazilian.generate_moves()
        self.assertEqual([m.notation() for m in moves], ["h6xb4"])
        self.assertEqual(len(moves[0].captured), 3)
        russian = Board.from_rows(rows, turn=BLACK, rules="russian")
        # without backwards captures the man on b2 cannot take c3 at all, and the
        # chain stops after one jump, so the short capture is the only option
        self.assertEqual(sorted(m.notation() for m in russian.generate_moves()),
                         ["h6xd2"])

    def test_kings_fly_under_brazilian_rules_too(self):
        board = Board.from_rows([
            ".b......",
            "........",
            "........",
            "........",
            "...R....",
            "........",
            "........",
            "........",
        ], turn=BLACK, rules="brazilian")
        targets = sorted(square_name(m.to_square) for m in board.quiet_moves(RED))
        self.assertEqual(len(targets), 13)
        self.assertIn("h8", targets)


class EvaluationTests(unittest.TestCase):
    def test_start_position_is_balanced(self):
        self.assertEqual(evaluate(Board()), 0)

    def test_evaluation_is_symmetric(self):
        positions = [
            Board(),
            Board.from_rows([
                "........",
                "....b...",
                ".....R..",
                "........",
                "...r....",
                "..b.....",
                "........",
                "....b...",
            ]),
        ]
        for index, board in enumerate(positions):
            with self.subTest(position=index):
                self.assertEqual(evaluate(board), -evaluate(_rotate_and_swap(board)))

    def test_extra_piece_is_worth_about_a_man(self):
        rows = [
            "........",
            "....b...",
            "........",
            "........",
            "...r....",
            "........",
            "..b.....",
            "........",
        ]
        with_piece = Board.from_rows(rows)
        without_piece = Board.from_rows(rows)
        without_piece.set_piece(parse_square("e7"), 0)
        gain = evaluate(without_piece) - evaluate(with_piece)
        self.assertGreater(gain, 80)
        self.assertLess(gain, 400)


class SearchTests(unittest.TestCase):
    def test_engine_returns_a_legal_move(self):
        board = Board()
        snapshot = board.copy()
        ai = CheckersAI(max_depth=5, time_limit=1.0)
        move = ai.choose_move(board)
        self.assertIsNotNone(move)
        self.assertIn(move, board.generate_moves())
        self.assertGreaterEqual(ai.stats.depth, 1)
        self.assertEqual(board, snapshot)  # the search must not touch the caller's board

    def test_engine_handles_a_terminal_position(self):
        board = Board.from_rows([
            "........",
            "........",
            "...r....",
            "........",
            "........",
            "........",
            "........",
            "........",
        ], turn=BLACK)
        self.assertEqual(board.winner(), RED)
        ai = CheckersAI(max_depth=4, time_limit=0.5)
        self.assertIsNone(ai.choose_move(board))

    def test_engine_takes_the_safe_jump(self):
        # Two jumps are available.  d4xc5 walks into the recapture a7xb6 and
        # loses the whole red army, d4xf6 wins a man for free - the engine must
        # see the difference.
        board = Board.from_rows([
            "........",
            "b.......",
            "........",
            "..b.b...",
            "...r....",
            "........",
            "........",
            "........",
        ])
        ai = CheckersAI(max_depth=6, time_limit=1.0)
        move = ai.choose_move(board)
        self.assertIsNotNone(move)
        self.assertEqual(move.notation(), "d4xf6")

    def test_engine_finds_a_forced_win(self):
        # Both red men can jump the last black man, so the search really runs.
        board = Board.from_rows([
            "........",
            "........",
            "........",
            "....b...",
            "...r.r..",
            "........",
            "........",
            "........",
        ])
        self.assertEqual(len(board.generate_moves()), 2)
        ai = CheckersAI(max_depth=6, time_limit=1.0)
        move = ai.choose_move(board)
        self.assertIsNotNone(move)
        after = board.resulting_board(move)
        self.assertEqual(after.piece_count(BLACK), 0)
        self.assertGreaterEqual(ai.stats.score, MATE_THRESHOLD)

    def test_time_limited_search_with_history_does_not_crash(self):
        # A timeout interrupts the recursion, the search must still stay sane.
        board = Board()
        history = [board.position_key()]
        ai = CheckersAI(max_depth=12, time_limit=0.15)
        for _ in range(5):
            move = ai.choose_move(board, history=history)
            self.assertIn(move, board.generate_moves())
            board.apply_move(move)
            history.append(board.position_key())

    def test_repetition_is_avoided_when_ahead(self):
        board = Board.from_rows([
            "........",
            "........",
            "..R...R.",
            "........",
            "...R....",
            "........",
            ".b......",
            "........",
        ])
        ai = CheckersAI(max_depth=4, time_limit=1.0, max_extra_depth=0)
        first = ai.choose_move(board)
        self.assertIsNotNone(first)
        repeated = board.resulting_board(first).position_key()
        history = [board.position_key(), repeated]
        second = ai.choose_move(board, history=history)
        self.assertIsNotNone(second)
        self.assertNotEqual(
            second, first, "the engine should not walk into the same position again"
        )

    def test_engine_obeys_the_time_limit(self):
        ai = CheckersAI(max_depth=20, time_limit=0.4)
        started = time.perf_counter()
        move = ai.choose_move(Board())
        elapsed = time.perf_counter() - started
        self.assertIsNotNone(move)
        self.assertLess(elapsed, 2.0)

    def test_dynamic_depth_grows_in_endgames(self):
        ai = CheckersAI(max_depth=6, time_limit=1.0)
        self.assertEqual(ai.dynamic_depth(Board()), 6)
        endgame = Board.from_rows([
            "........",
            "....b...",
            "........",
            "...r....",
            "........",
            "..b.....",
            "........",
            "........",
        ])
        self.assertGreater(ai.dynamic_depth(endgame), 6)

    def test_engine_always_plays_a_forced_jump(self):
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
        ai = CheckersAI(max_depth=5, time_limit=1.0)
        move = ai.choose_move(board)
        self.assertTrue(move.is_capture)
        self.assertEqual(move.notation(), "d4xf6")


if __name__ == "__main__":
    unittest.main(verbosity=2)
