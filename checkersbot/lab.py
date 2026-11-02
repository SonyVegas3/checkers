"""Logic behind the interactive test board (``board_lab.py``).

Everything the test board *shows* is computed here from the real generators and
the real search, and nothing here draws anything - which keeps the whole thing
testable without a window:

* the position model with its problems (never silently repaired);
* every legal move and every completed capture path - all of them legal, since the
  number of pieces taken is never a filter;
* the hop-by-hop playback of one move, with the piece taken on each hop;
* the JokerGame rule text and which of its properties are visible here;
* one analysis and the equal-depth comparison of all root moves;
* the JSON + text log of an analysis.

The site's rules are *not* claimed to be verified here: the position model simply
carries free-text fields for the path that was seen on the site and a note.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .ai import MATE, MATE_THRESHOLD, CheckersAI, evaluate, evaluate_components
from .board import (
    BLACK,
    BOARD_SIZE,
    PLAYABLE_SQUARES,
    RED,
    Board,
    Move,
    col_of,
    row_of,
    square_name,
)
from .rules import JOKERGAME, describe as describe_rules
from .solver import solve

POSITION_FORMAT = "checkers-position/v1"
LOG_DIR = Path("test_board_logs")
POSITION_DIR = Path("test_positions")

GLYPH = {0: ".", 1: "r", 2: "R", -1: "b", -2: "B"}
FROM_GLYPH = {v: k for k, v in GLYPH.items()}

#: The one rule set of the project, written out for the test board (Georgian).
#: The last line marks the removal rule as the user's observation - nothing here
#: claims it was verified independently.
JOKERGAME_TEXT: List[str] = [
    "დაფა 8×8, 32 მუქი უჯრა, თითო მხარეს 12 ქვა; წითელი დადის პირველი.",
    "ჩვეულებრივი ქვა დადის დიაგონალურად წინ და დამკდება ბოლო რიგზე.",
    "დამკა მფრინავია: მოძრაობს და ჭამს დისტანციაზე, ნებისმიერ თავისუფალ უჯრაზე "
    "მოწინააღმდეგის ქვის უკან.",
    "მამაკაცი ჭამს წინ და უკან (ოთხივე დიაგონალზე), მაგრამ დადის მხოლოდ წინ.",
    "ჭამა სავალდებულოა: თუ ჭამა არსებობს, მშვიდი სვლა აკრძალულია.",
    "არჩეული ჯაჭვი უნდა გაგრძელდეს, სანამ იმ გზაზე კიდევ არსებობს ლეგალური ჭამა "
    "(განშტოებაზე ნებისმიერი ლეგალური გაგრძელება შეიძლება აირჩეს).",
    "თუმცა ყველაზე გრძელი (მაქსიმალური ქვების ამღები) გზა სავალდებულო არ არის: "
    "ყველა დასრულებული გზა ლეგალურია, ქვების რაოდენობის მიუხედავად.",
    "ჭამის დროს მფრინავ რიგზე მოხვედრილი მამაკაცი იქვე დამკდება და აგრძელებს "
    "ჭამას იმავე სვლაში.",
    "აღებული ქვა მაშინვე ტოვებს დაფას: დამკა დაეშვება მის უკან ნებისმიერ "
    "თავისუფალ უჯრაზე (მათ შორის ახლახან გათავისუფლებულზე) და აგრძელებს ჭამას "
    "საპირისპირო მიმართულებითაც.",
    "მხარე, რომელსაც ლეგალური სვლა არ აქვს, აგებს.",
    "შენიშვნა: მოცილებისა და „მაქსიმუმი სავალდებულო არ არის“ წესები ეფუძნება "
    "მომხმარებლის დაკვირვებას JokerGame-ზე; კოდით დამოუკიდებლად არ არის "
    "დადასტურებული.",
]




def engine_version() -> Dict[str, str]:
    """Version of the engine and of Python, for the log header."""
    import platform
    import sys

    try:
        from . import __version__
    except Exception:                                  # pragma: no cover
        __version__ = "unknown"
    return {
        "package": str(__version__),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def square_labels() -> List[str]:
    """``a1`` … ``h8`` for every square of the 8x8 board (row 0 = the top row)."""
    return [square_name(index) for index in range(BOARD_SIZE * BOARD_SIZE)]


def rows_of(board: Board) -> List[str]:
    """The 8 row strings of a board (``.bBr`` glyphs)."""
    return [
        "".join(GLYPH[board.cells[row * 8 + col]] for col in range(8))
        for row in range(8)
    ]


PLAYABLE_SQUARES_SET = frozenset(PLAYABLE_SQUARES)


@dataclass
class LabPosition:
    """A position as the test board holds it - hand-built, so history is unknown.

    The rules are **not** a field any more: the whole project plays JokerGame (see
    :mod:`checkersbot.rules`).  ``rules``/``removal`` are read-only views of that
    shared configuration, and ``migrated`` lists what a loaded file had to change.
    """

    rows: List[str] = field(default_factory=lambda: rows_of(Board()))
    turn: int = RED
    title: str = ""
    site_path: str = ""            # the path seen on the site, free text
    site_note: str = ""            # what the user noticed on the site
    origin: str = "hand-built"
    migrated: List[str] = field(default_factory=list)

    # -- the shared rule set ----------------------------------------------
    @property
    def rules(self) -> str:
        return JOKERGAME.name

    @property
    def removal(self) -> str:
        return JOKERGAME.removal

    # -- engine view ------------------------------------------------------
    def to_board(self) -> Board:
        return Board.from_rows(list(self.rows), turn=self.turn)

    @classmethod
    def from_board(cls, board: Board, **kwargs: Any) -> "LabPosition":
        return cls(rows=rows_of(board), turn=board.turn, **kwargs)

    # -- persistence ------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "format": POSITION_FORMAT,
            "title": self.title,
            "rows": list(self.rows),
            "turn": "red" if self.turn == RED else "black",
            "rules": JOKERGAME.name,
            "removal": JOKERGAME.removal,
            "rules_basis": describe_rules()["basis"],
            "site_path": self.site_path,
            "site_note": self.site_note,
            "origin": self.origin,
            "history": "unknown",
            "history_note": (
                "position was built by hand: earlier moves are unknown, so a "
                "repetition draw cannot be detected"
            ),
            "migrated": list(self.migrated),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LabPosition":
        rows = [str(row) for row in data.get("rows") or []]
        if len(rows) != 8 or any(len(row) != 8 for row in rows):
            raise ValueError("პოზიცია უნდა იყოს 8 სტრიქონი 8 სიმბოლოთი")
        for row in rows:
            for char in row:
                if char not in FROM_GLYPH:
                    raise ValueError("უცნობი სიმბოლო პოზიციაში: %r" % (char,))
        turn = RED if str(data.get("turn") or "red") == "red" else BLACK
        # a file written by an older version may name another rule set or removal
        # mode: the position is migrated to JokerGame and the caller is told, so the
        # old setting is never applied silently
        migrated: List[str] = []
        stored_rules = data.get("rules")
        if stored_rules is not None and str(stored_rules) != JOKERGAME.name:
            migrated.append("წესები: %s → %s" % (stored_rules, JOKERGAME.name))
        stored_removal = data.get("removal")
        if stored_removal is not None and str(stored_removal) != JOKERGAME.removal:
            migrated.append("მოცილება: %s → %s" % (stored_removal, JOKERGAME.removal))
        return cls(
            rows=rows, turn=turn,
            title=str(data.get("title") or ""),
            site_path=str(data.get("site_path") or ""),
            site_note=str(data.get("site_note") or ""),
            origin=str(data.get("origin") or "hand-built"),
            migrated=migrated,
        )

    def save(self, path: Any) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=1, ensure_ascii=False),
                          encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: Any) -> "LabPosition":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def position_problems(board: Board) -> List[str]:
    """Everything odd about this position, in words - never a silent repair."""
    problems: List[str] = []
    light = 0
    red_pieces = black_pieces = 0
    for index in range(BOARD_SIZE * BOARD_SIZE):
        piece = board.cells[index]
        if piece == 0:
            continue
        if index not in PLAYABLE_SQUARES_SET:
            light += 1
        if piece > 0:
            red_pieces += 1
        else:
            black_pieces += 1
    if light:
        problems.append(
            "მსუბუქ (თეთრ) უჯრებზე დგას %d ქვა - ძრავა მათ ვერ ანძრევს" % light
        )
    if red_pieces > 12:
        problems.append("წითელს აქვს %d ქვა (მაქსიმუმი 12)" % red_pieces)
    if black_pieces > 12:
        problems.append("შავს აქვს %d ქვა (მაქსიმუმი 12)" % black_pieces)
    if red_pieces == 0:
        problems.append("წითელს ქვა არ აქვს")
    if black_pieces == 0:
        problems.append("შავს ქვა არ აქვს")
    if red_pieces and black_pieces and not board.generate_moves():
        problems.append(
            "%s სვლის მხარეს ლეგალური სვლა არ აქვს - პოზიცია დასრულებულია (ეს მხარე აგებს)"
            % ("წითელი" if board.turn == RED else "შავი")
        )
    return problems


def _between(start: int, end: int) -> List[int]:
    """Squares strictly between two squares of one diagonal (empty otherwise)."""
    start_row, start_col = row_of(start), col_of(start)
    end_row, end_col = row_of(end), col_of(end)
    step_row = (end_row > start_row) - (end_row < start_row)
    step_col = (end_col > start_col) - (end_col < start_col)
    squares: List[int] = []
    row, col = start_row + step_row, start_col + step_col
    while (row, col) != (end_row, end_col):
        squares.append(row * 8 + col)
        row += step_row
        col += step_col
    return squares


def move_hops(board: Board, move: Move) -> List[Dict[str, Any]]:
    """Split one move into hops, with the piece taken on every hop.

    A hop jumps over exactly one enemy piece, so its victim is the enemy piece lying
    strictly between that hop's two squares (a flying king can jump from any
    distance, a man always from the next square).  Squares already cleared by this
    move are empty on the board, so they can appear between two hops - they simply
    carry no victim.  The board is not modified while the hops are read.
    """
    hops: List[Dict[str, Any]] = []
    taken: List[int] = []
    cleared: List[int] = []            # squares the mover has left behind
    previous = move.from_square
    landings = list(move.landing_path) or [move.to_square]
    for index, landing in enumerate(landings, 1):
        victim = None
        for square in _between(previous, landing):
            if square in cleared:
                continue               # the mover stood there a hop ago
            piece = board.cells[square]
            if piece != 0 and (piece > 0) != (board.turn > 0) and square not in taken:
                victim = square
                break
        if victim is not None:
            taken.append(victim)
        hops.append(
            {
                "index": index,
                "from": square_name(previous),
                "to": square_name(landing),
                "from_index": previous,
                "to_index": landing,
                "victim": square_name(victim) if victim is not None else None,
                "victim_index": victim,
            }
        )
        cleared.append(previous)
        previous = landing
    return hops


def move_row(board: Board, move: Move) -> Dict[str, Any]:
    """Everything the board shows about one move."""
    return {
        "notation": move.notation(),
        "from": square_name(move.from_square),
        "to": square_name(move.to_square),
        "from_index": move.from_square,
        "to_index": move.to_square,
        "captures": [square_name(square) for square in move.captured],
        "captured_count": move.captured_count,
        "promotes": bool(move.promotes),
        "is_capture": bool(move.captured),
        "hops": move_hops(board, move),
        "key": (move.from_square, move.to_square, move.captured, move.promotes),
    }


def legal_moves(board: Board) -> List[Dict[str, Any]]:
    """Every legal move, in the generator's own order."""
    return [move_row(board, move) for move in board.generate_moves()]


