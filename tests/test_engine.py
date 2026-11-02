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

from checkersbot.ai import MATE_THRESHOLD, CheckersAI, evaluate
from checkersbot.board import (
    BLACK,
    BLACK_MAN,
    RED,
    RED_KING,
    RED_MAN,
    RULES,
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
        # the opening has no kings and the first four plies contain no capture, so
        # these are the published English-draughts numbers
        for depth, nodes in ((1, 7), (2, 49), (3, 302), (4, 1469)):
            with self.subTest(depth=depth):
                self.assertEqual(board.perft(depth), nodes)
        # at ply 5 a capture appears for the first time, and JokerGame lets a man
        # take backwards too, so the tree is larger than the English 7361
        self.assertEqual(board.perft(5), 7482)
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

    def test_a_promotion_jump_continues_as_a_king(self):
        board = Board.from_rows([
            "........",
            "..b.b...",      # c7 and e7
            ".r......",      # b6 jumps c7, is crowned on d8, keeps taking e7
            "........",
            "........",
            "........",
            "........",
            "........",
        ])
        moves = board.generate_moves()
        self.assertEqual(sorted(m.notation() for m in moves),
                         ["b6xf6", "b6xg5", "b6xh4"])
        for move in moves:
            self.assertTrue(move.promotes)
            self.assertEqual(move.captured_count, 2)
            self.assertEqual(square_name(move.landing_path[0]), "d8")
        board.apply_move(next(m for m in moves if m.notation() == "b6xf6"))
        self.assertEqual(board.piece_at(parse_square("f6")), RED_KING)
        self.assertEqual(board.piece_count(BLACK), 0)
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

    def test_men_may_jump_backwards(self):
        # a black man on d4 jumps the red man on e5 - "up" is backwards for black
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
        self.assertEqual([move.notation() for move in moves], ["d4xf6"])
        self.assertEqual([square_name(s) for s in moves[0].captured], ["e5"])

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
        # a taken piece leaves at once, so the king may land on any free square
        # behind it - here that gives every route through both men
        self.assertEqual(len(moves), 12)
        for move in moves:
            self.assertEqual({square_name(s) for s in move.captured}, {"e5", "c3"})
            self.assertEqual(len(move.landing_path), 2)
            self.assertEqual(move.to_square, move.landing_path[-1])
        self.assertEqual({square_name(move.to_square) for move in moves},
                         {"a1", "b2", "f6", "g7", "h8"})

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


class JokerGameRuleTests(unittest.TestCase):
    """The one rule set of the project: :data:`checkersbot.rules.JOKERGAME`."""

    def test_there_is_exactly_one_rule_set(self):
        self.assertEqual(RULES, ("joker",))
        board = Board()
        self.assertEqual(board.rules, "joker")
        self.assertEqual(board.removal, "immediate")
        self.assertTrue(board.flying_kings)
        self.assertTrue(board.men_capture_backwards)
        self.assertTrue(board.promotion_continues)
        self.assertFalse(board.longest_capture_required)
        self.assertFalse(hasattr(board, "maximum_capture"))

    def test_other_rule_sets_are_refused(self):
        for name in ("english", "russian", "brazilian", "nope"):
            with self.assertRaises(ValueError, msg=name):
                Board(rules=name)
            with self.assertRaises(ValueError, msg=name):
                Board.from_rows(["........"] * 8, rules=name)

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
        ])
        targets = sorted(square_name(m.to_square) for m in board.quiet_moves(RED))
        self.assertEqual(targets, [
            "a1", "a7", "b2", "b6", "c3", "c5", "e3", "e5", "f2", "f6", "g1",
            "g7", "h8",
        ])

    def test_men_move_forwards_only(self):
        board = Board.from_rows([
            "........",
            "........",
            "........",
            "........",
            "...r....",
            "........",
            "........",
            "........",
        ])
        targets = sorted(square_name(m.to_square) for m in board.quiet_moves(RED))
        self.assertEqual(targets, ["c5", "e5"])

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
        board = Board.from_rows(rows, turn=BLACK)
        routes = {(m.notation(), tuple(square_name(s) for s in m.captured),
                   tuple(square_name(s) for s in m.landing_path))
                  for m in board.generate_moves()}
        self.assertEqual(routes, {
            ("c1xd6", ("e3", "e5"), ("f4", "d6")),
            ("c1xe7", ("e3", "f6"), ("g5", "e7")),
        })

    def test_men_capture_backwards_like_the_site_did(self):
        rows = [
            ".b.b.b.b",
            "b...b.b.",
            ".....b.b",
            "b.r.r...",
            ".......r",
            "....r...",
            "...r.r.r",
            "r.r.r.r.",
        ]
        board = Board.from_rows(rows, turn=BLACK)
        moves = {m.notation(): m for m in board.generate_moves()}
        self.assertEqual(sorted(moves), ["f6xb6"])
        move = moves["f6xb6"]
        self.assertEqual([square_name(s) for s in move.captured], ["e5", "c5"])
        self.assertEqual([square_name(s) for s in move.landing_path], ["d4", "b6"])

    def test_a_piece_is_never_taken_twice(self):
        rows = [
            "........",
            "........",
            ".....b..",
            "........",
            "...R....",
            "........",
            "........",
            "........",
        ]
        board = Board.from_rows(rows, turn=RED)
        for move in board.generate_moves():
            self.assertEqual(len(set(move.captured)), len(move.captured),
                             "%s takes a piece twice" % move.notation())


