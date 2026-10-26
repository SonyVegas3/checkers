"""Head-to-head match between two engine modules (strength check).

Both engines play the same amount of time per move, the colours are swapped
after every game and every game starts from a slightly different opening, so the
result is a fair comparison of *search and evaluation strength* rather than of
the machine's speed::

    python scripts/engine_match.py --games 20 --seconds 0.3
    python scripts/engine_match.py --games 20 --a checkersbot.ai --b checkersbot._legacy_ai

``--a`` is the challenger, ``--b`` the baseline.  A module is anything that
exposes a ``CheckersAI`` class with ``choose_move(board)``; the default compares
the current engine with ``checkersbot.ai``'s historical copy if one is present.
"""

from __future__ import annotations

import argparse
import importlib
import random
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from checkersbot.board import RED, Board          # noqa: E402

#: a game is declared drawn after this many plies without a capture
QUIET_PLIES_DRAW = 60
#: hard stop, so a match can never hang
MAX_PLIES = 240


def _engine(module: str, seconds: Optional[float], depth: int):
    return importlib.import_module(module).CheckersAI(max_depth=depth, time_limit=seconds)


def _opening(board: Board, plies: int, rng: random.Random) -> None:
    """Play a few random moves so the games do not all repeat themselves."""
    for _ in range(plies):
        moves = board.generate_moves()
        if not moves:
            return
        board.make_move(rng.choice(moves))


def _play_one(board: Board, engines: dict, seconds: Optional[float], depth: int,
              rng: random.Random, opening: int) -> str:
    """Returns ``"red"``, ``"black"`` or ``"draw"``."""
    work = board.copy()
    _opening(work, opening, rng)
    quiet = 0
    for _ in range(MAX_PLIES):
        moves = work.generate_moves()
        if not moves:
            # the side to move has no move: it loses
            return "black" if work.turn == RED else "red"
        engine = engines[work.turn]
        move = engine.choose_move(work)
        if move is None:
            return "black" if work.turn == RED else "red"
        quiet = 0 if move.captured or move.promotes else quiet + 1
        if quiet >= QUIET_PLIES_DRAW:
            return "draw"
        work.make_move(move)
    return "draw"


def main(argv: Sequence[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=20, help="number of games")
    parser.add_argument("--seconds", type=float, default=0.3, help="time per move")
    parser.add_argument("--depth", type=int, default=60, help="depth cap (time decides)")
    parser.add_argument("--opening", type=int, default=4, help="random opening plies")
    parser.add_argument("--a", default="checkersbot.ai", help="challenger module")
    parser.add_argument("--b", default="checkersbot.ai", help="baseline module")
    parser.add_argument("--set-a", action="append", default=[], metavar="NAME=VALUE",
                        help="patch a module constant for the challenger "
                             "(e.g. --set-a TRADE_WEIGHT=2)")
    parser.add_argument("--set-b", action="append", default=[], metavar="NAME=VALUE",
                        help="patch a module constant for the baseline")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)

    def _patch(module: str, assignments: Sequence[str]) -> None:
        target = importlib.import_module(module)
        for item in assignments:
            name, _, raw = item.partition("=")
            setattr(target, name.strip(), type(getattr(target, name.strip(), 0))(raw))

    _patch(args.a, args.set_a)
    _patch(args.b, args.set_b)

    rng = random.Random(args.seed)
    wins = losses = draws = 0
    started = time.perf_counter()
    for game in range(args.games):
        challenger_red = game % 2 == 0
        red_module, black_module = (
            (args.a, args.b) if challenger_red else (args.b, args.a)
        )
        engines = {
            RED: _engine(red_module, args.seconds, args.depth),
            -RED: _engine(black_module, args.seconds, args.depth),
        }
        result = _play_one(Board(), engines, args.seconds, args.depth, rng, args.opening)
        challenger_won = (result == "red") == challenger_red and result != "draw"
        if result == "draw":
            draws += 1
            mark = "="
        elif challenger_won:
            wins += 1
            mark = "A"
        else:
            losses += 1
            mark = "B"
        print(
            f"game {game + 1:2d}: {mark}  ({'A' if challenger_red else 'B'} was red)  "
            f"score A {wins} - {losses} B, draws {draws}",
            flush=True,
        )
    played = wins + losses + draws
    points = wins + draws / 2
    print(
        f"\n{played} games in {time.perf_counter() - started:.0f}s: "
        f"A (challenger) {wins} W / {losses} L / {draws} D "
        f"-> {points / max(1, played):.1%} of the points"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
