"""Tests for the interactive test board's logic (``checkersbot/lab.py``).

They check what the board *shows* against the real generator and the real search:
that the displayed moves are exactly the engine's legal moves, that **every**
completed capture path is legal (the number of pieces taken is never a filter),
that the two-path position from the report gives its two paths, that a position can
be saved and opened again (and old files are migrated, not silently reused), and
that the window invalidates an analysis when the position changes.
"""

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from checkersbot import lab  # noqa: E402
from checkersbot.board import BLACK, RED, Board, square_name  # noqa: E402
from checkersbot.solver import best_line, solve  # noqa: E402

#: two different routes with the same start and end (g1 → b6), found by search
SAME_ENDS = [
    ".....b..",
    "........",
    "........",
    "..b...b.",
    "........",
    "........",
    ".r...b..",
    "......R.",
]
#: man jumps, is crowned on d8 and keeps capturing as a king
PROMOTION = [
    "........",
    "..b.b...",
    ".....r..",
    "........",
    "........",
    "........",
    "........",
    "........",
]
#: a king that takes two men from a distance
FLYING = [
    "........",
    "......b.",
    "........",
    "....b...",
    "........",
    "..R.....",
    "........",
    "........",
]
#: THE position from the report: b2 takes c3 and then either b6 (2 pieces) or
#: h4 (3 pieces); b2→d4 alone is not legal because a continuation exists.
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
#: black king c3 between the red kings d4 and b2: eight routes, as reported
KING_EIGHT = [
    "........",
    "........",
    "........",
    "........",
    "...R....",
    "..B.....",
    ".R......",
    "........",
]
#: the same position rotated 180° with the colours swapped
KING_EIGHT_MIRRORED = [
    "........",
    "......B.",
    ".....R..",
    "....B...",
    "........",
    "........",
    "........",
    "........",
]
#: two men with captures of different length in one position (1 and 2 pieces)
TWO_LENGTHS = [
    "........",
    "........",
    "........",
    "........",
    ".....b..",
    "..b.....",
    ".r...b..",
    "......r.",
]


def position(rows, turn=RED):
    """A test-board position - the rules are always JokerGame."""
    return lab.LabPosition(rows=list(rows), turn=turn)


def routes_of(board):
    """``(notation, landing squares)`` for every legal move, sorted."""
    return sorted(
        (move.notation(), tuple(hop["to"] for hop in lab.move_hops(board, move)))
        for move in board.generate_moves()
    )


class DisplayMatchesEngineTests(unittest.TestCase):
    ROWS = [lab.rows_of(Board()), SAME_ENDS, PROMOTION, FLYING, TWO_PATHS]

    def test_displayed_moves_are_exactly_the_engines(self):
        for rows in self.ROWS:
            for turn in (RED, BLACK):
                board = Board.from_rows(list(rows), turn=turn)
                shown = [row["notation"] for row in lab.legal_moves(board)]
                real = [move.notation() for move in board.generate_moves()]
                self.assertEqual(shown, real, turn)

    def test_every_hop_matches_the_engines_capture_list(self):
        for rows in self.ROWS:
            board = Board.from_rows(list(rows), turn=RED)
            for move in board.generate_moves():
                hops = lab.move_hops(board, move)
                victims = [hop["victim"] for hop in hops if hop["victim"]]
                self.assertEqual(len(victims), move.captured_count)
                self.assertEqual(
                    {hop["victim_index"] for hop in hops if hop["victim"]},
                    set(move.captured),
                )
                self.assertEqual([hop["to_index"] for hop in hops][-1], move.to_square)


class JokerGameTextTests(unittest.TestCase):
    """Tab 4 shows the one shared configuration, not a comparison of modes."""

    def test_the_text_is_the_shared_configuration(self):
        text = "\n".join(lab.JOKERGAME_TEXT)
        self.assertEqual(lab.JOKERGAME.name, "joker")
        self.assertIn("8×8", text)
        self.assertIn("მფრინავ", text)                  # flying kings
        self.assertIn("უკან", text)                     # men capture backwards
        self.assertIn("სავალდებულოა", text)             # captures are compulsory
        self.assertIn("მაშინვე ტოვებს დაფას", text)      # immediate removal

    def test_the_text_does_not_claim_a_compulsory_maximum(self):
        text = "\n".join(lab.JOKERGAME_TEXT)
        self.assertNotIn("სავალდებულოა ყველაზე გრძელი ჭამა", text)
        self.assertIn("სავალდებულო არ არის", text)

    def test_the_text_says_where_the_observed_rules_come_from(self):
        text = "\n".join(lab.JOKERGAME_TEXT)
        self.assertIn("მომხმარებლის დაკვირვებას", text)
        self.assertIn("დამოუკიდებლად არ არის დადასტურებული", text)
        self.assertFalse(lab.JOKERGAME.longest_capture_required)
        self.assertEqual(lab.JOKERGAME.removal, "immediate")

    def test_the_diagnostic_flags_match_the_code(self):
        board = Board.from_rows(TWO_PATHS, turn=RED)
        diagnostics = lab.capture_diagnostics(board)
        self.assertEqual(diagnostics["flying_kings"], board.flying_kings)
        self.assertEqual(diagnostics["men_capture_backwards"],
                         board.men_capture_backwards)
        self.assertEqual(diagnostics["promotion_continues"],
                         board.promotion_continues)
        self.assertEqual(diagnostics["removal"], board.removal)
        self.assertEqual(diagnostics["longest_capture_required"],
                         board.longest_capture_required)
        self.assertFalse(hasattr(board, "maximum_capture"))   # the filter is gone