class CaptureLawTests(unittest.TestCase):
    """Capturing is compulsory, a chain is finished, but nothing is "the longest"."""

    #: the position of the report: 2 pieces or 3 pieces, both legal
    TWO_PATHS = [
        ".....b..",
        "......b.",
        ".....b.b",
        "..b...b.",
        "........",
        "..b.b.b.",
        ".r......",
        "........",
    ]

    def test_the_reported_position_has_exactly_two_paths(self):
        board = Board.from_rows(self.TWO_PATHS, turn=RED)
        moves = {m.notation(): m for m in board.generate_moves()}
        self.assertEqual(sorted(moves), ["b2xb6", "b2xh4"])
        self.assertEqual(moves["b2xb6"].captured_count, 2)
        self.assertEqual([square_name(s) for s in moves["b2xb6"].captured],
                         ["c3", "c5"])
        self.assertEqual([square_name(s) for s in moves["b2xb6"].landing_path],
                         ["d4", "b6"])
        self.assertEqual(moves["b2xh4"].captured_count, 3)
        self.assertEqual([square_name(s) for s in moves["b2xh4"].captured],
                         ["c3", "e3", "g3"])
        self.assertEqual([square_name(s) for s in moves["b2xh4"].landing_path],
                         ["d4", "f2", "h4"])

    def test_the_chain_may_not_stop_where_it_can_continue(self):
        board = Board.from_rows(self.TWO_PATHS, turn=RED)
        self.assertNotIn("b2xd4", [m.notation() for m in board.generate_moves()])
        # the generator really is the only source: no move is a single hop here
        for move in board.generate_moves():
            self.assertGreaterEqual(len(move.landing_path), 2, move.notation())

    def test_a_shorter_capture_is_legal_next_to_a_longer_one(self):
        # h6 takes three men, b2 takes two - both are offered by the engine
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
        board = Board.from_rows(rows, turn=BLACK)
        moves = {m.notation(): m for m in board.generate_moves()}
        self.assertEqual(sorted(moves), ["b2xf2", "h6xb4"])
        self.assertEqual(moves["h6xb4"].captured_count, 3)
        self.assertEqual(moves["b2xf2"].captured_count, 2)

    def test_nothing_is_filtered_after_generation(self):
        for turn in (RED, BLACK):
            board = Board.from_rows(list(self.TWO_PATHS), turn=turn)
            self.assertEqual(board.capture_moves(), board.raw_capture_moves())

    def test_both_paths_can_be_applied_and_undone(self):
        board = Board.from_rows(self.TWO_PATHS, turn=RED)
        snapshot = board.copy()
        for move in list(board.generate_moves()):
            after = board.resulting_board(move)
            self.assertEqual(after.piece_count(BLACK), 9 - move.captured_count)
            self.assertEqual(after.turn, BLACK)
            self.assertFalse(after.piece_at(move.from_square))
            self.assertEqual(after.piece_at(move.to_square), RED_MAN)
            undo = board.make_move(move)
            board.unmake_move(move, undo)
            self.assertEqual(board, snapshot)

    def test_the_engine_may_pick_either_path(self):
        board = Board.from_rows(self.TWO_PATHS, turn=RED)
        legal = board.generate_moves()
        self.assertEqual(len(legal), 2)
        for allowed in ([legal[0]], [legal[1]]):
            ai = CheckersAI(max_depth=4, time_limit=0.5)
            move = ai.choose_move(board, allowed=allowed)
            self.assertEqual(move, allowed[0])




