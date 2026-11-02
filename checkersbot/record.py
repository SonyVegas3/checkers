"""One game's record, written as JSON next to the logs.

What goes in: the position the bot first saw, our side, every move with the
engine's own numbers (score, depth, nodes, seconds, the time budget it was given),
the site's confirmations and mismatches, and the result **as the site reports it**.

What never goes in: names, e-mails, passwords, cookies, tokens, coin balances or
any other account data.  When the site does not say who won, the result stays
``"unknown"`` - the evaluation sign is not evidence (an engine can be winning and
still lose on the clock, or the opponent can leave).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

GLYPH = {0: ".", 1: "r", 2: "R", -1: "b", -2: "B"}
RESULTS = ("win", "loss", "draw", "unknown")


def rows_of(board) -> List[str]:
    """The 8x8 position as row strings (``r/R/b/B`` glyphs, ``.`` empty)."""
    cells = board.cells
    return [
        "".join(GLYPH[cells[row * 8 + col]] for col in range(8))
        for row in range(8)
    ]


def _side_name(player: int) -> str:
    return "red" if player > 0 else "black"


@dataclass
class MoveRecord:
    ply: int
    side: str
    move: str
    from_square: str
    to_square: str
    captures: List[str] = field(default_factory=list)
    promotes: bool = False
    score: Optional[int] = None
    depth: Optional[int] = None
    nodes: Optional[int] = None
    seconds: Optional[float] = None
    note: str = ""
    budget: Optional[Dict[str, Any]] = None
    confirmed: bool = False
    site_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "ply": self.ply,
            "side": self.side,
            "move": self.move,
            "from": self.from_square,
            "to": self.to_square,
            "captures": list(self.captures),
            "promotes": bool(self.promotes),
            "score": self.score,
            "depth": self.depth,
            "nodes": self.nodes,
            "seconds": self.seconds,
            "note": self.note,
            "confirmed": bool(self.confirmed),
            "site_note": self.site_note,
            "budget": dict(self.budget) if self.budget is not None else None,
        }
        return data


@dataclass
class GameRecord:
    """A whole game: position, side, moves, mismatches, result."""

    rules: str = "joker"
    our_site_color: Optional[str] = None
    our_engine_color: Optional[str] = None
    depth_cap: Optional[int] = None
    initial_rows: List[str] = field(default_factory=list)
    initial_note: str = "position when the bot first had the move"
    started_at: str = field(default_factory=lambda: _now())
    finished_at: Optional[str] = None
    moves: List[MoveRecord] = field(default_factory=list)
    mismatches: List[str] = field(default_factory=list)
    result: str = "unknown"
    result_source: str = "unknown"

    # -- filling in -------------------------------------------------------
    def add_move(self, record: MoveRecord) -> None:
        self.moves.append(record)

    def mismatch(self, note: str) -> None:
        if note and note not in self.mismatches:
            self.mismatches.append(note)

    def finish(self, result: Optional[str] = None, source: str = "") -> None:
        self.finished_at = _now()
        if result in ("win", "loss", "draw"):
            self.result = result
            self.result_source = source or "site"
        else:
            self.result = "unknown"
            self.result_source = source or "unknown"

    # -- persistence ------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "format": "checkersbot-game/v1",
            "rules": self.rules,
            "our_site_color": self.our_site_color,
            "our_engine_color": self.our_engine_color,
            "depth_cap": self.depth_cap,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "initial_rows": list(self.initial_rows),
            "initial_note": self.initial_note,
            "moves": [move.to_dict() for move in self.moves],
            "mismatches": list(self.mismatches),
            "result": self.result,
            "result_source": self.result_source,
        }

    def save(self, directory: str = "logs", name: Optional[str] = None) -> Path:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        stamp = re.sub(r"[^0-9]", "", (self.started_at or _now()))[:14]
        path = target / (name or ("game_record_%s.json" % stamp))
        path.write_text(json.dumps(self.to_dict(), indent=1, sort_keys=True),
                        encoding="utf-8")
        return path


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def move_record(ply: int, board, move, stats: Any = None,
                budget: Any = None, confirmed: bool = False,
                site_note: str = "") -> MoveRecord:
    """Build a :class:`MoveRecord` from the engine's own move and statistics."""
    from .board import square_name

    captured = [square_name(square) for square in getattr(move, "captured", ())]
    return MoveRecord(
        ply=ply,
        side=_side_name(board.turn),
        move=move.notation(),
        from_square=square_name(move.from_square),
        to_square=square_name(move.to_square),
        captures=captured,
        promotes=bool(getattr(move, "promotes", False)),
        score=getattr(stats, "score", None),
        depth=getattr(stats, "depth", None),
        nodes=getattr(stats, "nodes", None),
        seconds=(round(float(stats.elapsed), 3)
                 if getattr(stats, "elapsed", None) is not None else None),
        note=str(getattr(stats, "note", "") or ""),
        budget=(budget.to_dict() if hasattr(budget, "to_dict") else None),
        confirmed=confirmed,
        site_note=site_note,
    )
