"""Minimax + alpha-beta checkers engine.

The engine is a classic negamax searcher with:

* alpha-beta pruning and a transposition table keyed by ``(cells, side)``;
* iterative deepening with a wall clock budget, so the search always returns
  the best move of the last completed iteration;
* capture quiescence: when the depth budget is exhausted but the side to move
  is forced to jump, the search keeps following the jumps;
* move ordering (transposition move first, then captures by size, promotions
  and piece-square gain) which is what gives alpha-beta most of its power;
* a dynamic depth: fewer pieces on the board -> deeper search (endgames are
  searched further, crowded openings stay shallow and fast);
* a static evaluation built from material, piece-square tables (advancement,
  centre control, edge safety, back row defence) and mobility;
* optional repetition handling: game positions passed as ``history`` are scored
  as draws, which stops the engine from shuffling a won game into a draw.

:func:`evaluate` returns a score from RED's point of view; inside the search
scores are negated as usual (negamax convention: always from the side to move
point of view).
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from .board import (
    BLACK,
    BLACK_MAN,
    BLACK_STEPS,
    KING_STEPS,
    PLAYABLE_SQUARES,
    RAYS,
    RED,
    RED_MAN,
    RED_STEPS,
    SQUARE_COUNT,
    Board,
    Move,
    col_of,
    row_of,
)

INF = 1_000_000
MATE = 100_000
MATE_THRESHOLD = MATE - 1_000

MAN_VALUE = 100
KING_VALUE = 180
MOBILITY_WEIGHT = 1

#: Back-row defence (a man on our own back row is worth extra while the opponent
#: still has men).  Measured with ``scripts/engine_match.py``: 45.8 % against the
#: same engine without the term over 12 games, i.e. it does **not** help - and it
#: made the bot keep its men at home instead of playing, which looked passive.
#: The term stays as a knob (``--set-a BACK_ROW_WEIGHT=16`` in the match runner),
#: switched off by default.
BACK_ROW_WEIGHT = 0

#: With a material advantage, every exchange brings the win closer; when behind,
#: keeping the pieces on the board is the defender's best hope.  The term adds
#: ``TRADE_WEIGHT`` per missing piece, signed by who is ahead.  Measured with
#: ``scripts/engine_match.py`` before it is switched on.
TRADE_WEIGHT = 0

# Bonus for a red man ``n`` rows away from its home row (row 7).  Deliberately
# small: material has to dominate, otherwise the engine starts chasing space.
ADVANCEMENT = (0, 1, 2, 4, 6, 9, 12, 16)

# transposition table flags
EXACT, LOWER, UPPER = 0, 1, 2


def _mirror(square: int) -> int:
    """180 degree rotation - maps red's home squares onto black's."""
    return (7 - row_of(square)) * 8 + (7 - col_of(square))


def _build_piece_square_tables():
    red_man = [0] * SQUARE_COUNT
    red_king = [0] * SQUARE_COUNT
    for square in PLAYABLE_SQUARES:
        row, col = row_of(square), col_of(square)
        value = ADVANCEMENT[7 - row]          # advancement towards promotion
        if 2 <= col <= 5 and 2 <= row <= 5:
            value += 4                        # centre control
        if 3 <= row <= 4 and 2 <= col <= 5:
            value += 2                        # the strongest central squares
        if col == 0 or col == 7:
            value += 6                        # edge pieces cannot be captured
        if row == 7:
            value += 6                        # guards the promotion row
        red_man[square] = value

        king = 0
        if 1 <= row <= 6 and 1 <= col <= 6:
            king += 6                         # kings want room to manoeuvre
        if col == 0 or col == 7 or row == 0 or row == 7:
            king -= 4                         # less mobile on the rim
        red_king[square] = king

    black_man = [0] * SQUARE_COUNT
    black_king = [0] * SQUARE_COUNT
    for square in PLAYABLE_SQUARES:
        black_man[square] = red_man[_mirror(square)]
        black_king[square] = red_king[_mirror(square)]
    return red_man, black_man, red_king, black_king


RED_MAN_PST, BLACK_MAN_PST, RED_KING_PST, BLACK_KING_PST = _build_piece_square_tables()


def evaluate_components(board: Board) -> Dict[str, int]:
    """The static evaluation split into the terms it is built from.

    ``sum(components.values()) == evaluate(board)`` by construction, and a test
    checks that on many positions.  The test board shows these next to the searched
    score so the two are never confused: a search result is not a static score.
    """
    cells = board.cells
    material = 0
    tables = 0
    red_mobility = 0
    black_mobility = 0
    red_back = black_back = 0
    red_men = black_men = 0
    pieces_total = 0
    flying = board.flying_kings
    for square in PLAYABLE_SQUARES:
        piece = cells[square]
        if piece == 0:
            continue
        pieces_total += 1
        if piece > 0:
            if piece == RED_MAN:
                material += MAN_VALUE
                tables += RED_MAN_PST[square]
                red_men += 1
                if square >= 56:
                    red_back += 1
                steps = RED_STEPS[square]
            else:
                material += KING_VALUE
                tables += RED_KING_PST[square]
                steps = _king_targets(cells, square, flying)
            for target in steps:
                if cells[target] == 0:
                    red_mobility += 1
        else:
            if piece == BLACK_MAN:
                material -= MAN_VALUE
                tables -= BLACK_MAN_PST[square]
                black_men += 1
                if square < 8:
                    black_back += 1
                steps = BLACK_STEPS[square]
            else:
                material -= KING_VALUE
                tables -= BLACK_KING_PST[square]
                steps = _king_targets(cells, square, flying)
            for target in steps:
                if cells[target] == 0:
                    black_mobility += 1

    back_row = (min(red_back, 2) * black_men - min(black_back, 2) * red_men) \
        * BACK_ROW_WEIGHT // 12
    trade = 0
    if TRADE_WEIGHT:
        empty = len(PLAYABLE_SQUARES) - pieces_total
        balance = material + tables + (red_mobility - black_mobility) * MOBILITY_WEIGHT
        if balance > 0:
            trade += empty * TRADE_WEIGHT // 2
        elif balance < 0:
            trade -= empty * TRADE_WEIGHT // 2
    return {
        "material": material,
        "piece_square": tables,
        "mobility": (red_mobility - black_mobility) * MOBILITY_WEIGHT,
        "back_row": back_row,
        "trade": trade,
    }


def evaluate(board: Board) -> int:
    """Static evaluation of ``board`` from RED's point of view (centipawns)."""
    cells = board.cells
    score = 0
    red_mobility = 0
    black_mobility = 0
    red_men = black_men = 0
    red_back = black_back = 0
    pieces_total = 0
    flying = board.flying_kings

    for square in PLAYABLE_SQUARES:
        piece = cells[square]
        if piece == 0:
            continue
        pieces_total += 1
        if piece > 0:
            if piece == RED_MAN:
                score += MAN_VALUE + RED_MAN_PST[square]
                red_men += 1
                if square >= 56:                   # red's own back row
                    red_back += 1
                steps = RED_STEPS[square]
            else:
                score += KING_VALUE + RED_KING_PST[square]
                steps = _king_targets(cells, square, flying)
            for target in steps:
                if cells[target] == 0:
                    red_mobility += 1
        else:
            if piece == BLACK_MAN:
                score -= MAN_VALUE + BLACK_MAN_PST[square]
                black_men += 1
                if square < 8:                     # black's own back row
                    black_back += 1
                steps = BLACK_STEPS[square]
            else:
                score -= KING_VALUE + BLACK_KING_PST[square]
                steps = _king_targets(cells, square, flying)
            for target in steps:
                if cells[target] == 0:
                    black_mobility += 1

    score += (red_mobility - black_mobility) * MOBILITY_WEIGHT
    # Back-row defence, scaled by the opponent's men (nothing to defend against
    # once they are all gone).  Only the first two men are counted: that is the
    # classic "bridge" - paying for every man left at home makes the engine
    # passive, which is exactly what it looked like before this cap.
    score += (min(red_back, 2) * black_men - min(black_back, 2) * red_men) \
        * BACK_ROW_WEIGHT // 12
    if TRADE_WEIGHT:
        # ahead: fewer pieces is better (fewer chances to go wrong); behind: the
        # other way round.  32 squares are playable, so this counts the empty ones.
        empty = len(PLAYABLE_SQUARES) - pieces_total
        if score > 0:
            score += empty * TRADE_WEIGHT // 2
        elif score < 0:
            score -= empty * TRADE_WEIGHT // 2
    return score