def capture_diagnostics(board: Board) -> Dict[str, Any]:
    """What the capture generator produced for this position - numbers, not guesses.

    There is no "excluded" concept any more: every completed path is legal, so the
    report lists them side by side and only notes how many pieces each one takes.
    """
    paths = []
    for move in board.capture_moves():
        row = move_row(board, move)
        row["legal"] = True
        row["reason"] = "ლეგალურია: დასრულებული გზა (ქვების რაოდენობა ფილტრი არ არის)"
        paths.append(row)
    longest = max((move.captured_count for move in board.capture_moves()), default=0)
    return {
        "has_capture": bool(paths),
        "longest_capture_required": bool(board.longest_capture_required),
        "promotion_continues": bool(board.promotion_continues),
        "flying_kings": bool(board.flying_kings),
        "men_capture_backwards": bool(board.men_capture_backwards),
        "removal": board.removal,
        "longest": longest,
        "path_count": len(paths),
        "legal_count": len(paths),
        "paths": paths,
        "quiet_moves": len(board.quiet_moves()) if not paths else 0,
    }


def _routes(board: Board) -> Dict[str, List[List[str]]]:
    """Legal moves as ``notation -> [route, route, ...]`` (one row per path)."""
    grouped: Dict[str, List[List[str]]] = {}
    for row in legal_moves(board):
        grouped.setdefault(row["notation"], []).append(
            [hop["to"] for hop in row["hops"]]
        )
    return grouped