class CapturePathTests(unittest.TestCase):
    """Every completed path is a legal move - length is not a filter."""

    def test_the_two_paths_of_the_reported_position(self):
        board = Board.from_rows(TWO_PATHS, turn=RED)
        diagnostics = lab.capture_diagnostics(board)
        self.assertTrue(diagnostics["has_capture"])
        self.assertEqual(diagnostics["path_count"], 2)
        self.assertTrue(all(path["legal"] for path in diagnostics["paths"]))
        lengths = sorted(path["captured_count"] for path in diagnostics["paths"])
        self.assertEqual(lengths, [2, 3])
        self.assertEqual(diagnostics["longest"], 3)

    def test_the_short_path_is_not_dropped_for_a_longer_one(self):
        board = Board.from_rows(TWO_PATHS, turn=RED)
        moves = {move.notation(): move for move in board.capture_moves()}
        self.assertEqual(sorted(moves), ["b2xb6", "b2xh4"])
        short = moves["b2xb6"]
        self.assertEqual(short.captured_count, 2)
        self.assertEqual([square_name(s) for s in short.landing_path], ["d4", "b6"])
        self.assertEqual([square_name(s) for s in short.captured], ["c3", "c5"])
        long = moves["b2xh4"]
        self.assertEqual(long.captured_count, 3)
        self.assertEqual([square_name(s) for s in long.landing_path],
                         ["d4", "f2", "h4"])
        self.assertEqual([square_name(s) for s in long.captured], ["c3", "e3", "g3"])

    def test_a_chain_may_not_stop_where_a_continuation_exists(self):
        board = Board.from_rows(TWO_PATHS, turn=RED)
        notations = {row["notation"] for row in lab.legal_moves(board)}
        self.assertNotIn("b2xd4", notations)
        # ...and no legal move is a single hop of one of the real chains
        for row in lab.legal_moves(board):
            self.assertGreaterEqual(len(row["hops"]), 2, row["notation"])

    def test_nothing_is_excluded_by_the_generator(self):
        for rows in (SAME_ENDS, PROMOTION, FLYING, TWO_PATHS, KING_EIGHT):
            board = Board.from_rows(list(rows), turn=RED)
            self.assertEqual(board.capture_moves(), board.raw_capture_moves())
            for path in lab.capture_diagnostics(board)["paths"]:
                self.assertTrue(path["legal"])
                self.assertNotIn("გამოირიცხა", path["reason"])

    def test_two_routes_with_the_same_ends_stay_separate(self):
        board = Board.from_rows(SAME_ENDS, turn=RED)
        groups = lab.moves_with_same_ends(board)
        self.assertTrue(groups)
        group = groups[0]
        same_ends = {(row["from_index"], row["to_index"]) for row in group}
        self.assertEqual(len(same_ends), 1)
        self.assertGreaterEqual(len(group), 2)
        routes = {tuple((hop["from"], hop["to"]) for hop in row["hops"])
                  for row in group}
        self.assertEqual(len(routes), len(group))      # every route is distinct

    def test_different_pieces_with_different_lengths_are_both_legal(self):
        board = Board.from_rows(TWO_LENGTHS, turn=RED)
        moves = {move.notation(): move.captured_count
                 for move in board.capture_moves()}
        self.assertEqual(moves, {"b2xd4": 1, "g1xg5": 2})
        self.assertEqual(sorted(moves.values()), [1, 2])

    def test_the_quiet_move_is_forbidden_while_a_capture_exists(self):
        board = Board.from_rows(TWO_PATHS, turn=RED)
        notations = [row["notation"] for row in lab.legal_moves(board)]
        self.assertEqual(sorted(notations), ["b2xb6", "b2xh4"])
        self.assertTrue(all("-" not in notation for notation in notations))