def _king_targets(cells, square: int, flying: bool):
    """Squares a king can step to: the four neighbours, or whole diagonals when flying."""
    if not flying:
        return KING_STEPS[square]
    targets = []
    for ray in RAYS[square]:
        for target in ray:
            if cells[target] != 0:
                break
            targets.append(target)
    return targets


class SearchTimeout(Exception):
    """Internal signal: the wall clock budget of the search is exhausted."""


class SearchStats:
    """Diagnostics of the last :meth:`CheckersAI.choose_move` call.

    ``gap`` is how far the second-best root move is behind the best one: a big
    gap means the move was obvious (a forced capture, the only sensible
    continuation), a small one means the position needed real thinking.  The bot
    uses it to "think" for a plausible time instead of always waiting the same.
    """

    __slots__ = ("depth", "nodes", "score", "elapsed", "move", "note", "gap")

    def __init__(
        self,
        depth: int = 0,
        nodes: int = 0,
        score: Optional[int] = None,
        elapsed: float = 0.0,
        move: Optional[Move] = None,
        note: str = "",
        gap: int = 0,
    ) -> None:
        self.depth = depth
        self.nodes = nodes
        #: ``None`` means "not evaluated" - never a made-up zero
        self.score = score
        self.elapsed = elapsed
        self.move = move
        self.note = note
        self.gap = gap

    def __str__(self) -> str:
        move = self.move.notation() if self.move is not None else "-"
        score = "not evaluated" if self.score is None else "%+d" % self.score
        text = (
            f"move {move} | depth {self.depth} | score {score} | "
            f"nodes {self.nodes} | {self.elapsed * 1000:.0f} ms"
        )
        if self.note:
            text += f" | {self.note}"
        return text