class PromotionInsideCaptureTests(unittest.TestCase):
    """A man crowned *during* a capture keeps capturing as a (flying) king."""

    ROWS = [
        "........",
        "..b.b...",      # c7 and e7
        ".....r..",      # f6: jumps e7, lands d8 (crown row), then takes c7
        "........",
        "........",
        "........",
        "........",
        "........",
    ]

    def test_the_crowned_man_keeps_capturing(self):
        board = Board.from_rows(self.ROWS, turn=RED)
        moves = board.generate_moves()
        self.assertTrue(moves)
        self.assertEqual(sorted(m.notation() for m in moves),
                         ["f6xa5", "f6xb6"])
        for move in moves:
            self.assertEqual(move.captured_count, 2)
            self.assertTrue(move.promotes)
            self.assertEqual(square_name(move.landing_path[0]), "d8")
        self.assertEqual([square_name(s) for s in moves[0].captured], ["e7", "c7"])

    def test_the_move_ends_as_a_king(self):
        board = Board.from_rows(self.ROWS, turn=RED)
        move = next(m for m in board.generate_moves()
                    if square_name(m.to_square) == "b6")
        board.apply_move(move)
        self.assertEqual(board.piece_at(parse_square("b6")), RED_KING)
        self.assertEqual(board.piece_count(BLACK), 0)
        self.assertEqual(board.turn, BLACK)

    def test_promotion_without_further_captures_still_promotes(self):
        board = Board.from_rows([
            "........",
            "....b...",      # e7
            ".....r..",      # f6 -> d8
            "........",
            "........",
            "........",
            "........",
            "........",
        ], turn=RED)
        moves = board.generate_moves()
        self.assertEqual([m.notation() for m in moves], ["f6xd8"])
        self.assertTrue(moves[0].promotes)
        board.apply_move(moves[0])
        self.assertEqual(board.piece_at(parse_square("d8")), RED_KING)

    def test_make_unmake_roundtrip_with_a_promotion_chain(self):
        board = Board.from_rows(self.ROWS, turn=RED)
        snapshot = board.copy()
        move = next(m for m in board.generate_moves()
                    if square_name(m.to_square) == "b6")
        undo = board.make_move(move)
        board.unmake_move(move, undo)
        self.assertEqual(board, snapshot)

    def test_the_engine_plays_a_promotion_chain(self):
        board = Board.from_rows(self.ROWS, turn=RED)
        ai = CheckersAI(color=RED, max_depth=4, time_limit=0.5)
        move = ai.choose_move(board)
        self.assertIsNotNone(move)
        self.assertIn(move, board.generate_moves())
        self.assertTrue(move.promotes)


class FlyingKingCaptureTests(unittest.TestCase):
    """A king ("damka") captures over any distance and keeps going."""

    def test_a_king_takes_two_men_in_one_move(self):
        board = Board.from_rows([
            "........",
            "......b.",      # g7
            "........",
            "....b...",      # e5
            "........",
            "..R.....",      # c3: over e5 to f6, then over g7 to h8
            "........",
            "........",
        ], turn=RED)
        moves = board.generate_moves()
        self.assertEqual([m.notation() for m in moves], ["c3xh8"])
        move = moves[0]
        self.assertEqual(move.captured_count, 2)
        self.assertEqual([square_name(s) for s in move.captured], ["e5", "g7"])
        self.assertEqual([square_name(s) for s in move.landing_path], ["f6", "h8"])
        board.apply_move(move)
        self.assertEqual(board.piece_count(BLACK), 0)
        self.assertEqual(board.piece_at(parse_square("h8")), RED_KING)

    def test_a_king_may_land_anywhere_behind_the_captured_piece(self):
        board = Board.from_rows([
            "........",
            "........",
            "........",
            "....b...",      # e5
            "........",
            "..R.....",      # c3
            "........",
            "........",
        ], turn=RED)
        landings = sorted(square_name(m.to_square) for m in board.generate_moves())
        self.assertEqual(landings, ["f6", "g7", "h8"])   # not just the next square
        self.assertNotIn("e5", landings)                 # never on the piece it took

    def test_the_engine_plays_the_double_king_capture(self):
        board = Board.from_rows([
            "........",
            "......b.",
            "........",
            "....b...",
            "........",
            "..R.....",
            "........",
            "........",
        ], turn=RED)
        ai = CheckersAI(color=RED, max_depth=4, time_limit=0.5)
        move = ai.choose_move(board)
        self.assertIsNotNone(move)
        self.assertEqual(move.captured_count, 2)