class ReportedPositionTests(unittest.TestCase):
    """The exact positions of the report, in both colours."""

    def test_the_eight_routes_of_the_king(self):
        board = Board.from_rows(KING_EIGHT, turn=BLACK)
        routes = routes_of(board)
        self.assertEqual(len(routes), 8)
        expected = [("c3xa1", ("e5", "a1")), ("c3xa1", ("f6", "a1")),
                    ("c3xa1", ("g7", "a1")), ("c3xa1", ("h8", "a1")),
                    ("c3xe5", ("a1", "e5")), ("c3xf6", ("a1", "f6")),
                    ("c3xg7", ("a1", "g7")), ("c3xh8", ("a1", "h8"))]
        self.assertEqual(routes, sorted(expected))
        for move in board.generate_moves():
            self.assertEqual(move.captured_count, 2)
            self.assertEqual({square_name(s) for s in move.captured}, {"d4", "b2"})

    def test_the_same_routes_with_the_colours_swapped(self):
        board = Board.from_rows(KING_EIGHT_MIRRORED, turn=RED)
        self.assertEqual(len(board.generate_moves()), 8)
        for move in board.generate_moves():
            self.assertEqual(move.captured_count, 2)
            self.assertEqual({square_name(s) for s in move.captured}, {"e5", "g7"})

    def test_every_segment_is_diagonal(self):
        for rows, turn in ((KING_EIGHT, BLACK), (KING_EIGHT_MIRRORED, RED),
                           (TWO_PATHS, RED), (TWO_LENGTHS, RED)):
            board = Board.from_rows(list(rows), turn=turn)
            for row in lab.legal_moves(board):
                squares = ([row["from_index"]]
                           + [hop["to_index"] for hop in row["hops"]])
                for start, end in zip(squares, squares[1:]):
                    d_row = abs((end // 8) - (start // 8))
                    d_col = abs((end % 8) - (start % 8))
                    self.assertEqual(d_row, d_col, row["notation"])
                    self.assertGreater(d_row, 0)

    def test_the_reported_turn_back_route_exists(self):
        board = Board.from_rows(KING_EIGHT, turn=BLACK)
        routes = {tuple(hop["to"] for hop in lab.move_hops(board, move))
                  for move in board.generate_moves()}
        # c3 → a1 → f6: the king clears d4 and b2, lands on a1 and turns back
        self.assertIn(("a1", "f6"), routes)

    def test_after_the_two_captures_the_opponent_has_nothing_left(self):
        board = Board.from_rows(KING_EIGHT, turn=BLACK)
        for move in board.generate_moves():
            after = board.resulting_board(move)
            self.assertEqual(after.piece_count(RED), 0, move.notation())
            self.assertEqual(after.turn, RED)
            self.assertFalse(after.generate_moves())      # red must pass: black won
            self.assertTrue(after.is_terminal())

    def test_both_paths_of_the_man_are_playable(self):
        board = Board.from_rows(TWO_PATHS, turn=RED)
        self.assertEqual(board.piece_count(BLACK), 9)
        for move in board.generate_moves():
            after = board.resulting_board(move)
            self.assertEqual(after.piece_at(move.to_square), 1)   # still a man
            self.assertEqual(after.piece_count(BLACK), 9 - move.captured_count)
            self.assertEqual(after.turn, BLACK)




class PositionTests(unittest.TestCase):
    def test_save_and_open_round_trip(self):
        original = lab.LabPosition(rows=list(PROMOTION), turn=BLACK,
                                   title="ტესტი", site_path="f6xd8",
                                   site_note="საიტზე ასე დავინახე")
        with tempfile.TemporaryDirectory() as folder:
            path = original.save(pathlib.Path(folder) / "pos.json")
            loaded = lab.LabPosition.load(path)
        self.assertEqual(loaded.rows, original.rows)
        self.assertEqual(loaded.turn, BLACK)
        self.assertEqual(loaded.rules, "joker")
        self.assertEqual(loaded.removal, "immediate")
        self.assertEqual(loaded.site_path, "f6xd8")
        self.assertEqual(loaded.site_note, "საიტზე ასე დავინახე")
        self.assertEqual(loaded.migrated, [])
        self.assertEqual(loaded.to_board().cells, original.to_board().cells)

    def test_an_old_file_is_migrated_and_says_so(self):
        # a file written by the old multi-rule version: brazilian + removal "end"
        data = {
            "format": lab.POSITION_FORMAT,
            "rows": list(TWO_PATHS),
            "turn": "red",
            "rules": "brazilian",
            "removal": "end",
        }
        loaded = lab.LabPosition.from_dict(data)
        self.assertEqual(loaded.rules, "joker")
        self.assertEqual(loaded.removal, "immediate")
        self.assertEqual(len(loaded.migrated), 2)
        self.assertTrue(any("brazilian" in line for line in loaded.migrated))
        self.assertTrue(any("end" in line for line in loaded.migrated))
        # the migrated record also lands in the saved JSON, so nothing is silent
        written = loaded.to_dict()
        self.assertEqual(written["rules"], "joker")
        self.assertEqual(written["removal"], "immediate")
        self.assertEqual(written["migrated"], loaded.migrated)
        self.assertIn("rules_basis", written)

    def test_the_file_marks_the_history_as_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            path = lab.LabPosition(rows=list(PROMOTION)).save(
                pathlib.Path(folder) / "pos.json")
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["history"], "unknown")
        self.assertIn("repetition", data["history_note"])
        self.assertEqual(data["format"], lab.POSITION_FORMAT)

    def test_broken_positions_are_refused(self):
        with self.assertRaises(ValueError):
            lab.LabPosition.from_dict({"rows": ["........"] * 7, "turn": "red"})
        with self.assertRaises(ValueError):
            rows = ["........"] * 8
            rows[0] = "x......."
            lab.LabPosition.from_dict({"rows": rows})

    def test_problems_are_reported_not_fixed(self):
        rows = ["........"] * 8
        rows[0] = "r......."                 # a light square (row 0, col 0)
        board = Board.from_rows(rows, turn=RED)
        problems = lab.position_problems(board)
        self.assertTrue(any("მსუბუქ" in text for text in problems))
        self.assertTrue(any("შავს ქვა არ აქვს" in text for text in problems))
        self.assertEqual(board.cells[0], 1)  # the board was not repaired


class AnalysisTests(unittest.TestCase):
    def test_an_analysis_comes_back_with_numbers(self):
        analysis = lab.analyze_position(position(PROMOTION), seconds=0.3, depth_cap=6)
        self.assertIsNotNone(analysis["move"])
        self.assertIn(analysis["move"]["notation"], ["f6xa5", "f6xb6"])
        self.assertEqual(analysis["side"], "red")
        self.assertGreaterEqual(analysis["depth"], 1)
        self.assertGreater(analysis["nodes"], 0)
        self.assertIsNotNone(analysis["score"])
        components = analysis["components"]
        self.assertEqual(sum(components.values()), analysis["static"])

    def test_a_single_move_is_still_searched_in_the_lab(self):
        # the bot short-circuits a forced move (score None); the test board must
        # not - it always reports a real search result
        rows = ["........"] * 7 + ["r......."]
        analysis = lab.analyze_position(position(rows), seconds=0.5, depth_cap=6)
        self.assertIsNotNone(analysis["move"])
        self.assertIsNotNone(analysis["score"])
        self.assertGreater(analysis["nodes"], 0)

    def test_a_position_without_moves_is_reported_as_such(self):
        rows = [
            "........",
            ".....b..",
            "..b.....",
            "........",
            "........",
            "........",
            "........",
            "........",
        ]
        board = Board.from_rows(rows, turn=RED)
        self.assertFalse(board.generate_moves())      # red has no move -> mate
        analysis = lab.analyze_position(position(rows), seconds=0.2)
        self.assertIsNone(analysis["move"])
        self.assertEqual(analysis["note"], "ლეგალური სვლა არ არის")

    def test_compare_root_moves_covers_every_legal_move(self):
        board = Board.from_rows(TWO_PATHS, turn=RED)
        comparison = lab.compare_root_moves(position(TWO_PATHS), depth=3,
                                            total_seconds=1.5)
        self.assertEqual(len(comparison["rows"]), len(board.generate_moves()))
        self.assertEqual(comparison["side"], "red")
        for row in comparison["rows"]:
            self.assertIn(row["move"]["notation"],
                          [move.notation() for move in board.generate_moves()])
            self.assertIsInstance(row["value_exact"], bool)
            self.assertIsInstance(row["proven"], bool)
        statuses = [row["value_exact"] for row in comparison["rows"]]
        self.assertEqual(statuses, sorted(statuses, reverse=True))

    def test_both_paths_are_compared_under_the_same_conditions(self):
        comparison = lab.compare_root_moves(position(TWO_PATHS), depth=2,
                                            total_seconds=4.0)
        rows = {row["move"]["notation"]: row for row in comparison["rows"]}
        self.assertEqual(sorted(rows), ["b2xb6", "b2xh4"])
        for row in rows.values():
            # one depth target and one clock for everything, nothing pre-ranked
            self.assertEqual(comparison["target_depth"], 2)
            self.assertEqual(comparison["child_target_depth"], 1)
            self.assertEqual(comparison["budget_seconds"], 4.0)
            self.assertEqual(row["requested_depth"], 2)
            self.assertIsNotNone(row["score"])
            self.assertIsInstance(row["proven"], bool)

    def test_the_comparison_respects_the_stop_flag(self):
        comparison = lab.compare_root_moves(position(SAME_ENDS), depth=30,
                                            total_seconds=30,
                                            should_stop=lambda: True)
        for row in comparison["rows"]:
            self.assertFalse(row["value_exact"])
            self.assertIn("შეწყდა", row["note"])
        self.assertIsNone(comparison["best_by_search"])
        self.assertIsNone(comparison["best_proven"])



class LogTests(unittest.TestCase):
    def test_the_log_holds_everything_the_task_asks_for(self):
        analysis = lab.analyze_position(position(TWO_PATHS), seconds=0.3, depth_cap=5)
        comparison = lab.compare_root_moves(position(TWO_PATHS), depth=2,
                                            total_seconds=1.5)
        with tempfile.TemporaryDirectory() as folder:
            paths = lab.write_analysis_log(position(TWO_PATHS), analysis,
                                           comparison=comparison, log_dir=folder)
            data = json.loads(pathlib.Path(paths["json"]).read_text(encoding="utf-8"))
            text = pathlib.Path(paths["text"]).read_text(encoding="utf-8")
        self.assertEqual(data["format"], "checkers-lab-analysis/v1")
        self.assertEqual(data["side_to_move"], "red")
        self.assertEqual(data["position"]["rules"], "joker")
        self.assertEqual(data["removal"], "immediate")
        self.assertIn("observation", data["removal_basis"])
        self.assertEqual(data["position"]["history"], "unknown")
        self.assertIn("package", data["engine"])
        self.assertTrue(data["legal_moves"])
        self.assertEqual(len(data["capture_diagnostics"]["paths"]), 2)
        self.assertIn("move", data["analysis"])
        self.assertIsNotNone(data["comparison"])
        self.assertIn("ისტორია: უცნობია", text)
        self.assertIn("არჩეული სვლა", text)
        self.assertIn("JokerGame-ის წესები", text)
        self.assertNotIn("მაქსიმალური ჭამის ფილტრი", text)
        self.assertNotIn("მოცილების რეჟიმების შედარება", text)

    def test_the_site_note_survives_into_the_log(self):
        board_position = position(PROMOTION)
        board_position.site_path = "f6xd8 (საიტი)"
        board_position.site_note = "საიტზე დამკის შემდეგ ჭამა არ გაგრძელდა"
        analysis = lab.analyze_position(board_position, seconds=0.2, depth_cap=4)
        with tempfile.TemporaryDirectory() as folder:
            paths = lab.write_analysis_log(board_position, analysis, log_dir=folder)
            text = pathlib.Path(paths["text"]).read_text(encoding="utf-8")
            data = json.loads(pathlib.Path(paths["json"]).read_text(encoding="utf-8"))
        self.assertIn("f6xd8 (საიტი)", text)
        self.assertIn("საიტზე დამკის შემდეგ", text)
        self.assertEqual(data["position"]["site_note"],
                         "საიტზე დამკის შემდეგ ჭამა არ გაგრძელდა")


class ExtensionFlagTests(unittest.TestCase):
    """The small engine additions the board relies on."""

    def test_evaluate_components_sum_to_the_score(self):
        from checkersbot.ai import evaluate, evaluate_components

        for rows in (lab.rows_of(Board()), SAME_ENDS, PROMOTION, FLYING,
                     TWO_PATHS):
            board = Board.from_rows(list(rows), turn=RED)
            self.assertEqual(sum(evaluate_components(board).values()),
                             evaluate(board))

    def test_the_stop_flag_stops_the_search_after_the_last_finished_iteration(self):
        from checkersbot.ai import CheckersAI

        board = Board.from_rows(TWO_PATHS, turn=RED)
        ai = CheckersAI(max_depth=60, time_limit=None)
        ai.should_stop = lambda: True
        move = ai.choose_move(board)
        self.assertIn(move, board.generate_moves())
        self.assertLess(ai.stats.elapsed, 5.0)
        self.assertEqual(board.cells,
                         Board.from_rows(TWO_PATHS, turn=RED).cells)



class GuiTests(unittest.TestCase):
    """The window's own logic (no main loop): place, play, undo, analyse."""

    @classmethod
    def setUpClass(cls):
        try:
            import board_lab
        except Exception as error:                 # pragma: no cover
            raise unittest.SkipTest("Tkinter is not available: %s" % error)
        try:
            cls.app = board_lab.BoardLab()
            cls.app.withdraw()
        except Exception as error:                 # pragma: no cover - no display
            raise unittest.SkipTest("no display for Tk: %s" % error)
        cls.module = board_lab

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.app.preset_box.set("ორი გზა, ერთი დასასრული (g1xb6)")
        self.app.load_preset()

    def test_placing_and_undoing_restores_the_previous_cells(self):
        self.app.mode_var.set("edit")
        self.app.tool.set("R")
        rows_before = list(self.app.position.rows)
        self.app._place(3)                      # the real click path snapshots itself
        self.assertIn("R", self.app.position.rows[0])
        self.app.undo()
        self.assertEqual(self.app.position.rows, rows_before)
        # a light square cannot be used at all and nothing is changed
        self.app.tool.set("r")
        self.app._place(0)
        self.assertEqual(self.app.position.rows, rows_before)
        self.assertIn("მსუბუქ", self.app.status.get())

    def test_undo_restores_the_side_to_move_too(self):
        rows_before = list(self.app.position.rows)
        self.app.move_list.selection_set(0)
        self.app.on_move_selected()
        self.app.step_playback(1)
        self.assertEqual(self.app.playback["step"], 1)
        # a playback step must not change the position at all
        self.assertEqual(self.app.position.rows, rows_before)
        before_turn = self.app.position.turn
        self.app.apply_playback_move()
        self.assertNotEqual(self.app.position.turn, before_turn)
        self.assertNotEqual(self.app.position.rows, rows_before)
        self.app.undo()
        self.assertEqual(self.app.position.turn, before_turn)
        self.assertEqual(self.app.position.rows, rows_before)
        self.assertEqual(len(self.app.move_rows), 5)

    def test_the_move_list_shows_the_engines_moves(self):
        board = self.app.position.to_board()
        shown = [self.app.move_list.get(index)
                 for index in range(self.app.move_list.size())]
        notations = [text.split()[0] for text in shown]
        self.assertEqual(notations, [move.notation() for move in board.generate_moves()])


    def test_the_two_path_preset_loads_both_paths(self):
        self.app.preset_box.set("ორი გზა, 2 და 3 ქვა (b2)")
        self.app.load_preset()
        shown = [self.app.move_list.get(index)
                 for index in range(self.app.move_list.size())]
        notations = [text.split()[0] for text in shown]
        self.assertEqual(notations, ["b2xb6", "b2xh4"])
        self.assertEqual(self.app.position.turn, RED)
        self.assertTrue(all("ჭამა" in text for text in shown))

    def test_the_window_has_no_rule_or_removal_selectors(self):
        for attribute in ("rules_box", "rules_var", "removal_var", "rules_var",
                          "removal_text", "removal_compare_text"):
            self.assertFalse(hasattr(self.app, attribute), attribute)
        for name in ("on_rules_change", "on_removal_change",
                     "_select_rules_widget"):
            self.assertFalse(hasattr(self.app, name), name)
        # the preset list is the JokerGame-only one
        self.assertTrue(all("rules" not in preset
                            for preset in self.module.PRESETS.values()))

    def test_the_rules_tab_explains_jokergame(self):
        text = self.app.rules_table.get("1.0", "end")
        self.assertIn("JokerGame", text)
        self.assertIn(lab.JOKERGAME_TEXT[0], text)
        self.assertIn("სავალდებულო არ არის", text)
        self.assertNotIn("სავალდებულოა ყველაზე გრძელი ჭამა", text)
        self.assertNotIn("ოთხივე რეჟიმი", text)

    def test_the_capture_box_lists_both_paths_without_exclusions(self):
        self.app.preset_box.set("ორი გზა, 2 და 3 ქვა (b2)")
        self.app.load_preset()
        listed = [self.app.raw_list.get(index)
                  for index in range(self.app.raw_list.size())]
        self.assertEqual(len(listed), 2)
        self.assertIn("b2xb6", listed[0])
        self.assertIn("2 ქვას", listed[0])
        self.assertIn("b2xh4", listed[1])
        self.assertIn("3 ქვას", listed[1])
        diagnostics = self.app.diag_text.get("1.0", "end")
        self.assertNotIn("მაქსიმალური ჭამის ფილტრი", diagnostics)
        self.assertIn("ფილტრი არ არის", diagnostics)

    def test_a_changed_position_invalidates_the_analysis(self):
        self.app.analysis_text.delete("1.0", "end")
        self.app.analysis_text.insert("1.0", "ძველი ანალიზი: 5 გზა")
        self.app.compare_table.insert("", "end", values=("g1xb6", 12, 8, 100,
                                                         0.2, "ზუსტი", ""))
        self.app.turn_var.set("black")
        self.app.on_turn_change()
        self.assertNotIn("5 გზა", self.app.analysis_text.get("1.0", "end"))
        self.assertIn("გაუქმებულია", self.app.analysis_text.get("1.0", "end"))
        self.assertEqual(len(self.app.compare_table.get_children()), 0)

    def test_a_late_result_for_the_old_position_is_ignored(self):
        old_key = self.app._position_key()
        self.app.turn_var.set("black")
        self.app.on_turn_change()                      # the position changed
        self.app.results.put({"kind": "analysis", "key": old_key,
                              "data": {"move": None, "note": "ძველი"}})
        self.app._poll_results()
        self.assertIn("გაუქმებულია", self.app.analysis_text.get("1.0", "end"))
        self.assertIn("მოძველებული", self.app.status.get())

    def test_the_launcher_can_pick_the_reported_position(self):
        # what open_test_board.bat passes on the command line; the fragment is
        # ASCII on purpose, because a .bat is read in the console code page
        self.assertEqual(self.module.resolve_preset("b2"),
                         "ორი გზა, 2 და 3 ქვა (b2)")
        self.assertEqual(self.module.resolve_preset("ორი გზა, 2 და 3 ქვა (b2)"),
                         "ორი გზა, 2 და 3 ქვა (b2)")
        self.assertEqual(self.module.resolve_preset("2 და 3 ქვა"),
                         "ორი გზა, 2 და 3 ქვა (b2)")
        self.assertIsNone(self.module.resolve_preset("არ არსებობს"))
        # the launcher file itself must stay pure ASCII, or cmd mangles the argument
        launcher = self.module.__file__.replace("board_lab.py", "open_test_board.bat")
        data = pathlib.Path(launcher).read_bytes()
        self.assertEqual([byte for byte in data if byte > 127], [])
        self.assertIn(b"--preset b2", data)

    def test_the_same_position_keeps_its_analysis(self):
        self.app.analysis_text.delete("1.0", "end")
        self.app.analysis_text.insert("1.0", "მოქმედი ანალიზი")
        self.app.refresh_all()                          # nothing changed
        self.assertEqual(self.app.analysis_text.get("1.0", "end").strip(),
                         "მოქმედი ანალიზი")


if __name__ == "__main__":
    unittest.main()



#: an independent reference for the solver: plain negamax over "win in d plies"
#: scores with no pruning at all, used only by the tests below.
def reference_result(rows, turn, depth_limit=12):
    """``(outcome, plies)`` from a deliberately naive full-width search.

    A terminal node scores ``-(BIG - ply)`` where ``ply`` counts half-moves from
    the searched root, so the score itself carries the distance (exactly what
    ``ai`` does); everything is plain negamax with no pruning and no evaluation.
    This is a different implementation from ``checkersbot.solver`` on purpose: the
    two must agree on both the outcome and the distance.
    """
    BIG = 10_000
    board = Board.from_rows(list(rows), turn=turn)
    memo = {}

    def walk(position, plies_left, ply):
        moves = position.generate_moves()
        if not moves:
            return -(BIG - ply)               # no move: lost at this very ply
        if plies_left <= 0:
            return None                       # beyond the horizon: not proven
        key = (tuple(position.cells), position.turn, plies_left)
        if key in memo:
            return memo[key]
        best = None
        unknown = False
        for move in moves:
            value = walk(position.resulting_board(move), plies_left - 1, ply + 1)
            if value is None:
                unknown = True
                continue
            score = -value
            if best is None or score > best:
                best = score
        if best is not None and best < 0 and unknown:
            # a loss needs *every* reply proven: one unknown line forbids the claim
            best = None
        memo[key] = best
        return best

    for limit in range(0, depth_limit + 1):
        memo.clear()                          # a memo from a smaller budget is stale
        value = walk(board, limit, 0)
        if value is None:
            continue
        if value > 0:
            return "win", BIG - value
        return "loss", BIG + value
    return "unknown", None


class SolverTests(unittest.TestCase):
    """The proof must come from full-width play, never from an evaluation."""

    def test_a_win_in_one_move(self):
        rows = [
            "........",
            "........",
            "........",
            "....b...",
            "...r....",
            "........",
            "........",
            "........",
        ]
        board = Board.from_rows(rows, turn=RED)
        self.assertFalse(board.resulting_board(board.generate_moves()[0]).generate_moves())
        proof = solve(board)
        self.assertEqual((proof["outcome"], proof["plies"]), ("win", 1))
        self.assertTrue(proof["complete"])

    def test_a_loss_in_two_plies(self):
        # red's only move is a1-b2, then black's c3 takes it: red has nothing left
        rows = [
            "........",
            "........",
            "........",
            "........",
            "........",
            "..b.....",
            "........",
            "r.......",
        ]
        board = Board.from_rows(rows, turn=RED)
        self.assertEqual([move.notation() for move in board.generate_moves()],
                         ["a1-b2"])
        proof = solve(board)
        self.assertEqual(proof["outcome"], "loss", proof)
        self.assertEqual(proof["plies"], 2)

    def test_a_budget_that_is_too_small_says_unknown(self):
        board = Board.from_rows(TWO_PATHS, turn=RED)
        proof = solve(board, max_plies=2)
        self.assertEqual(proof["outcome"], "unknown", proof)
        self.assertFalse(proof["complete"])
        self.assertTrue(proof["reason"])
        self.assertIsNone(proof["plies"])

    def test_the_reported_position_is_proven_lost_for_both_paths(self):
        board = Board.from_rows(TWO_PATHS, turn=RED)
        root = solve(board)
        self.assertEqual((root["outcome"], root["plies"]), ("loss", 8))
        children = {move.notation(): solve(board.resulting_board(move))
                    for move in board.generate_moves()}
        for move in board.generate_moves():
            child = children[move.notation()]
            self.assertEqual(child["outcome"], "win")        # the opponent wins
        # best play on the losing side is the *longest* resistance, +1 for our move
        longest = max(child["plies"] for child in children.values())
        self.assertEqual(longest + 1, root["plies"])
        self.assertEqual(children["b2xb6"]["plies"] + 1, root["plies"])
        self.assertEqual(children["b2xh4"]["plies"] + 1, 2)

    def test_the_proof_matches_an_independent_search(self):
        # two implementations, the same answer, for both colours
        for rows, turn in ((TWO_PATHS, RED), (KING_EIGHT, BLACK),
                           (KING_EIGHT_MIRRORED, RED), (TWO_LENGTHS, RED)):
            board = Board.from_rows(list(rows), turn=turn)
            proof = solve(board)
            outcome, plies = reference_result(rows, turn)
            self.assertEqual((proof["outcome"], proof["plies"]), (outcome, plies),
                             (turn, rows))

    def test_every_move_of_the_loser_loses(self):
        # the point of the proof: not one sample line - **all** of the loser's moves
        board = Board.from_rows(TWO_PATHS, turn=RED)
        for move in board.generate_moves():
            child = solve(board.resulting_board(move))
            self.assertEqual(child["outcome"], "win", move.notation())
            self.assertGreater(child["plies"], 0)

    def test_a_bad_defence_can_still_flip_a_proven_win(self):
        # the proof is about *best* play: after b2xh4 black may not play f6-e5,
        # because that line is a proven win for red - a sample line proves nothing
        board = Board.from_rows(TWO_PATHS, turn=RED)
        move = next(m for m in board.generate_moves() if m.notation() == "b2xh4")
        child = board.resulting_board(move)
        self.assertEqual(solve(child)["outcome"], "win")        # black wins
        blunder = next(m for m in child.generate_moves() if m.notation() == "f6-e5")
        after = child.resulting_board(blunder)
        self.assertEqual(solve(after)["outcome"], "win")        # ...but red wins here
        self.assertEqual(solve(after)["side"], "red")

    def test_the_best_line_is_legal_and_ends_at_the_stated_distance(self):
        board = Board.from_rows(TWO_PATHS, turn=RED)
        line = best_line(board)
        self.assertEqual(line["outcome"], "loss")
        self.assertEqual(len(line["moves"]), line["plies"])
        work = board.copy()
        for notation in line["moves"]:
            move = next((m for m in work.generate_moves()
                         if m.notation() == notation), None)
            self.assertIsNotNone(move, notation)               # every move is legal
            work.make_move(move)
        self.assertFalse(work.generate_moves())                # the loser is stuck
        self.assertEqual(work.piece_count(RED), 0)



class AnalysisConsistencyTests(unittest.TestCase):
    """Depth, score direction and the two panels must agree."""

    def test_the_score_is_from_the_side_to_move(self):
        # red is lost here; a position red wins must score positive
        lost = lab.analyze_position(position(TWO_PATHS), seconds=1.0, depth_cap=4)
        self.assertLess(lost["score"], 0)
        won_rows = [
            "........",
            ".....b..",
            "........",
            "....r...",
            "........",
            ".....r..",
            "........",
            "........",
        ]
        won = lab.analyze_position(position(won_rows), seconds=1.0, depth_cap=4)
        self.assertGreaterEqual(won["score"], 0)

    def test_all_rows_share_one_sign_for_one_side(self):
        comparison = lab.compare_root_moves(position(TWO_PATHS), depth=4,
                                            total_seconds=4.0)
        signs = {row["score"] > 0 for row in comparison["rows"]}
        self.assertEqual(len(signs), 1)                 # same normalisation
        self.assertIn(False, signs)                     # red is lost here

    def test_depth_counts_the_root_move(self):
        comparison = lab.compare_root_moves(position(TWO_PATHS), depth=3,
                                            total_seconds=4.0)
        self.assertEqual(comparison["target_depth"], 3)
        self.assertEqual(comparison["child_target_depth"], 2)
        for row in comparison["rows"]:
            # a child search of n plies plus the root move is n+1 plies from here
            self.assertEqual(row["depth"], row["child_depth"] + 1)
            self.assertEqual(row["requested_depth"], 3)
            self.assertGreaterEqual(row["depth"], 1)

    def test_the_reported_mate_distance_is_measured_from_the_root(self):
        comparison = lab.compare_root_moves(position(TWO_PATHS), depth=4,
                                            total_seconds=4.0)
        for row in comparison["rows"]:
            self.assertIsNotNone(row["proof"]["plies"])
            if row["mate_in"] is not None:
                # both the search's mate distance and the proof count plies from
                # this root, so they must agree here
                self.assertEqual(row["mate_in"], row["proof"]["plies"],
                                 row["move"]["notation"])

    def test_both_panels_agree_on_the_chosen_move(self):
        analysis = lab.analyze_position(position(TWO_PATHS), seconds=2.0,
                                        depth_cap=4)
        comparison = lab.compare_root_moves(position(TWO_PATHS), depth=4,
                                            total_seconds=4.0)
        rows = {row["move"]["notation"]: row for row in comparison["rows"]}
        row = rows[analysis["move"]["notation"]]
        # the same sign, the same proven result and the same distance; the ordinary
        # scores may differ, because the analysis panel searches deeper
        self.assertLess(analysis["score"], 0)
        self.assertLess(row["score"], 0)
        self.assertEqual(analysis["proof"]["outcome"], "loss")
        self.assertEqual(analysis["proof"]["plies"], 8)
        self.assertEqual(row["proof"]["plies"], analysis["proof"]["plies"])
        self.assertEqual(row["proof"]["outcome"], analysis["proof"]["outcome"])
        self.assertEqual(analysis["depth_meaning"], comparison["depth_meaning"])
        self.assertGreaterEqual(analysis["depth"], 1)
        self.assertGreaterEqual(row["depth"], 1)

    def test_a_proven_result_is_not_a_search_winner(self):
        comparison = lab.compare_root_moves(position(TWO_PATHS), depth=4,
                                            total_seconds=4.0)
        # the search stops early on a found mate, so the depths differ and no
        # ordinary "best move" may be named ...
        self.assertIsNone(comparison["best_by_search"])
        self.assertIn("ერთნაირი არ არის", comparison["best_by_search_note"])
        # ... while the proof names the move that resists longest
        self.assertEqual(comparison["best_proven"], "b2xb6")
        self.assertIn("დადასტურებული წაგება", comparison["best_proven_note"])

    def test_an_unproven_result_is_reported_as_such(self):
        result = lab.analyze_position(position(TWO_PATHS), seconds=0.3,
                                      depth_cap=3, proof_plies=2)
        self.assertEqual(result["proof"]["outcome"], "unknown")
        self.assertIn("არ არის დადასტურებული", lab.proof_summary(result["proof"]))


class ProofTextTests(unittest.TestCase):
    def test_the_summary_never_claims_an_unproven_result(self):
        self.assertIn("დადასტურებული მოგება",
                      lab.proof_summary({"outcome": "win", "plies": 3}))
        self.assertIn("დადასტურებული წაგება",
                      lab.proof_summary({"outcome": "loss", "plies": 2}))
        unknown = lab.proof_summary({"outcome": "unknown", "plies": None,
                                     "reason": "node limit"})
        self.assertIn("ამ სიღრმეზე", unknown)
        self.assertIn("არა მტკიცება", unknown)