def moves_with_same_ends(board: Board) -> List[List[Dict[str, Any]]]:
    """Groups of legal moves that share start and end square.

    Different jump chains can begin and finish on the same squares while taking
    different pieces; they are shown as separate paths, never merged.
    """
    groups: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
    for row in legal_moves(board):
        groups.setdefault((row["from_index"], row["to_index"]), []).append(row)
    return [group for group in groups.values() if len(group) > 1]


def _mate_in(score: int) -> Optional[int]:
    """Half-moves to the forced mate **from the position that was searched**.

    The engine's mate score is ``MATE - ply``, where ``ply`` counts half-moves from
    the root of *that* search (see ``ai._negamax``), so this is a distance in
    plies, not a score difference.  A caller that searched a child position has to
    add the root move back (``compare_root_moves`` does exactly that).
    """
    if abs(score) < MATE_THRESHOLD:
        return None
    return max(0, MATE - abs(score))


def _flip(outcome: str) -> str:
    """The same result seen from the other side."""
    return {"win": "loss", "loss": "win"}.get(outcome, outcome)


def result_side(turn: int) -> str:
    """``"red"``/``"black"`` for an engine turn value."""
    return "red" if turn > 0 else "black"


def proof_summary(proof: Optional[Dict[str, Any]]) -> str:
    """One line for a solver result - never claims more than was proven."""
    if not proof:
        return "დადასტურება არ ჩატარებულა"
    outcome = proof.get("outcome")
    if outcome == "win":
        return ("დადასტურებული მოგება: %d ნახევარსვლაში (საუკეთესო დაცვის "
                "წინააღმდეგ)" % proof["plies"])
    if outcome == "loss":
        return ("დადასტურებული წაგება: %d ნახევარსვლაში (საუკეთესო დაცვის "
                "წინააღმდეგ)" % proof["plies"])
    reason = proof.get("reason") or "უცნობი მიზეზი"
    return ("ამ სიღრმეზე წაგებად/მოგებად არ არის დადასტურებული (%s) — ეს შეფასებაა, "
            "არა მტკიცება" % reason)