class BotCaptureChoiceTests(unittest.TestCase):
    """The real bot's plumbing must not lose either path of the report position."""

    ROWS = CaptureLawTests.TWO_PATHS

    def _bot(self):
        from checkersbot.bot import CheckersBot

        return CheckersBot(site=object(), depth=4, time_limit=0.4,
                           log=lambda message: None)

    def test_the_bot_plays_one_of_the_two_paths(self):
        bot = self._bot()
        board = Board.from_rows(self.ROWS, turn=RED)
        move = bot._search_move(board)
        self.assertIn(move.notation(), ["b2xb6", "b2xh4"])

    def test_the_bot_can_be_told_to_play_each_path(self):
        bot = self._bot()
        board = Board.from_rows(self.ROWS, turn=RED)
        legal = {move.notation(): move for move in board.generate_moves()}
        for notation in ("b2xb6", "b2xh4"):
            move = bot._search_move(board, allowed=[legal[notation]])
            self.assertEqual(move.notation(), notation)

    def test_the_site_filter_keeps_both_paths(self):
        from checkersbot.site import first_landing, site_legal_moves

        board = Board.from_rows(self.ROWS, turn=RED)
        origin = parse_square("b2")
        probe = {origin: [parse_square("d4")]}       # the site offers d4 only
        kept = site_legal_moves(board, probe)
        self.assertEqual(sorted(move.notation() for move in kept),
                         ["b2xb6", "b2xh4"])
        # both paths share the first landing, so a site offer of d4 keeps both
        self.assertEqual({first_landing(move) for move in kept},
                         {parse_square("d4")})

    def test_the_bot_has_no_rule_option(self):
        from checkersbot.bot import CheckersBot

        with self.assertRaises(TypeError):
            CheckersBot(site=object(), rules="english")
        bot = self._bot()
        self.assertEqual(bot.rules, "joker")



class CapturePreferenceTests(unittest.TestCase):
    """JokerGame allows any completed capture, but the bot takes as much as it can.

    The site does not force the longest chain (``rules.JOKERGAME``), so a 2-piece
    capture may be played while a 3-piece one exists - a player expectation is the
    other way round, which is what ``CheckersAI.capture_margin`` implements.
    """

    ROWS = [
        ".R......",      # b8: red king
        "..b.....",      # c7
        "........",
        "........",
        "........",
        "..b.....",      # c3
        "........",
        "..b.....",      # c1
    ]

    def _moves(self):
        board = Board.from_rows(self.ROWS, turn=RED, rules="joker")
        moves = board.generate_moves()
        small = next(move for move in moves if move.captured_count == 1)
        big = next(move for move in moves if move.captured_count == 2)
        return board, moves, small, big

    def test_the_bigger_capture_wins_unless_it_is_clearly_worse(self):
        board, moves, small, big = self._moves()
        ai = CheckersAI(max_depth=4, time_limit=None)
        ai.rank_moves = lambda *a, **k: [(0, small), (-40, big)]     # worst first
        self.assertIs(ai._prefer_bigger_capture(board, moves, small), big)
        ai.rank_moves = lambda *a, **k: [(0, small), (-200, big)]
        self.assertIs(ai._prefer_bigger_capture(board, moves, small), small)

    def test_a_quiet_move_is_never_swapped_for_a_capture(self):
        board, moves, small, _ = self._moves()
        quiet = Board().generate_moves()[0]
        ai = CheckersAI(max_depth=4, time_limit=None)
        ai.rank_moves = lambda *a, **k: [(0, quiet), (-1, small)]
        self.assertIs(ai._prefer_bigger_capture(board, moves, quiet), quiet)

    def test_the_preference_can_be_switched_off(self):
        board, moves, small, big = self._moves()
        ai = CheckersAI(max_depth=4, time_limit=None)
        ai.capture_margin = 0
        self.assertIs(ai._prefer_bigger_capture(board, moves, small), small)


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
    def test_a_forced_move_is_reported_as_not_evaluated(self):
        # one legal move: the bot does not waste a search, but it must not invent a
        # score for it either - `None` means "not evaluated", never 0
        board = Board.from_rows(["........"] * 7 + ["r......."], turn=RED)
        self.assertEqual(len(board.generate_moves()), 1)
        ai = CheckersAI(max_depth=6, time_limit=1.0)
        move = ai.choose_move(board)
        self.assertEqual(move, board.generate_moves()[0])
        self.assertIsNone(ai.stats.score)
        self.assertEqual(ai.stats.depth, 0)
        self.assertIn("not evaluated", ai.stats.note)
        self.assertIn("not evaluated", str(ai.stats))

    def test_the_same_move_is_really_searched_when_asked(self):
        board = Board.from_rows(["........"] * 7 + ["r......."], turn=RED)
        ai = CheckersAI(max_depth=6, time_limit=1.0)
        move = ai.choose_move(board, quick_single=False)
        self.assertEqual(move, board.generate_moves()[0])
        self.assertIsNotNone(ai.stats.score)
        self.assertGreater(ai.stats.nodes, 0)
        self.assertGreaterEqual(ai.stats.depth, 1)

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
        # three kings against a single man: the engine is winning and must not walk
        # into the position it has just reached
        board = Board.from_rows([
            ".b......",
            "........",
            "..R...R.",
            "........",
            "...R....",
            "........",
            "........",
            "........",
        ], turn=RED)
        self.assertGreaterEqual(len(board.generate_moves()), 2)
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
