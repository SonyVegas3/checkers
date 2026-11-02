"""Reproducible engine benchmark on a few frozen positions.

Two modes:

* ``--seconds`` (default 2.0): the clock decides; records the depth the engine
  reached, the nodes, the time and the memory.  This is the strength proxy.
* ``--depth`` (e.g. 8) with ``--seconds 0``: a fixed depth, so the same work is
  compared between engines (search efficiency).

``--engine`` is any module with a ``CheckersAI`` class, e.g. the frozen baseline::

    python scripts/engine_bench.py --engine checkersbot.ai_baseline --seconds 2
    python scripts/engine_bench.py --engine checkersbot.ai --depth 8 --seconds 0

Results are printed and written to ``logs/bench_<engine>_<mode>.json``.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from checkersbot.board import RED, Board  # noqa: E402

#: Frozen on 2026-10-27: "midgame" and "endgame_4" were derived by playing the
#: baseline at depth 4 from the start (24 and 70 plies); the tactical ones are the
#: fixtures of ``tests/test_engine.py``.  Never regenerate them - comparisons need
#: the very same positions.
ROWS: Dict[str, Optional[List[str]]] = {
    "opening": None,
    "midgame": [
        "...b...b",
        "b.......",
        "...b...b",
        "..b...b.",
        ".....r..",
        "..r.....",
        ".......r",
        "r.r.r.r.",
    ],
    "endgame_4": [
        ".R......",
        "........",
        "........",
        "........",
        "........",
        "r.......",
        "........",
        "r.....B.",
    ],
    "tactical_chain": [          # man jumps, is crowned and keeps capturing
        "........",
        "..b.b...",
        ".....r..",
        "........",
        "........",
        "........",
        "........",
        "........",
    ],
    "tactical_multi": [          # forced double jump b2xf6
        "........",
        "........",
        "........",
        "....b...",
        "........",
        "..b.....",
        ".r......",
        "........",
    ],
    "king_double": [             # a red king takes two men in one move
        "........",
        "......b.",
        "........",
        "....b...",
        "........",
        "..R.....",
        "........",
        "........",
    ],
}


def build(name: str) -> Board:
    rows = ROWS[name]
    if rows is None:
        return Board(turn=RED, rules="joker")
    return Board.from_rows(rows, turn=RED, rules="joker")


def _rss_mb() -> Optional[float]:
    try:                                    # psutil is optional
        import psutil

        return round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
    except Exception:
        return None


def run_one(engine_cls, board: Board, seconds: Optional[float], depth: int) -> Dict[str, object]:
    ai = engine_cls(max_depth=depth, time_limit=seconds)
    tracemalloc.start()
    before = _rss_mb()
    started = time.perf_counter()
    move = ai.choose_move(board)
    elapsed = time.perf_counter() - started
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    stats = ai.stats
    return {
        "move": move.notation() if move is not None else None,
        "depth": stats.depth,
        "nodes": stats.nodes,
        "seconds": round(elapsed, 3),
        "knps": round((stats.nodes / elapsed) / 1000.0, 1) if elapsed else None,
        "score": stats.score,
        "tt_entries": len(getattr(ai, "_table", {}) or {}),
        "peak_python_mb": round(peak / (1024 * 1024), 1),
        "rss_before_mb": before,
        "rss_after_mb": _rss_mb(),
        "note": stats.note,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", default="checkersbot.ai")
    parser.add_argument("--seconds", type=float, default=2.0,
                        help="time per position (0 = no clock, fixed depth)")
    parser.add_argument("--depth", type=int, default=60, help="depth cap")
    parser.add_argument("--out", default=None, help="JSON output path")
    parser.add_argument("--repeat", type=int, default=1, help="run each position N times")
    args = parser.parse_args(argv)

    engine_cls = importlib.import_module(args.engine).CheckersAI
    seconds = args.seconds or None
    mode = ("time %.1fs" % seconds) if seconds else ("depth %d" % args.depth)
    print("%s | %s" % (args.engine, mode))

    results: Dict[str, List[Dict[str, object]]] = {}
    for name in ROWS:
        rows: List[Dict[str, object]] = []
        for _ in range(max(1, args.repeat)):
            rows.append(run_one(engine_cls, build(name), seconds, args.depth))
        results[name] = rows
        best = rows[0]
        print("%-15s move=%-12s depth=%-3s nodes=%-8s %6.2fs %7s knps peak=%sMB (%s)"
              % (name, best["move"], best["depth"], best["nodes"], best["seconds"],
                 best["knps"], best["peak_python_mb"], best["note"] or "-"))

    payload = {
        "engine": args.engine,
        "mode": mode,
        "depth_cap": args.depth,
        "seconds": seconds,
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "results": results,
    }
    out = Path(args.out) if args.out else Path("logs") / (
        "bench_%s_%s.json" % (args.engine.replace(".", "_"),
                              "depth%d" % args.depth if not seconds else "t%gs" % seconds)
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    print("written %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
