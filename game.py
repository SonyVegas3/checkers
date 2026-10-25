#!/usr/bin/env python3
"""Terminal interface for the checkers engine.

Interactive play::

    python game.py                  # you are RED (bottom), the engine is BLACK
    python game.py --ai-red         # the engine plays RED, you play BLACK
    python game.py --depth 8 --time 2

Automated smoke test (engine against itself, no input needed)::

    python game.py --selfplay 2 --depth 5 --time 1
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import List, Optional, Tuple

from ai import CheckersAI
from board import BLACK, RED, Board, Move

DRAW_PLIES = 80  # 40 moves per side without a capture or a promotion

HELP_TEXT = """\
Commands:
  <move>   play a move, e.g. c3-d4 or c3xd5 (jump paths such as c3xe5xg7 also work)
  moves    list the legal moves
  board    redraw the board
  help     show this help
  quit     resign / leave the game
"""


def _side(player: int) -> str:
    return "RED" if player == RED else "BLACK"


def _format_moves(moves: List[Move], limit: int = 100) -> str:
    text = ", ".join(m.notation() for m in moves[:limit])
    if len(moves) > limit:
        text += f", ... ({len(moves)} total)"
    return text


def _prompt_move(board: Board, moves: List[Move]) -> Optional[Move]:
    """Read a legal move from stdin, ``None`` when the human gives up."""
    while True:
        try:
            text = input(f"{_side(board.turn)} to move > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not text:
            continue
        command = text.lower()
        if command in ("quit", "exit", "q"):
            return None
        if command in ("help", "h", "?"):
            print(HELP_TEXT)
            continue
        if command in ("moves", "m"):
            print("legal moves:", _format_moves(moves))
            continue
        if command in ("board", "b"):
            print(board)
            continue
        try:
            return board.parse_move(text)
        except ValueError as error:
            print(f"  ! {error}")
            print("  legal moves:", _format_moves(moves))


def play_human_vs_ai(args: argparse.Namespace) -> int:
    board = Board(rules=args.rules)
    ai_side = RED if args.ai_red else BLACK
    human_side = -ai_side
    ai = CheckersAI(color=ai_side, max_depth=args.depth, time_limit=args.time or None)

    print(f"Checkers - 8x8, forced jumps, {args.rules} rules.")
    print(f"You are {_side(human_side)}, the engine is {_side(ai_side)}.")
    print(HELP_TEXT)

    quiet_plies = 0
    history: List[object] = []
    while True:
        print()
        print(board)
        moves = board.generate_moves()
        if not moves:
            print(f"\n{_side(-board.turn)} wins - {_side(board.turn)} has no legal move.")
            return 0
        if quiet_plies >= DRAW_PLIES:
            print("\nDraw - 40 moves without a capture or a promotion.")
            return 0
        history.append(board.position_key())

        if board.turn == human_side:
            move = _prompt_move(board, moves)
            if move is None:
                print(f"You resigned - {_side(ai_side)} wins.")
                return 0
        else:
            print("engine is thinking...")
            move = ai.choose_move(board, history=history)
            if move is None:
                print(f"\n{_side(human_side)} wins!")
                return 0
            print(f"engine plays {move.notation()}   [{ai.stats}]")

        board.apply_move(move)
        quiet_plies = 0 if (move.captured or move.promotes) else quiet_plies + 1


def _auto_game(
    depth: int, time_limit: Optional[float], verbose: bool, rules: str = "brazilian"
) -> Tuple[str, int]:
    """Play one engine-vs-engine game, return ``(result, plies)``."""
    board = Board(rules=rules)
    ai = CheckersAI(max_depth=depth, time_limit=time_limit)
    quiet_plies = 0
    plies = 0
    history: List[object] = []
    while True:
        if not board.generate_moves():
            winner = _side(-board.turn)
            if verbose:
                print(board)
                print(f"{winner} wins after {plies} plies - {_side(board.turn)} is stuck")
            return winner, plies
        if quiet_plies >= DRAW_PLIES:
            if verbose:
                print(f"draw after {plies} plies - 40 quiet moves")
            return "draw", plies
        history.append(board.position_key())

        move = ai.choose_move(board, history=history)
        if move is None:  # cannot happen, generate_moves() was not empty
            return "draw", plies
        if verbose:
            print(f"{plies + 1:3d}. {_side(board.turn):5s} {move.notation():10s} {ai.stats}")
        board.apply_move(move)
        quiet_plies = 0 if (move.captured or move.promotes) else quiet_plies + 1
        plies += 1


def play_selfplay(args: argparse.Namespace) -> int:
    tally = {"RED": 0, "BLACK": 0, "draw": 0}
    started = time.perf_counter()
    for game_no in range(1, args.selfplay + 1):
        result, plies = _auto_game(args.depth, args.time or None, args.verbose, args.rules)
        tally[result] += 1
        print(f"game {game_no}: {result} in {plies} plies")
    elapsed = time.perf_counter() - started
    print()
    print(f"RED {tally['RED']} - {tally['BLACK']} BLACK, {tally['draw']} draw(s), "
          f"{elapsed:.1f}s total")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Play 8x8 checkers against the minimax engine.")
    parser.add_argument("--ai-red", action="store_true", help="the engine plays RED, you play BLACK")
    parser.add_argument("--depth", type=int, default=7, help="base search depth (default: 7)")
    parser.add_argument(
        "--time", type=float, default=3.0, help="seconds per move, 0 = no limit (default: 3)"
    )
    parser.add_argument(
        "--selfplay",
        type=int,
        metavar="GAMES",
        help="run GAMES engine-vs-engine games and exit (smoke test)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="print every self-play move")
    parser.add_argument(
        "--rules",
        default="joker",
        choices=("joker", "brazilian", "russian", "english"),
        help="joker = brazilian rules + a man crowned in the middle of a capture "
             "keeps capturing as a king (jokergame.cc), brazilian = flying kings + "
             "backwards man captures + longest capture, russian = flying kings, "
             "english = American checkers (default: joker)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.depth < 1:
        print("--depth must be at least 1", file=sys.stderr)
        return 2
    if args.selfplay is not None:
        if args.selfplay < 1:
            print("--selfplay must be at least 1", file=sys.stderr)
            return 2
        return play_selfplay(args)
    return play_human_vs_ai(args)


if __name__ == "__main__":
    sys.exit(main())