#: Notes with which ``ai`` reports that the value is *not* a finished iteration.
_INCOMPLETE_NOTES = ("time limit", "soft time limit", "not evaluated",
                     "stopped by user", "no legal moves", "only move (not evaluated)")


def _value_is_exact(stats: Any) -> bool:
    """Is ``stats.score`` the value of a *completed* full-window iteration?

    The engine searches every iteration with an open window (the aspiration window
    is widened on failure), so a finished iteration is an exact value of the
    depth-limited search - but only then: a timeout, a user stop or "not evaluated"
    leaves a bound or nothing at all.
    """
    if stats.score is None or stats.depth <= 0:
        return False
    return not any(note in (stats.note or "") for note in _INCOMPLETE_NOTES)


def _continuation_table(ai: CheckersAI, board: Board, move: Move,
                        plies: int = 4) -> List[str]:
    """A probable continuation taken from the transposition table, not a claim."""
    work = board.copy()
    work.make_move(move)
    found: List[str] = []
    for _ in range(max(0, plies)):
        entry = ai._table.get((tuple(work.cells), work.turn))
        if entry is None or entry[3] is None:
            break
        nxt = entry[3]
        if nxt not in work.generate_moves():
            break
        found.append(nxt.notation())
        work.make_move(nxt)
    return found