class CheckersAI:
    """Iterative deepening minimax engine with alpha-beta pruning."""

    def __init__(
        self,
        color: int = BLACK,
        max_depth: int = 7,
        time_limit: Optional[float] = 3.0,
        quiescence_depth: int = 12,
        max_extra_depth: int = 6,
        tt_size: int = 400_000,
        soft_limit: Optional[float] = None,
        use_killers: bool = False,
        use_pvs: bool = False,
        use_aspiration: bool = False,
        aspiration_window: int = 60,
    ) -> None:
        if color not in (RED, BLACK):
            raise ValueError("color must be RED or BLACK")
        self.color = color            # informational, the board decides who moves
        self.max_depth = max_depth
        self.time_limit = time_limit  # seconds per move, None = no limit
        #: Stop starting new iterations after this many seconds (the hard limit,
        #: ``time_limit``, still aborts a running one).  ``None`` = no soft limit.
        self.soft_limit = soft_limit
        self.quiescence_depth = quiescence_depth
        self.max_extra_depth = max_extra_depth
        self.tt_size = tt_size
        # -- search refinements (each one measurable on its own) --------------
        #: killer moves + history heuristic for *quiet* move ordering
        self.use_killers = use_killers
        #: principal variation search (null-window probes after the first move)
        self.use_pvs = use_pvs
        #: aspiration windows around the previous iteration's score
        self.use_aspiration = use_aspiration
        self.aspiration_window = int(aspiration_window)
        #: Optional ``callable() -> bool``: when it returns True the search stops
        #: like a clock expiry and returns the last fully completed iteration (the
        #: test board's "stop" button uses this; the game itself never sets it).
        self.should_stop: Optional[object] = None
        self._killers: Dict[int, List[Move]] = {}
        self._history_scores: Dict[Tuple[int, int, int], int] = {}
        self.stats = SearchStats()
        self._table: Dict[Tuple[Tuple[int, ...], int], Tuple[int, int, int, Optional[Move]]] = {}
        self._history: Optional[set] = None
        self._nodes = 0
        self._deadline = float("inf")
        self._started = 0.0

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def dynamic_depth(self, board: Board) -> int:
        """Depth for this position - deeper once the board gets empty."""
        pieces = board.piece_count()
        if pieces <= 6:
            extra = self.max_extra_depth
        elif pieces <= 10:
            extra = self.max_extra_depth - 1
        elif pieces <= 14:
            extra = self.max_extra_depth - 3
        elif pieces <= 18:
            extra = self.max_extra_depth - 4
        else:
            extra = 0
        return max(1, self.max_depth + max(0, extra))

    def choose_move(self, board: Board, history=None, allowed=None,
                    quick_single: bool = True) -> Optional[Move]:
        """Best move for ``board.turn``, or ``None`` if the side has no moves.

        The board passed in is never modified (the search works on a copy),
        so it is safe to call this straight from a web handler.

        ``history`` is an optional iterable of earlier :meth:`Board.position_key`
        values.  Positions from that history are scored as a draw inside the
        search, so a winning engine keeps making progress instead of shuffling a
        won game into a 40-move draw (and a losing one may still repeat to draw).

        ``allowed`` optionally restricts the root moves (e.g. when the site the
        bot is playing on offers only some of them).

        With exactly one legal move, ``quick_single`` (the default) skips the
        search and reports ``score=None`` / ``depth=0`` - "not evaluated", never a
        made-up zero.  Pass ``quick_single=False`` when a real evaluation of that
        single move is needed (the test board's comparison mode does).
        """
        start = time.perf_counter()
        self._nodes = 0
        self._started = start
        self._table.clear()
        self._killers.clear()
        self._history_scores.clear()
        self._history = set(history) if history else None
        self._deadline = start + self.time_limit if self.time_limit else float("inf")

        work = board.copy()
        moves = work.generate_moves()
        if allowed is not None:
            restricted = [move for move in moves if move in allowed]
            if restricted:
                moves = restricted
        if not moves:
            self.stats = SearchStats(0, 0, None, time.perf_counter() - start, None,
                                     "no legal moves")
            return None
        if len(moves) == 1 and quick_single:
            # nothing to choose - and nothing is claimed about the position
            self.stats = SearchStats(0, 0, None, time.perf_counter() - start,
                                     moves[0], "only move (not evaluated)")
            return moves[0]

        target_depth = self.dynamic_depth(work)
        best_move, best_score, reached_depth, note, gap = self._deepen(
            work, target_depth, allowed
        )

        if best_move is None:                      # cannot happen, kept for safety
            best_move = self._fallback_move(work, moves)
        else:
            bigger = self._prefer_bigger_capture(work, moves, best_move, allowed)
            if bigger is not best_move:
                note = f"{note} bigger capture".strip()
                best_move = bigger
        self.stats = SearchStats(
            reached_depth, self._nodes, best_score, time.perf_counter() - start,
            best_move, note, gap,
        )
        return best_move

    #: Play the move that takes more pieces when its evaluation is within this many
    #: centipawns of the best one.  JokerGame does **not** force the longest capture
    #: (any completed chain is legal - see ``checkersbot/rules.py``), but "take as
    #: much as you can" is what a player expects, so a 3-piece capture is played
    #: over a 2-piece one unless it is clearly worse.  ``0`` switches the
    #: preference off.
    capture_margin = 60

    def _prefer_bigger_capture(self, board: Board, moves, best_move: Move,
                               allowed=None) -> Move:
        """The bigger capture, unless a smaller one is clearly better."""
        if not self.capture_margin or best_move is None or not best_move.captured:
            return best_move
        bigger = [move for move in moves
                  if move.captured_count > best_move.captured_count]
        if not bigger:
            return best_move
        saved_stats, saved_nodes = self.stats, self._nodes
        try:
            ranked = {str(move): score
                      for score, move in self.rank_moves(board, allowed=allowed, depth=4)}
        except Exception:                          # a search that cannot finish
            return best_move
        finally:
            self.stats, self._nodes = saved_stats, saved_nodes
        if not ranked:
            return best_move
        best_score = ranked.get(str(best_move))
        if best_score is None:
            return best_move
        for move in sorted(bigger, key=lambda item: item.captured_count, reverse=True):
            score = ranked.get(str(move))
            if score is not None and score >= best_score - self.capture_margin:
                return move
        return best_move

    # ------------------------------------------------------------------
    # search internals
    # ------------------------------------------------------------------
    def rank_moves(self, board: Board, allowed=None, depth: int = 4):
        """Score every legal move with a shallow search, **worst first**.

        Used by the bot's "lose on purpose" button: a move taken from the weaker
        half of this list looks like a human slip rather than an obvious throw.
        ``allowed`` restricts the candidates exactly like :meth:`choose_move`.
        """
        work = board.copy()
        moves = work.generate_moves()
        if allowed is not None:
            restricted = [move for move in moves if move in allowed]
            if restricted:
                moves = restricted

        start = time.perf_counter()
        self._nodes = 0
        self._table.clear()
        self._history = None
        self._deadline = start + self.time_limit if self.time_limit else float("inf")
        scored: List[Tuple[int, Move]] = []
        for move in moves:
            undo = work.make_move(move)
            try:
                score = -self._negamax(work, max(0, depth - 1), -INF, INF, 1)
            except SearchTimeout:
                work.unmake_move(move, undo)
                break
            work.unmake_move(move, undo)
            scored.append((score, move))
        scored.sort(key=lambda item: item[0])
        gap = 0
        if len(scored) > 1:
            gap = max(0, scored[-1][0] - scored[-2][0])
        self.stats = SearchStats(
            depth,
            self._nodes,
            scored[-1][0] if scored else None,
            time.perf_counter() - start,
            scored[-1][1] if scored else None,
            "ranked",
            gap,
        )
        return scored

    def _fallback_move(self, board: Board, moves: List[Move]) -> Move:
        """A legal move when not even the first iteration finished.

        The clock can fire before depth 1 completes; instead of an arbitrary
        ``moves[0]`` this returns the move the static evaluation likes best, so a
        timed-out move is still a sensible one.
        """
        cache: Dict[Any, float] = {}
        return max(
            moves, key=lambda move: -self._evaluate(board.resulting_board(move), cache)
        )

    def _deepen(self, board: Board, target_depth: int, allowed=None):
        """Iterative deepening up to ``target_depth``; returns the best result so far."""
        snapshot = board.cells[:]
        turn = board.turn
        best_move: Optional[Move] = None
        best_score = -INF
        reached_depth = 0
        gap = 0
        note = ""
        try:
            for depth in range(1, target_depth + 1):
                if (
                    self.soft_limit is not None
                    and depth > 1
                    and (time.perf_counter() - self._started) >= self.soft_limit
                ):
                    # no new iteration: the last completed one is the answer
                    note = note or "soft time limit"
                    break
                move, score, iteration_gap = self._search_iteration(
                    board, depth,
                    best_move if reached_depth else None,
                    best_score if reached_depth else None,
                    allowed,
                )
                if move is not None:
                    best_move, best_score, reached_depth = move, score, depth
                    gap = iteration_gap
                if best_move is not None and abs(best_score) >= MATE_THRESHOLD:
                    note = "forced result"
                    break
                if time.perf_counter() >= self._deadline:
                    # the clock is spent: the last completed iteration is the answer
                    note = note or "time limit"
                    break
        except SearchTimeout:
            # The interrupted recursion leaves make/unmake unbalanced: restore.
            note = "time limit"
            board.cells[:] = snapshot
            board.turn = turn
        if reached_depth <= 0:
            # the clock stopped the very first iteration: no value exists for this
            # position, so report "not evaluated" instead of the -INF placeholder
            return best_move, None, reached_depth, note or "not evaluated", gap
        return best_move, best_score, reached_depth, note, gap

    def _search_root(self, board: Board, depth: int, preferred: Optional[Move] = None,
                     allowed=None, alpha: int = -INF, beta: int = INF):
        """One full-width iteration of iterative deepening."""
        ordered = self._order_moves(board, board.generate_moves(), preferred, ply=0)
        if allowed is not None:
            restricted = [move for move in ordered if move in allowed]
            if restricted:
                ordered = restricted
        best_move = ordered[0]
        best_score = -INF
        second_score = -INF
        for move in ordered:
            undo = board.make_move(move)
            score = -self._negamax(board, depth - 1, -beta, -alpha, 1)
            board.unmake_move(move, undo)
            if score > best_score:
                second_score = best_score
                best_score = score
                best_move = move
                if score > alpha:
                    alpha = score
            elif score > second_score:
                second_score = score
        # how far the runner-up is behind: a big gap means the move was obvious
        gap = 0 if second_score <= -INF else max(0, best_score - second_score)
        return best_move, best_score, gap

    def _search_iteration(self, board: Board, depth: int,
                          preferred: Optional[Move], previous: Optional[int],
                          allowed=None):
        """One iteration, with an aspiration window when the previous score is known.

        A failed window is widened and the iteration re-searched; the returned bound
        is then guaranteed to be exact for the widening that succeeded.
        """
        if not (self.use_aspiration and previous is not None and depth >= 3):
            return self._search_root(board, depth, preferred, allowed)

        delta = max(1, self.aspiration_window)
        low, high = previous - delta, previous + delta
        for _ in range(6):                      # widening is bounded, then full width
            move, score, gap = self._search_root(
                board, depth, preferred, allowed, alpha=low, beta=high
            )
            if score <= low:
                low -= delta
                delta *= 2
                continue
            if score >= high:
                high += delta
                delta *= 2
                continue
            return move, score, gap
        return self._search_root(board, depth, preferred, allowed)

    def _negamax(self, board: Board, depth: int, alpha: int, beta: int, ply: int) -> int:
        """Alpha-beta negamax with a transposition table and jump quiescence."""
        self._nodes += 1
        if not self._nodes & 1023:
            if time.perf_counter() > self._deadline:
                raise SearchTimeout
            if self.should_stop is not None and self.should_stop():
                raise SearchTimeout

        key = (tuple(board.cells), board.turn)
        if self._history is not None and key in self._history:
            # This position already occurred in the game: score it as a draw so a
            # winning engine keeps making progress instead of shuffling forever.
            return 0
        alpha_start, beta_start = alpha, beta
        hash_move: Optional[Move] = None
        entry = self._table.get(key)
        if entry is not None:
            entry_depth, entry_score, entry_flag, hash_move = entry
            if entry_depth >= depth:
                if entry_flag == EXACT:
                    return entry_score
                if entry_flag == LOWER:
                    if entry_score >= beta:
                        return entry_score
                    if entry_score > alpha:
                        alpha = entry_score
                else:  # UPPER bound
                    if entry_score <= alpha:
                        return entry_score
                    if entry_score < beta:
                        beta = entry_score
                if alpha >= beta:
                    return entry_score

        if depth <= -self.quiescence_depth:
            # The quiescence budget is spent: evaluating is enough, and
            # ``generate_moves`` is the most expensive call in the whole search.
            return evaluate(board) * board.turn

        moves = board.generate_moves()
        if not moves:
            return -MATE + ply                     # no move: the side to move loses

        if depth <= 0 and not moves[0].captured:
            # no jump is forced: the position is quiet, so the static score holds
            return evaluate(board) * board.turn

        best_score = -INF
        best_move: Optional[Move] = None
        first = True
        for move in self._order_moves(board, moves, hash_move, ply=ply):
            undo = board.make_move(move)
            if self.use_pvs and not first:
                # null-window probe: only a move that beats alpha needs the full one
                score = -self._negamax(board, depth - 1, -alpha - 1, -alpha, ply + 1)
                if alpha < score < beta:
                    score = -self._negamax(board, depth - 1, -beta, -alpha, ply + 1)
            else:
                score = -self._negamax(board, depth - 1, -beta, -alpha, ply + 1)
            first = False
            board.unmake_move(move, undo)
            if score > best_score:
                best_score = score
                best_move = move
                if score > alpha:
                    alpha = score
                if alpha >= beta:
                    if not move.captured and self.use_killers:
                        # a quiet move that caused a cutoff is worth trying early
                        # in the same place of the next sibling searches
                        slots = self._killers.setdefault(ply, [])
                        if move not in slots:
                            slots.insert(0, move)
                            del slots[2:]
                        key = (board.turn, move.from_square, move.to_square)
                        self._history_scores[key] = (
                            self._history_scores.get(key, 0) + depth * depth
                        )
                    break                          # beta cutoff

        # Mate scores depend on the ply, keep them out of the table.
        if abs(best_score) < MATE_THRESHOLD and len(self._table) < self.tt_size:
            if best_score <= alpha_start:
                flag = UPPER
            elif best_score >= beta_start:
                flag = LOWER
            else:
                flag = EXACT
            self._table[key] = (depth, best_score, flag, best_move)
        return best_score

    def _order_moves(
        self, board: Board, moves: List[Move], hash_move: Optional[Move] = None,
        ply: Optional[int] = None,
    ) -> List[Move]:
        """Order moves so that the most promising ones are searched first."""
        if len(moves) <= 1:
            return moves
        cells = board.cells
        turn = board.turn
        killers = ()
        history = {}
        if self.use_killers:                # the refinements cost a lookup per move
            killers = self._killers.get(ply, []) if ply is not None else ()
            history = self._history_scores

        def priority(move: Move) -> int:
            if hash_move is not None and move == hash_move:
                return 1 << 30
            value = 0
            if move.captured:
                value += 100_000 + 10_000 * len(move.captured)
            elif self.use_killers:
                # a quiet killer is searched before ordinary quiet moves, and the
                # history heuristic orders the rest by how often they cut
                if move in killers:
                    value += 90_000
                else:
                    value += min(8_000, history.get(
                        (turn, move.from_square, move.to_square), 0))
            if move.promotes:
                value += 5_000
            piece = cells[move.from_square]
            if piece == RED_MAN:
                table = RED_MAN_PST
            elif piece == BLACK_MAN:
                table = BLACK_MAN_PST
            elif piece > 0:
                table = RED_KING_PST
            else:
                table = BLACK_KING_PST
            return value + table[move.to_square] - table[move.from_square]

        return sorted(moves, key=priority, reverse=True)


def best_move(board: Board, **kwargs) -> Optional[Move]:
    """Convenience wrapper: ``best_move(board, max_depth=8)``."""
    return CheckersAI(**kwargs).choose_move(board)
