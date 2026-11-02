"""The single rule set this project plays: JokerGame (jokergame.cc checkers).

Every component - the board, the search, the bot, the recovery path, the
benchmark, the match runner and the interactive test board - reads its rules from
:data:`JOKERGAME`.  There is no other variant: any other rule name is rejected.

Where each rule comes from
--------------------------
Derived from the public client code of the site and confirmed by replaying live
games (see ``README.md``, "How the site's rule set was identified"):

* 8x8 board, 12 men a side, men move diagonally forward and are crowned on the
  far row;
* kings are flying (they slide and capture over any distance);
* men capture forwards **and** backwards;
* a man crowned *inside* a capture keeps capturing as a king in the same move;
* the side with no legal move loses.

From the **user's observation** of JokerGame (not independently verified here):

* a captured piece leaves the board **immediately**, so the king that took it may
  land on any free square behind it, pass over squares it has just cleared and
  continue capturing in the opposite direction as well;
* capturing is compulsory and a chosen chain must be followed while a legal
  capture still exists on it, but **the longest capture is not compulsory**: from
  all the completed capture paths any one may be played, whatever the number of
  pieces it takes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Union

__all__ = ["Rules", "JOKERGAME", "RULES", "resolve", "describe"]


@dataclass(frozen=True)
class Rules:
    """One immutable rule set."""

    name: str
    board_size: int = 8
    flying_kings: bool = True
    men_capture_backwards: bool = True
    promotion_continues: bool = True
    #: ``"immediate"``: a taken piece leaves the board at once (see the docstring).
    removal: str = "immediate"
    #: Where the removal rule comes from, so no document can claim more than that.
    removal_basis: str = (
        "user's observation of JokerGame; not independently verified"
    )
    #: The longest capture is *not* compulsory (user's observation as well).
    longest_capture_required: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "board_size": self.board_size,
            "flying_kings": self.flying_kings,
            "men_capture_backwards": self.men_capture_backwards,
            "promotion_continues": self.promotion_continues,
            "removal": self.removal,
            "removal_basis": self.removal_basis,
            "longest_capture_required": self.longest_capture_required,
        }


#: The rule set of the project.  Import this instead of writing rule flags.
JOKERGAME = Rules(name="joker")

#: Name -> rule set (kept so a name can be validated in one place).
RULES: Dict[str, Rules] = {JOKERGAME.name: JOKERGAME}


def resolve(value: Union[str, Rules, None]) -> Rules:
    """Accept a :class:`Rules`, the name ``"joker"``/``None``; reject anything else."""
    if value is None:
        return JOKERGAME
    if isinstance(value, Rules):
        if value != JOKERGAME:
            raise ValueError(
                "only the JokerGame rule set is supported, got %r" % (value.name,)
            )
        return JOKERGAME
    if isinstance(value, str):
        if value in RULES:
            return RULES[value]
        raise ValueError(
            "unknown rule set %r - this project only plays JokerGame (%r)"
            % (value, JOKERGAME.name)
        )
    raise ValueError("rules must be a name or a Rules instance, got %r" % (value,))


def describe() -> Dict[str, Any]:
    """The rule set as JSON-able text plus where each part comes from."""
    return {
        "rules": JOKERGAME.to_dict(),
        "basis": {
            "board": "public client code + replayed live games",
            "flying_kings": "public client code + replayed live games",
            "men_capture_backwards": "public client code + replayed live games",
            "promotion_continues": "public client code + replayed live games",
            "removal": JOKERGAME.removal_basis,
            "longest_capture_required": JOKERGAME.removal_basis,
        },
    }