def analyze_position(position: LabPosition, *, seconds: float = 3.0,
                     depth_cap: int = 60, should_stop: Any = None,
                     proof_plies: int = 40,
                     proof_nodes: int = 400_000) -> Dict[str, Any]:
    """One search on the current position plus a separate result proof.

    Two different things are reported and never mixed:

    * the **search** - ``score``, ``depth`` (half-moves searched *including the
      root move*; a whole jump chain is one ply, because it is one move) and
      ``value_exact`` (a finished iteration, i.e. an exact value *for that depth*);
    * the **proof** - ``proof`` from :func:`checkersbot.solver.solve`: the
      game-theoretic result and the number of half-moves to the end with best play,
      or ``outcome="unknown"`` when the budget was not enough.

    ``forced`` is *only* the proof's outcome (win/loss); a mate score from the
    search is reported as ``mate_in`` and is not a claim about the result.
    """
    board = position.to_board()
    moves = board.generate_moves()
    result: Dict[str, Any] = {
        "side": "red" if board.turn == RED else "black",
        "rules": position.rules,
        "requested_seconds": float(seconds),
        "depth_cap": int(depth_cap),
        "depth_meaning": "ნახევარსვლები საწყისი სვლის ჩათვლით (ჯაჭვი = 1 სვლა)",
        "static": evaluate(board),
        "components": evaluate_components(board),
        "legal_count": len(moves),
        "move": None,
    }
    if not moves:
        proof = solve(board, max_plies=proof_plies, node_limit=proof_nodes)
        result.update({"score": None, "depth": 0, "target_depth": 0, "nodes": 0,
                       "seconds": 0.0, "note": "ლეგალური სვლა არ არის",
                       "value_exact": False,
                       "forced": proof["outcome"] == "loss",
                       "mate_in": 0, "continuation": [], "proof": proof})
        return result

    ai = CheckersAI(max_depth=int(depth_cap), time_limit=float(seconds) or None)
    ai.should_stop = should_stop
    # the cap is a *base* depth: the engine deepens it in endgames
    # (``dynamic_depth``), so both numbers are reported
    target_depth = ai.dynamic_depth(board)
    started = time.perf_counter()
    # the lab always searches - even when only one move is legal - so the score it
    # prints is a real search result rather than the bot's "only move" shortcut
    move = ai.choose_move(board, quick_single=False)
    elapsed = time.perf_counter() - started
    stats = ai.stats
    score = int(stats.score) if move is not None else None
    mate_in = _mate_in(score) if score is not None else None
    # the result proof is a separate question, answered without any evaluation
    proof = solve(board, max_plies=proof_plies, node_limit=proof_nodes,
                  should_stop=should_stop)
    result.update(
        {
            "move": move_row(board, move) if move is not None else None,
            "score": score,
            "depth": int(stats.depth),
            "target_depth": int(target_depth),
            "nodes": int(stats.nodes),
            "seconds": round(elapsed, 3),
            "note": str(stats.note or ""),
            "value_exact": _value_is_exact(stats),
            "mate_in": mate_in,
            "proof": proof,
            "forced": proof["outcome"] in ("win", "loss"),
            "continuation": (_continuation_table(ai, board, move)
                             if move is not None else []),
            "continuation_source": "ტრანსპოზიციური ცხრილიდან (სავარაუდო)",
            "score_sign": "დადებითი = სვლაზე მყოფი მხარისთვის კარგია",
        }
    )
    return result


