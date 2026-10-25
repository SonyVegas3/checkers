"""Checkers rules engine for 8x8 boards: American checkers and Russian draughts.

Board layout
------------
The board is a flat list of 64 integers, ``index = row * 8 + col`` where
row 0 is the top row (rank 8) and col 0 is the left column (file a).
Pieces only ever stand on the 32 dark squares, i.e. squares with
``(row + col) % 2 == 1``.

Pieces
------
=============  =====
``EMPTY``      0
``RED_MAN``    1
``RED_KING``   2
``BLACK_MAN``  -1
``BLACK_KING`` -2
=============  =====

RED starts at the bottom (rows 5-7) and moves upwards (towards row 0),
BLACK starts at the top (rows 0-2) and moves downwards.  RED moves first.

Rules
-----
Rule sets are chosen with ``Board(..., rules=...)``:

``english`` (American checkers, the default)
    * men step one square diagonally forward, kings step one square in any
      diagonal direction (no flying kings);
    * a capture is a jump over an adjacent enemy piece onto the empty square
      directly behind it, several jumps are chained into a single move.

``russian`` (Russian draughts / ``damka``)
    * men step and capture forwards only, exactly as above;
    * **kings fly**: they slide any number of empty squares along a diagonal,
      and when they capture they jump an enemy piece from any distance and
      may land on any free square behind it, so one king can sweep several
      pieces along a diagonal in a single move;
    * captured pieces stay on the board until the move ends, so a king may
      not pass over the same piece twice nor land on a square it already took.

``brazilian`` (Brazilian draughts / international draughts)
    * everything from ``russian``;
    * men also **capture backwards** (they still only move forwards);
    * the **longest capture** is compulsory.

``joker`` (what jokergame.cc actually plays - Brazilian + Russian promotion)
    * everything from ``brazilian``;
    * a man that lands on its far row **during a capture** is crowned on the
      spot and **keeps capturing as a (flying) king in the same move**, so one
      move can contain a crown and further jumps behind it.  ``Move.promotes``
      is set and the piece finishes the move as a king.

Common to all rule sets:

* capturing is mandatory: as soon as a capture exists it must be played
  (the player may pick any of the available capture sequences);
* a man that reaches the far row outside a capture is crowned and the move ends;
* a side that cannot move loses.

The module is written for speed: move generation uses pre-computed
neighbour tables and the search in :mod:`ai` mutates the board in place
through :meth:`Board.make_move` / :meth:`Board.unmake_move`.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

BOARD_SIZE = 8
SQUARE_COUNT = 64
PIECES_PER_SIDE = 12

EMPTY = 0
MAN = 1
KING = 2
RED = 1
BLACK = -1

RED_MAN = RED * MAN
RED_KING = RED * KING
BLACK_MAN = BLACK * MAN
BLACK_KING = BLACK * KING

FILES = "abcdefgh"

_GLYPHS = {EMPTY: " ", RED_MAN: "r", RED_KING: "R", BLACK_MAN: "b", BLACK_KING: "B"}
_LETTERS = {" ": EMPTY, ".": EMPTY, "r": RED_MAN, "R": RED_KING, "b": BLACK_MAN, "B": BLACK_KING}
_CROWN_ROW = {RED: 0, BLACK: BOARD_SIZE - 1}


def _index(row: int, col: int) -> int:
    return row * BOARD_SIZE + col


def row_of(square: int) -> int:
    return square >> 3


def col_of(square: int) -> int:
    return square & 7


def is_playable_square(square: int) -> bool:
    """True for the 32 dark squares pieces are allowed to stand on."""
    if not 0 <= square < SQUARE_COUNT:
        return False
    return (row_of(square) + col_of(square)) % 2 == 1


PLAYABLE_SQUARES: Tuple[int, ...] = tuple(s for s in range(SQUARE_COUNT) if is_playable_square(s))


def square_name(square: int) -> str:
    """Human readable name, e.g. ``square_name(43) == 'c3'``."""
    return f"{FILES[col_of(square)]}{BOARD_SIZE - row_of(square)}"


def parse_square(name: str) -> int:
    """Inverse of :func:`square_name`."""
    text = name.strip().lower()
    if len(text) != 2 or text[0] not in FILES or text[1] not in "12345678":
        raise ValueError(f"invalid square name: {name!r}")
    return _index(BOARD_SIZE - int(text[1]), FILES.index(text[0]))


def color_of(piece: int) -> int:
    """``RED``, ``BLACK`` or ``EMPTY`` (0) for an empty square."""
    if piece > 0:
        return RED
    if piece < 0:
        return BLACK
    return EMPTY


def kind_of(piece: int) -> int:
    """``MAN``, ``KING`` or ``EMPTY`` (0)."""
    return abs(piece)


def is_king(piece: int) -> bool:
    return piece == RED_KING or piece == BLACK_KING


def is_man(piece: int) -> bool:
    return piece == RED_MAN or piece == BLACK_MAN


RULES = ("english", "russian", "brazilian", "joker")
DIAGONALS: Tuple[Tuple[int, int], ...] = ((-1, -1), (-1, 1), (1, -1), (1, 1))


def _build_rays() -> Tuple[Tuple[Tuple[int, ...], ...], ...]:
    """``RAYS[square][direction]``: the squares along one diagonal, nearest first.

    Used by the flying kings of the Russian rules, which slide and capture at a
    distance instead of stepping one square at a time.
    """
    rays = []
    for square in range(SQUARE_COUNT):
        row, col = row_of(square), col_of(square)
        per_square = []
        for d_row, d_col in DIAGONALS:
            squares = []
            r, c = row + d_row, col + d_col
            while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE:
                squares.append(_index(r, c))
                r += d_row
                c += d_col
            per_square.append(tuple(squares))
        rays.append(tuple(per_square))
    return tuple(rays)


RAYS = _build_rays()


def promote_piece(piece: int) -> int:
    """Crown a man (kings are returned unchanged)."""
    if abs(piece) == MAN:
        return piece * KING
    return piece


def _build_move_tables():
    """Pre-compute step / jump neighbours for kings, red men and black men.

    ``*_STEPS[square]`` is a tuple of reachable squares, ``*_JUMPS[square]``
    is a tuple of ``(jumped_square, landing_square)`` pairs.
    """
    king_steps: List[List[int]] = [[] for _ in range(SQUARE_COUNT)]
    red_steps: List[List[int]] = [[] for _ in range(SQUARE_COUNT)]
    black_steps: List[List[int]] = [[] for _ in range(SQUARE_COUNT)]
    king_jumps: List[List[Tuple[int, int]]] = [[] for _ in range(SQUARE_COUNT)]
    red_jumps: List[List[Tuple[int, int]]] = [[] for _ in range(SQUARE_COUNT)]
    black_jumps: List[List[Tuple[int, int]]] = [[] for _ in range(SQUARE_COUNT)]

    for square in PLAYABLE_SQUARES:
        row, col = row_of(square), col_of(square)
        for d_row in (-1, 1):
            for d_col in (-1, 1):
                step_row, step_col = row + d_row, col + d_col
                jump_row, jump_col = row + 2 * d_row, col + 2 * d_col
                if 0 <= step_row < BOARD_SIZE and 0 <= step_col < BOARD_SIZE:
                    target = _index(step_row, step_col)
                    king_steps[square].append(target)
                    if d_row < 0:
                        red_steps[square].append(target)
                    else:
                        black_steps[square].append(target)
                if 0 <= jump_row < BOARD_SIZE and 0 <= jump_col < BOARD_SIZE:
                    over = _index(step_row, step_col)
                    land = _index(jump_row, jump_col)
                    king_jumps[square].append((over, land))
                    if d_row < 0:
                        red_jumps[square].append((over, land))
                    else:
                        black_jumps[square].append((over, land))

    return (
        tuple(tuple(x) for x in king_steps),
        tuple(tuple(x) for x in red_steps),
        tuple(tuple(x) for x in black_steps),
        tuple(tuple(x) for x in king_jumps),
        tuple(tuple(x) for x in red_jumps),
        tuple(tuple(x) for x in black_jumps),
    )


(
    KING_STEPS,
    RED_STEPS,
    BLACK_STEPS,
    KING_JUMPS,
    RED_JUMPS,
    BLACK_JUMPS,
) = _build_move_tables()


class Move:
    """A single legal move (possibly a whole chain of jumps)."""

    __slots__ = ("from_square", "to_square", "captured", "promotes", "landing_path")

    def __init__(
        self,
        from_square: int,
        to_square: int,
        captured: Sequence[int] = (),
        promotes: bool = False,
        landing_path: Sequence[int] = (),
    ) -> None:
        self.from_square = from_square
        self.to_square = to_square
        self.captured: Tuple[int, ...] = tuple(captured)
        self.promotes = promotes
        # the squares the piece touches in order (one per captured piece); a
        # flying king may land far behind the piece it takes, so this cannot be
        # reconstructed from the captured squares alone
        self.landing_path: Tuple[int, ...] = tuple(landing_path) or (
            (to_square,) if captured else ()
        )

    @property
    def is_capture(self) -> bool:
        return bool(self.captured)

    @property
    def captured_count(self) -> int:
        return len(self.captured)

    def notation(self) -> str:
        """``c3-d4`` for a step, ``c3xe5`` for a jump."""
        separator = "x" if self.captured else "-"
        return f"{square_name(self.from_square)}{separator}{square_name(self.to_square)}"

    def to_dict(self) -> Dict[str, object]:
        """JSON friendly representation (used by the web bot later)."""
        return {
            "from": square_name(self.from_square),
            "to": square_name(self.to_square),
            "captures": [square_name(s) for s in self.captured],
            "path": [square_name(s) for s in self.landing_path],
            "promotes": self.promotes,
            "notation": self.notation(),
        }

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Move):
            return NotImplemented
        return (
            self.from_square == other.from_square
            and self.to_square == other.to_square
            and self.captured == other.captured
            and self.promotes == other.promotes
        )

    def __hash__(self) -> int:
        return hash((self.from_square, self.to_square, self.captured, self.promotes))

    def __str__(self) -> str:
        return self.notation()

    def __repr__(self) -> str:
        return f"Move({self.notation()!r}, captured={len(self.captured)}, promotes={self.promotes})"


class Board:
    """A mutable 8x8 checkers position plus the side to move and the rules."""

    __slots__ = ("cells", "turn", "rules")

    def __init__(
        self,
        cells: Optional[Sequence[int]] = None,
        turn: int = RED,
        rules: str = "english",
    ) -> None:
        if cells is None:
            self.cells = _initial_cells()
        else:
            if len(cells) != SQUARE_COUNT:
                raise ValueError(f"expected {SQUARE_COUNT} cells, got {len(cells)}")
            self.cells = list(cells)
        if turn not in (RED, BLACK):
            raise ValueError("turn must be RED or BLACK")
        if rules not in RULES:
            raise ValueError(f"rules must be one of {', '.join(RULES)}")
        self.turn = turn
        self.rules = rules

    @property
    def flying_kings(self) -> bool:
        """True when kings slide and capture at a distance (Russian/Brazilian)."""
        return self.rules in ("russian", "brazilian", "joker")

    @property
    def men_capture_backwards(self) -> bool:
        """True when men may capture in all four directions (Brazilian rules)."""
        return self.rules in ("brazilian", "joker")

    @property
    def maximum_capture(self) -> bool:
        """True when the longest capture sequence is compulsory (Brazilian rules)."""
        return self.rules in ("brazilian", "joker")

    @property
    def promotion_continues(self) -> bool:
        """True when a man crowned *during* a capture keeps capturing (joker).

        jokergame.cc crowns the man on the promotion row in the middle of the
        jump sequence and lets it carry on as a flying king, so one move can be
        "man jumps, is crowned, the new king jumps again".
        """
        return self.rules == "joker"

    # ------------------------------------------------------------------
    # construction helpers
    # ------------------------------------------------------------------
    @classmethod
    def start(cls) -> "Board":
        """The standard starting position with RED to move."""
        return cls()

    @classmethod
    def from_matrix(
        cls, matrix: Sequence[Sequence[int]], turn: int = RED, rules: str = "english"
    ) -> "Board":
        """Build a board from an 8x8 nested sequence of piece values."""
        if len(matrix) != BOARD_SIZE:
            raise ValueError("matrix must have 8 rows")
        cells: List[int] = []
        for row in matrix:
            if len(row) != BOARD_SIZE:
                raise ValueError("every matrix row must have 8 columns")
            cells.extend(int(value) for value in row)
        return cls(cells, turn, rules)

    @classmethod
    def from_rows(
        cls, rows: Sequence[str], turn: int = RED, rules: str = "english"
    ) -> "Board":
        """Build a board from 8 strings such as ``" b b b b"`` / ``".r.r.r.r"``.

        Accepted glyphs: ``.`` or space (empty), ``r`` red man, ``R`` red king,
        ``b`` black man, ``B`` black king.
        """
        if len(rows) != BOARD_SIZE:
            raise ValueError("expected 8 rows")
        cells: List[int] = []
        for row in rows:
            if len(row) != BOARD_SIZE:
                raise ValueError("every row must be 8 characters long")
            for char in row:
                if char not in _LETTERS:
                    raise ValueError(f"unknown cell glyph: {char!r}")
                cells.append(_LETTERS[char])
        return cls(cells, turn, rules)

    def copy(self) -> "Board":
        return Board(self.cells, self.turn, self.rules)

    # ------------------------------------------------------------------
    # piece access
    # ------------------------------------------------------------------
    def piece_at(self, square: int) -> int:
        return self.cells[square]

    def set_piece(self, square: int, piece: int) -> None:
        self.cells[square] = piece

    def squares_of(self, player: Optional[int] = None) -> List[int]:
        """Squares occupied by ``player`` (default: the side to move)."""
        player = self.turn if player is None else player
        cells = self.cells
        return [s for s in PLAYABLE_SQUARES if cells[s] and (cells[s] > 0) == (player > 0)]

    def piece_count(self, player: Optional[int] = None) -> int:
        cells = self.cells
        if player is None:
            return sum(1 for s in PLAYABLE_SQUARES if cells[s])
        return sum(1 for s in PLAYABLE_SQUARES if cells[s] and (cells[s] > 0) == (player > 0))

    def king_count(self, player: Optional[int] = None) -> int:
        cells = self.cells
        if player is None:
            return sum(1 for s in PLAYABLE_SQUARES if abs(cells[s]) == KING)
        return sum(1 for s in PLAYABLE_SQUARES if cells[s] * player > 0 and abs(cells[s]) == KING)

    def man_count(self, player: Optional[int] = None) -> int:
        return self.piece_count(player) - self.king_count(player)

    def to_matrix(self) -> List[List[int]]:
        return [self.cells[r * BOARD_SIZE:(r + 1) * BOARD_SIZE] for r in range(BOARD_SIZE)]

    def position_key(self):
        """Hashable identity of the position (used for repetition detection)."""
        return (tuple(self.cells), self.turn)

    # ------------------------------------------------------------------
    # move generation
    # ------------------------------------------------------------------
    def generate_moves(self, player: Optional[int] = None) -> List[Move]:
        """All legal moves for ``player`` (default: the side to move).

        Capturing is mandatory: as soon as a capture exists only the capture
        sequences are returned, exactly as the rules require.
        """
        captures = self.capture_moves(player)
        if captures:
            return captures
        return self.quiet_moves(player)

    def capture_moves(self, player: Optional[int] = None) -> List[Move]:
        """Every complete capture sequence, or ``[]`` when no jump is available."""
        player = self.turn if player is None else player
        is_red = player > 0
        cells = self.cells
        results: List[Move] = []
        for square in PLAYABLE_SQUARES:
            piece = cells[square]
            if piece == 0 or (piece > 0) != is_red:
                continue
            self._collect_captures(square, piece, [], square, results)
        if results and self.maximum_capture:
            # Brazilian rules: the longest capture is compulsory
            longest = max(move.captured_count for move in results)
            results = [move for move in results if move.captured_count == longest]
        return results

    def quiet_moves(self, player: Optional[int] = None) -> List[Move]:
        """Non-capturing moves for ``player`` (ignoring the mandatory jump rule)."""
        player = self.turn if player is None else player
        is_red = player > 0
        cells = self.cells
        flying = self.flying_kings
        moves: List[Move] = []
        for square in PLAYABLE_SQUARES:
            piece = cells[square]
            if piece == 0 or (piece > 0) != is_red:
                continue
            if piece == RED_MAN:
                steps, crown_row, man = RED_STEPS[square], 0, True
            elif piece == BLACK_MAN:
                steps, crown_row, man = BLACK_STEPS[square], BOARD_SIZE - 1, True
            elif flying:
                # a Russian king slides along a diagonal until something blocks it
                for ray in RAYS[square]:
                    for target in ray:
                        if cells[target] != 0:
                            break
                        moves.append(Move(square, target, (), False))
                continue
            else:
                steps, crown_row, man = KING_STEPS[square], -1, False
            for target in steps:
                if cells[target] != 0:
                    continue
                promote = man and row_of(target) == crown_row
                moves.append(Move(square, target, (), promote))
        return moves

    def _collect_captures(
        self,
        square: int,
        piece: int,
        captured: List[int],
        origin: int,
        results: List[Move],
        path: Optional[List[int]] = None,
        promoted: bool = False,
    ) -> None:
        """Depth-first enumeration of all maximal jump sequences for one piece.

        ``promoted`` records that the piece was already crowned earlier in this
        move (joker rules), so the final :class:`Move` still finishes as a king.
        """
        cells = self.cells
        path = [] if path is None else path
        if piece == RED_MAN:
            jumps, crown_row, king, enemy_is_red = RED_JUMPS[square], 0, False, False
        elif piece == BLACK_MAN:
            jumps, crown_row, king, enemy_is_red = BLACK_JUMPS[square], BOARD_SIZE - 1, False, True
        elif piece == RED_KING:
            jumps, crown_row, king, enemy_is_red = KING_JUMPS[square], -1, True, False
        else:
            jumps, crown_row, king, enemy_is_red = KING_JUMPS[square], -1, True, True

        if king and self.flying_kings:
            self._collect_king_captures(square, captured, origin, results, path, promoted)
            return

        if not king and self.men_capture_backwards:
            # Brazilian rules: a man may jump in every direction, but it only
            # *moves* forwards and is crowned as soon as it lands on the far row
            jumps = KING_JUMPS[square]

        extended = False
        for over, land in jumps:
            victim = cells[over]
            if victim == 0 or (victim > 0) != enemy_is_red:
                continue
            if over in captured:
                # already jumped over in this sequence, it is gone from the board
                continue
            if cells[land] != 0 and land != origin:
                continue
            extended = True
            captured.append(over)
            path.append(land)
            if not king and row_of(land) == crown_row:
                if self.promotion_continues:
                    # crowned in the middle of the jump: the rest of the chain is
                    # played by the new king (jokergame.cc), and the move ends as
                    # a king, so remembered by ``promoted=True``
                    self._collect_captures(
                        land, promote_piece(piece), captured, origin, results, path, True
                    )
                else:
                    results.append(Move(origin, land, captured, True, path))
            else:
                self._collect_captures(land, piece, captured, origin, results, path, promoted)
            path.pop()
            captured.pop()

        if not extended and captured:
            results.append(Move(origin, square, captured, promoted, path))

    def _collect_king_captures(
        self,
        square: int,
        captured: List[int],
        origin: int,
        results: List[Move],
        path: List[int],
        promoted: bool = False,
    ) -> None:
        """Flying-king captures: take from any distance and land anywhere behind.

        Captured pieces stay on the board until the move ends, so the king may
        neither jump the same piece twice nor pass over a piece it already took.
        ``promoted`` marks a king that was crowned earlier in this same move.
        """
        cells = self.cells
        enemy_is_red = cells[origin] < 0
        extended = False
        for ray in RAYS[square]:
            for position, victim_square in enumerate(ray):
                # our own starting square is empty during the move, exactly like
                # in the man generator - a king may pass over it and land on it
                victim = 0 if victim_square == origin else cells[victim_square]
                if victim == 0:
                    continue
                if (victim > 0) != enemy_is_red or victim_square in captured:
                    break                      # our own piece or an already taken one
                for land in ray[position + 1:]:
                    if cells[land] != 0 and land != origin:
                        break                  # a captured piece still blocks the ray
                    extended = True
                    captured.append(victim_square)
                    path.append(land)
                    self._collect_king_captures(
                        land, captured, origin, results, path, promoted
                    )
                    path.pop()
                    captured.pop()
                break

        if not extended and captured:
            results.append(Move(origin, square, captured, promoted, path))

    # ------------------------------------------------------------------
    # applying moves
    # ------------------------------------------------------------------
    def make_move(self, move: Move):
        """Play ``move`` in place and return an undo token for :meth:`unmake_move`."""
        cells = self.cells
        piece_before = cells[move.from_square]
        if piece_before == EMPTY:
            raise ValueError(f"no piece on {square_name(move.from_square)}")
        captured = []
        for square in move.captured:
            captured.append((square, cells[square]))
            cells[square] = EMPTY
        cells[move.from_square] = EMPTY
        cells[move.to_square] = promote_piece(piece_before) if move.promotes else piece_before
        previous_turn = self.turn
        self.turn = -previous_turn
        return (piece_before, captured, previous_turn)

    def unmake_move(self, move: Move, undo) -> None:
        """Exactly revert a :meth:`make_move` call (used by the search)."""
        piece_before, captured, previous_turn = undo
        cells = self.cells
        cells[move.to_square] = EMPTY
        cells[move.from_square] = piece_before
        for square, piece in captured:
            cells[square] = piece
        self.turn = previous_turn

    def apply_move(self, move: Move) -> None:
        """Play ``move`` and throw the undo information away."""
        self.make_move(move)

    def resulting_board(self, move: Move) -> "Board":
        """A new board with ``move`` applied (leaves ``self`` untouched)."""
        board = self.copy()
        board.make_move(move)
        return board

    def parse_move(self, text: str, player: Optional[int] = None) -> Move:
        """Match free-form input such as ``c3-d4``, ``c3xd5`` or ``c3 d4``.

        Only the first and the last square of the input are used, so a full
        jump path like ``c3xe5xg7`` resolves to the matching capture sequence.
        Raises :class:`ValueError` when the move is not legal.
        """
        tokens = re.findall(r"[a-hA-H][1-8]", text or "")
        if not tokens:
            raise ValueError(f"cannot parse move: {text!r}")
        origin = parse_square(tokens[0])
        destination = parse_square(tokens[-1])
        for move in self.generate_moves(player):
            if move.from_square == origin and move.to_square == destination:
                return move
        raise ValueError(f"illegal move: {text.strip()!r}")

    # ------------------------------------------------------------------
    # game state
    # ------------------------------------------------------------------
    def has_moves(self, player: Optional[int] = None) -> bool:
        return bool(self.generate_moves(player))

    def is_terminal(self) -> bool:
        """True when the side to move has no legal move (it loses)."""
        return not self.has_moves()

    def winner(self) -> Optional[int]:
        """``RED``/``BLACK`` when the game is decided, else ``None``."""
        if self.has_moves():
            return None
        return -self.turn

    def perft(self, depth: int) -> int:
        """Count the nodes of the game tree (a self test for move generation)."""
        if depth <= 0:
            return 1
        moves = self.generate_moves()
        if not moves:
            return 0
        if depth == 1:
            return len(moves)
        total = 0
        for move in moves:
            undo = self.make_move(move)
            total += self.perft(depth - 1)
            self.unmake_move(move, undo)
        return total

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def __str__(self) -> str:
        border = "  +" + "---+" * BOARD_SIZE
        lines = ["    " + "   ".join(FILES), border]
        for row in range(BOARD_SIZE):
            line = f"{BOARD_SIZE - row} |"
            for col in range(BOARD_SIZE):
                square = _index(row, col)
                glyph = _GLYPHS[self.cells[square]] if is_playable_square(square) else " "
                line += f" {glyph} |"
            lines.append(line + f" {BOARD_SIZE - row}")
            lines.append(border)
        lines.append("    " + "   ".join(FILES))
        side = "RED" if self.turn == RED else "BLACK"
        lines.append(f"  r/R = red man/king, b/B = black man/king, {side} to move")
        return "\n".join(lines)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Board):
            return NotImplemented
        return self.turn == other.turn and self.cells == other.cells

    __hash__ = None  # mutable


def _initial_cells() -> List[int]:
    """Standard opening: 12 black men on rows 0-2, 12 red men on rows 5-7."""
    cells = [EMPTY] * SQUARE_COUNT
    for square in PLAYABLE_SQUARES:
        row = row_of(square)
        if row < 3:
            cells[square] = BLACK_MAN
        elif row > 4:
            cells[square] = RED_MAN
    return cells
