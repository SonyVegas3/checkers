"""Exact terminal solver: proof of the game-theoretic result, not an estimate.

The search in :mod:`checkersbot.ai` is a *depth limited* evaluator: its score is
the value of the position as far as it could look, and a "mate score" is a mate it
happened to see inside that horizon.  That is what chooses a move, but it is **not**
a proof: one sample continuation does not show that every defence loses.

This module answers the other question, and only that one:

    if both sides play perfectly, who wins from this position, and after how many
    half-moves?

It uses the two predicates a proof needs (``_win_in`` / ``_loss_in`` below), which
walk **every** legal move of the defender, so a "win" means "against all replies"
by construction.  There is no evaluation function anywhere in this module, and a
position is never called lost just because a search happened to score it low.

Checkers has no draw in the rules we play (a draw is declared by the caller after
40 quiet moves), so a very long game or a repetition stays unproven: the solver
gives up at ``max_plies`` and returns ``"unknown"`` with a reason instead of
guessing.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .board import Board

__all__ = ["solve", "best_line", "WIN", "LOSS", "UNKNOWN"]

WIN = "win"
LOSS = "loss"
UNKNOWN = "unknown"

Kind = str
Key = Tuple[Kind, Tuple[int, ...], int, int]


class _Abort(Exception):
    """Internal: the node/clock budget is spent."""


class Solver:
    """Bounded mate solver: ``win in k`` / ``loss in k`` with a memo table."""

    def __init__(self, *, max_plies: int = 40, node_limit: int = 400_000,
                 should_stop: Optional[Any] = None) -> None:
        self.max_plies = max(1, int(max_plies))
        self.node_limit = max(1, int(node_limit))
        self.should_stop = should_stop
        self.nodes = 0
        self.memo: Dict[Key, bool] = {}

    # -- budgets ----------------------------------------------------------
    def _charge(self) -> None:
        self.nodes += 1
        if self.nodes > self.node_limit:
            raise _Abort("node limit")
        if self.should_stop is not None and not self.nodes & 1023:
            if self.should_stop():
                raise _Abort("stopped by the user")

    # -- the two predicates ----------------------------------------------
    def _win_in(self, board: Board, plies: int) -> bool:
        """Can the side to move force a win within ``plies`` half-moves?"""
        self._charge()
        if plies <= 0:
            return False
        key: Key = ("win", tuple(board.cells), board.turn, plies)
        cached = self.memo.get(key)
        if cached is not None:
            return cached
        moves = board.generate_moves()
        if not moves:
            self.memo[key] = False            # no move: this side loses, it wins nothing
            return False
        # ordering only: forcing moves first, the answer never depends on the order
        if len(moves) > 1:
            moves = sorted(moves, key=lambda move: -len(move.captured))
        answer = False
        for move in moves:
            if self._loss_in(board.resulting_board(move), plies - 1):
                answer = True                 # one line that always wins is enough
                break
        self.memo[key] = answer
        return answer

    def _loss_in(self, board: Board, plies: int) -> bool:
        """Does the side to move lose within ``plies`` plies against **every** reply?"""
        self._charge()
        moves = board.generate_moves()
        if not moves:
            return True                       # no move: it has already lost (0 plies)
        if plies <= 0:
            return False
        key: Key = ("loss", tuple(board.cells), board.turn, plies)
        cached = self.memo.get(key)
        if cached is not None:
            return cached
        for move in moves:                    # every defence has to fail
            if not self._win_in(board.resulting_board(move), plies - 1):
                self.memo[key] = False
                return False
        self.memo[key] = True
        return True


def best_line(board: Board, *, max_plies: int = 40,
              node_limit: int = 400_000,
              should_stop: Optional[Any] = None) -> Dict[str, Any]:
    """The proven best-play line, so the proof can be inspected move by move.

    Every move of the line is legal, the line has exactly ``plies`` half-moves and
    it ends where the loser has no legal move.  Returns
    ``{"outcome", "plies", "moves", "complete", "reason"}``; ``moves`` is a list of
    notations (empty when nothing was proven).
    """
    result = solve(board, max_plies=max_plies, node_limit=node_limit,
                   should_stop=should_stop)
    moves: List[str] = []
    if result["complete"] and result["plies"]:
        work = board.copy()
        left = result["plies"]
        wanted = "loss" if result["outcome"] == "win" else "win"
        try:
            while left > 0:
                # the move that keeps the game on exactly this distance: the winner
                # picks the fastest win, the loser the longest resistance
                same = []
                for move in work.generate_moves():
                    child = solve(work.resulting_board(move), max_plies=left,
                                  node_limit=node_limit, should_stop=should_stop)
                    if child["outcome"] == wanted and child["plies"] == left - 1:
                        same.append((move, child))
                if not same:                  # pragma: no cover - proof would be broken
                    break
                move, child = same[0]
                moves.append(move.notation())
                work.make_move(move)
                left -= 1
                wanted = "loss" if child["outcome"] == "win" else "win"
        except _Abort:                        # pragma: no cover - budget
            moves = []
    result["moves"] = moves
    return result


def solve(board: Board, *, max_plies: int = 40, node_limit: int = 400_000,
          should_stop: Optional[Any] = None) -> Dict[str, Any]:
    """Prove the result for the side to move, or admit that it could not.

    Returns ``{"side", "outcome", "plies", "nodes", "complete", "reason"}``:

    * ``outcome`` - ``"win"`` (a forced win against every defence), ``"loss"``
      (every legal reply loses) or ``"unknown"`` (not proven with this budget);
    * ``plies`` - half-moves from **this** position to the end of the game with
      best play on both sides; ``None`` when the outcome is unknown;
    * ``complete`` - ``True`` only when the outcome is proven; otherwise
      ``reason`` says which budget stopped the proof.
    """
    solver = Solver(max_plies=max_plies, node_limit=node_limit,
                    should_stop=should_stop)
    outcome = UNKNOWN
    plies: Optional[int] = None
    reason = ""
    # The smallest k that works is exactly the distance under best play: "win in
    # k" needs one winning line, "loss in k" needs *all* k-plies defences to fail.
    try:
        for k in range(0, solver.max_plies + 1):
            if solver._win_in(board, k):
                outcome, plies = WIN, k
                break
            if solver._loss_in(board, k):
                outcome, plies = LOSS, k
                break
        else:
            reason = "no forced result within %d plies" % solver.max_plies
    except _Abort as error:
        reason = str(error)
    except RecursionError:                     # pragma: no cover - safety net
        reason = "recursion limit"
    complete = outcome != UNKNOWN
    if not complete and not reason:
        reason = "not proven"
    return {
        "side": "red" if board.turn > 0 else "black",
        "outcome": outcome,
        "plies": plies,
        "nodes": solver.nodes,
        "complete": complete,
        "reason": "" if complete else reason,
        "max_plies": solver.max_plies,
    }