def compare_root_moves(position: LabPosition, *, depth: int = 8,
                       total_seconds: float = 6.0,
                       should_stop: Any = None,
                       proof_plies: int = 40,
                       proof_nodes: int = 400_000) -> Dict[str, Any]:
    """Score every legal first move with one depth target and one shared clock.

    ``depth`` (and every row's ``depth``) means **half-moves from this position,
    including the root move** - the same unit as the analysis panel, so the two
    panels can be compared.  A whole jump chain is one ply.  The child search is
    therefore given ``depth - 1`` plies, and each row reports the child's number
    rebased (``1 + child depth``) together with the raw ``child_depth``.

    Three separate facts per row, never merged:

    * ``value_exact`` - the score is the value of a finished iteration (exact *for
      that depth*, not a bound);
    * ``depth_complete`` - the requested depth was reached, so the score can be
      compared with the other rows;
    * ``proven`` - :mod:`checkersbot.solver` proved the game-theoretic result and
      ``proof.plies`` counts the half-moves to the end from this root.

    A "best move" is named from the search only when every row is exact at the
    *same* depth; a proven result is reported separately.
    """
    board = position.to_board()
    moves = board.generate_moves()
    requested = max(1, int(depth))
    child_depth = max(1, requested - 1)
    deadline = time.perf_counter() + max(0.1, float(total_seconds))
    rows: List[Dict[str, Any]] = []
    for move in moves:
        row = {
            "move": move_row(board, move),
            "score": None,
            "depth": 0,                 # plies from the root, root move included
            "child_depth": 0,           # what the child search itself reported
            "requested_depth": requested,
            "value_exact": False,
            "depth_complete": False,
            "mate_in": None,
            "proof": None,
            "proven": False,
            "nodes": 0,
            "seconds": 0.0,
            "note": "",
            "continuation": [],
        }
        rows.append(row)
        if should_stop is not None and should_stop():
            row["note"] = "შეწყდა მომხმარებლის მიერ"
            continue
        remaining = deadline - time.perf_counter()
        if remaining <= 0.02:
            row["note"] = "დრო ამოიწურა - არ შეფასდა"
            continue

        child = board.copy()
        child.make_move(move)
        proof = solve(child, max_plies=proof_plies, node_limit=proof_nodes,
                      should_stop=should_stop)
        row["proof"] = {
            "outcome": _flip(proof["outcome"]),
            "plies": (None if proof["plies"] is None else proof["plies"] + 1),
            "nodes": proof["nodes"],
            "complete": proof["complete"],
            "reason": proof["reason"],
            "side": result_side(board.turn),
        }
        row["proven"] = bool(row["proof"]["complete"]
                             and row["proof"]["outcome"] in ("win", "loss"))

        if not child.generate_moves():
            # the opponent has no reply: the root move already won the game
            row.update({"score": MATE, "depth": 1, "child_depth": 0,
                        "value_exact": True, "depth_complete": True,
                        "mate_in": 1, "note": "მეტოქეს სვლა არ აქვს - მოგება"})
            continue
        ai = CheckersAI(max_depth=child_depth, time_limit=max(0.05, remaining))
        started = time.perf_counter()
        # a real search even when the reply is forced: no shortcut, no invented score
        reply = ai.choose_move(child, quick_single=False)
        elapsed = time.perf_counter() - started
        stats = ai.stats
        raw_score = stats.score
        # every row is seen from the side to move at the root: same normalisation
        score = -int(raw_score) if raw_score is not None else None
        rope = _mate_in(score) if score is not None else None
        row.update({
            "score": score,
            "child_depth": int(stats.depth),
            "depth": int(stats.depth) + 1,        # + the root move
            "nodes": int(stats.nodes),
            "seconds": round(elapsed, 3),
            "value_exact": _value_is_exact(stats),
            "depth_complete": int(stats.depth) >= child_depth,
            # a mate distance found in the child is one ply longer from this root
            "mate_in": None if rope is None else rope + 1,
            "note": str(stats.note or "") if score is not None else "არ შეფასდა",
            "continuation": (_continuation_table(ai, board, move)
                             if reply is not None else []),
        })

    comparable = [row for row in rows if row["value_exact"] and row["depth_complete"]]
    depths = {row["depth"] for row in comparable}
    best_by_search = None
    if comparable and len(comparable) == len(rows) and len(depths) == 1:
        best_by_search = max(comparable,
                             key=lambda row: row["score"])["move"]["notation"]
    depth_note = ""
    if best_by_search:
        depth_note = ("საუკეთესო ჩვეულებრივი შეფასებიდან: ყველა სვლა დასრულდა "
                      "იმავე სიღრმეზე (%d) და ყველა მნიშვნელობა ზუსტია"
                      % depths.pop())
    else:
        depth_note = ("ჩვეულებრივი შეფასებიდან გამარჯვებული არ გამოცხადდა: "
                      "სვლების სიღრმეები ან სიზუსტე ერთნაირი არ არის")

    proven_rows = [row for row in rows if row["proven"]]
    best_proven = None
    if proven_rows and len(proven_rows) == len(rows):
        outcomes = {row["proof"]["outcome"] for row in proven_rows}
        if len(outcomes) == 1:
            outcome = outcomes.pop()
            if outcome == "win":
                best = min(proven_rows, key=lambda row: row["proof"]["plies"])
                proof_note = ("დადასტურებული მოგება: %s — %d ნახევარსვლაში "
                              "(საუკეთესო დაცვის წინააღმდეგ)"
                              % (best["move"]["notation"], best["proof"]["plies"]))
            else:
                best = max(proven_rows, key=lambda row: row["proof"]["plies"])
                proof_note = ("დადასტურებული წაგება: ყველა სვლა აგებს; ყველაზე "
                              "ხანგრძლივი წინააღმდეგობა %s — %d ნახევარსვლაში"
                              % (best["move"]["notation"], best["proof"]["plies"]))
            best_proven = best["move"]["notation"]
        else:
            proof_note = ("დადასტურებულია, მაგრამ შედეგები ერთნაირი არ არის: %s"
                          % ", ".join("%s=%s" % (row["move"]["notation"],
                                                 row["proof"]["outcome"])
                                      for row in proven_rows))
    elif proven_rows:
        proof_note = ("ნაწილობრივ დადასტურებული: %d/%d სვლა"
                      % (len(proven_rows), len(rows)))
    else:
        proof_note = "ვერც ერთი სვლის შედეგი არ დადასტურდა ამ ბიუჯეტში"

    rows.sort(key=lambda row: (not row["value_exact"], -(row["score"] or -2 * MATE)))
    return {
        "side": result_side(board.turn),
        "target_depth": requested,
        "depth_meaning": "ნახევარსვლები საწყისი სვლის ჩათვლით (ჯაჭვი = 1 სვლა)",
        "child_target_depth": child_depth,
        "budget_seconds": float(total_seconds),
        "rows": rows,
        "exact": comparable,
        "inexact": [row for row in rows if not (row["value_exact"]
                                                and row["depth_complete"])],
        "best_by_search": best_by_search,
        "best_by_search_note": depth_note,
        "best_proven": best_proven,
        "best_proven_note": proof_note,
    }


def write_analysis_log(position: LabPosition, analysis: Dict[str, Any], *,
                       comparison: Optional[Dict[str, Any]] = None,
                       log_dir: Any = LOG_DIR) -> Dict[str, str]:
    """Write the JSON record and the human-readable text of one analysis."""
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-%03d" % (int(time.time() * 1000) % 1000)
    board = position.to_board()
    diagnostics = capture_diagnostics(board)
    move_name = analysis["move"]["notation"] if analysis.get("move") else None
    payload = {
        "format": "checkers-lab-analysis/v1",
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "engine": engine_version(),
        "position": position.to_dict(),
        "side_to_move": "red" if board.turn == RED else "black",
        "rules": describe_rules(),
        "removal": position.removal,
        "removal_basis": JOKERGAME.removal_basis,
        "problems": position_problems(board),
        "capture_diagnostics": diagnostics,
        "legal_moves": [
            {key: value for key, value in row.items() if key != "key"}
            for row in legal_moves(board)
        ],
        "same_start_and_end": [
            [row["notation"] for row in group] for group in moves_with_same_ends(board)
        ],
        "analysis": {key: value for key, value in analysis.items() if key != "move"}
        | {"move": move_name},
        "comparison": comparison,
    }
    json_path = directory / ("analysis_%s.json" % stamp)
    json_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False),
                         encoding="utf-8")
    text_path = directory / ("analysis_%s.txt" % stamp)
    text_path.write_text(_analysis_text(payload), encoding="utf-8")
    return {"json": str(json_path), "text": str(text_path)}


def _analysis_text(payload: Dict[str, Any]) -> str:
    """The same record as readable Georgian text."""
    position = payload["position"]
    analysis = payload["analysis"]
    diagnostics = payload["capture_diagnostics"]
    engine = payload["engine"]
    lines: List[str] = []
    lines.append("შაშის სატესტო დაფა — ანალიზის ჩანაწერი")
    lines.append("დრო: %s" % payload["when"])
    lines.append("ძრავა: ვერსია %s, Python %s, %s"
                 % (engine["package"], engine["python"], engine["platform"]))
    lines.append("")
    lines.append("1) პოზიცია (სვლის მხარე: %s, წესები: %s, მოცილება: %s)"
                 % ("წითელი" if payload["side_to_move"] == "red" else "შავი",
                    position["rules"], position.get("removal", "end")))
    for index, row in enumerate(position["rows"], 1):
        lines.append("   %d  %s" % (index, row))
    lines.append("   ისტორია: უცნობია (დაფა ხელით აწყობილია) — გამეორებით ფრეს "
                 "დადგენა შეზღუდულია")
    lines.append("   შენიშვნა: საიტის მოცილების რეჟიმი დაუდასტურებელია — იხილე "
                 "მე-6 პუნქტი")
    if position.get("site_path") or position.get("site_note"):
        lines.append("   საიტზე ნანახი გზა: %s" % (position.get("site_path") or "—"))
        lines.append("   შენიშვნა საიტზე: %s" % (position.get("site_note") or "—"))
    if payload["problems"]:
        lines.append("   პრობლემები: " + " | ".join(payload["problems"]))
    lines.append("")
    lines.append("2) ჭამის დიაგნოსტიკა")
    lines.append("   ჭამა არსებობს: %s" % ("დიახ" if diagnostics["has_capture"] else "არა"))
    lines.append("   ყველაზე გრძელი ჭამა: %d ქვა — მაგრამ მაქსიმუმი სავალდებულო არ "
                 "არის: ყველა დასრულებული გზა ლეგალურია"
                 % diagnostics["longest"])
    for row in diagnostics["paths"]:
        lines.append("   - %-14s იღებს %d, აღებული: %s, %s"
                     % (row["notation"], row["captured_count"],
                        ", ".join(row["captures"]) or "—", row["reason"]))
    lines.append("")
    lines.append("3) ლეგალური სვლები (%d)" % len(payload["legal_moves"]))
    for row in payload["legal_moves"]:
        hops = " → ".join(
            "%s%s" % (hop["to"], ("x" + hop["victim"]) if hop["victim"] else "")
            for hop in row["hops"]
        )
        lines.append("   %-14s %s%s" % (row["notation"], hops,
                                        " (დამკა)" if row["promotes"] else ""))
    if payload["same_start_and_end"]:
        lines.append("   ერთი და იმავე დასაწყისი/დასასრული, სხვადასხვა გზა: %s"
                     % payload["same_start_and_end"])
    lines.append("")
    lines.append("4) ბოტის ანალიზი (ძიებითი შეფასება)")
    lines.append("   მოთხოვნილი დრო: %.1f წმ, სიღრმის ჭერი: %s, ფაქტობრივი მიზანი "
                 "(ენდშპილის გაღრმავებით): %s"
                 % (analysis["requested_seconds"], analysis["depth_cap"],
                    analysis.get("target_depth")))
    lines.append("   სიღრმის ერთეული: %s"
                 % analysis.get("depth_meaning",
                                "ნახევარსვლები საწყისი სვლის ჩათვლით"))
    if analysis.get("move"):
        lines.append("   არჩეული სვლა: %s" % analysis["move"])
        lines.append("   შეფასება: %s (%s)" % (analysis["score"], analysis["score_sign"]))
        lines.append("   დასრულებული სიღრმე: %s, კვანძები: %s, დრო: %.2f წმ, შენიშვნა: %s"
                     % (analysis["depth"], analysis["nodes"], analysis["seconds"],
                        analysis["note"] or "—"))
        lines.append("   მნიშვნელობა ზუსტია ამ სიღრმეზე: %s (ეს არ არის შედეგის "
                     "მტკიცება)"
                     % ("დიახ" if analysis.get("value_exact") else "არა"))
        if analysis.get("mate_in") is not None:
            lines.append("   ძიებაში ნანახი მატი: %d ნახევარსვლაში (ამ ძიების "
                         "სიღრმეში ნანახი, არა დამოუკიდებელი მტკიცება)"
                         % analysis["mate_in"])
        if analysis.get("continuation"):
            lines.append("   სავარაუდო გაგრძელება (%s): %s"
                         % (analysis["continuation_source"],
                            " ".join(analysis["continuation"])))
    else:
        lines.append("   ლეგალური სვლა არ არის")
    lines.append("   სტატიკური შეფასება: %s = %s"
                 % (analysis.get("static"), analysis.get("components")))
    lines.append("   (სტატიკური შეფასება ცალკეა ძიებით მიღებული ქულისგან)")
    lines.append("   დამოუკიდებელი მტკიცება (სრული გადალახვა, შეფასების გარეშე): %s"
                 % proof_summary(analysis.get("proof")))
    if payload.get("comparison"):
        comparison = payload["comparison"]
        lines.append("")
        lines.append("5) ყველა სვლის შედარება (სიღრმე %d ფესვიდან; შვილის სამიზნე "
                     "%d; დრო %.1f წმ)"
                     % (comparison["target_depth"],
                        comparison.get("child_target_depth", comparison["target_depth"]),
                        comparison["budget_seconds"]))
        for row in comparison["rows"]:
            marks = []
            marks.append("მნიშვნელობა ზუსტი" if row["value_exact"] else "მნიშვნელობა არაა ზუსტი")
            marks.append("სიღრმე დასრულდა" if row["depth_complete"]
                         else "სიღრმე არ დასრულდა")
            marks.append("დადასტურებული" if row["proven"] else "არ არის დადასტურებული")
            lines.append("   %-14s ქულა=%-8s სიღრმე=%-3s (შვილი %-3s) კვანძები=%-7s "
                         "%.2f წმ [%s] %s"
                         % (row["move"]["notation"], row["score"], row["depth"],
                            row["child_depth"], row["nodes"], row["seconds"],
                            "; ".join(marks), row["note"]))
            if row.get("proof"):
                lines.append("      მტკიცება: %s (მოძებნა: %d კვანძი)"
                             % (proof_summary(row["proof"]), row["proof"]["nodes"]))
        lines.append("   " + comparison.get("best_by_search_note", ""))
        if comparison.get("best_by_search"):
            lines.append("   საუკეთესო ჩვეულებრივი შეფასებიდან: %s"
                         % comparison["best_by_search"])
        lines.append("   " + comparison.get("best_proven_note", ""))
        if comparison.get("best_proven"):
            lines.append("   დადასტურებული შედეგიდან საუკეთესო წინააღმდეგობა: %s"
                         % comparison["best_proven"])
    lines.append("")
    lines.append("6) JokerGame-ის წესები, რომლითაც ეს ანალიზი შესრულდა")
    for item in JOKERGAME_TEXT:
        lines.append("   • " + item)
    return "\n".join(lines) + "\n"
