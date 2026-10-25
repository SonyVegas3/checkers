"""The browser side of jokergame.cc, without a browser library in sight.

Everything in this module is plain data and plain JavaScript:

* ``SELECTORS`` - the class names of the site's checkers client;
* ``READ_STATE_JS`` / ``FIT_VIEW_JS`` - the two scripts that read the board and
  keep it inside the window;
* :class:`SiteState` - one snapshot of the board (stones, active squares, turn);
* the coordinate helpers and :func:`site_legal_moves`.

``web_bot.JokerGameSite`` (Playwright, Windows/Linux/macOS) and
``checkersbot_ios.site.WebViewSite`` (WKWebView, iOS) both drive exactly these
scripts, so the bot decides identically on every platform.  The two backends
only differ in *how* a script is evaluated and how a click is dispatched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Protocol, Tuple

from .board import Board, Move


# --- selectors of the jokergame.cc checkers client -------------------------
SELECTORS = {
    "table": ".game-table",
    "square": ".game-square[item-absolute-id]",
    "active_square": ".game-square.active",
    "stone": ".game-stone[stone-absolute]",
    "highlighted_stone": ".game-stone.highlight",
    "finish_dialog": ".game-finish-dialog",
    "waiting": ".waiting-opponent",
    "lobby_play": ".play-button, .play-button-wrap button, button.play-button",
    "lobby_random": "button.play-icon[item-type='random']",
    "lobby_stake": ".v2-coins-dialog .play-coins[item-coins='{coins}']",
    "sign_in_button": ".login-button",
    "auth_dialog": ".v2-auth-dialog",
    "auth_email": '.v2-auth-dialog input[name="phone"]',
    "auth_password": '.v2-auth-dialog input[name="password"]',
}

SITE_COLORS = ("white", "black")


def square_from_xy(x: int, y: int) -> int:
    """Site coordinates (1-based, origin top-left) -> engine square index."""
    return (y - 1) * 8 + (x - 1)


def xy_from_square(square: int) -> Tuple[int, int]:
    """Engine square index -> site coordinates (1-based)."""
    return square % 8 + 1, square // 8 + 1


def move_landing_path(move: Move) -> List[int]:
    """Squares the piece lands on, in order (one landing per captured piece).

    ``Move`` stores the landings it really makes, which matters for the flying
    kings of Russian draughts: a king may land far behind the piece it takes, so
    the landing cannot be derived from the origin and the victim.
    """
    if move.landing_path:
        return list(move.landing_path)
    return [move.to_square]


def first_landing(move: Move) -> int:
    """The first square the site has to offer for this move.

    Chained jumps are clicked one landing at a time, so the site only ever
    advertises the first landing square - this is what the bot has to compare
    with ``.game-square.active``.
    """
    if move.landing_path:
        return move.landing_path[0]
    return move.to_square


def site_legal_moves(board: Board, probe: Dict[int, List[int]]) -> List[Move]:
    """Engine moves whose first landing square the site actually offers.

    ``probe`` is ``{from_square: [first_landing, ...]}`` as returned by
    :meth:`CheckersBot.probe_moves`.  Restricting the engine to this set is what
    keeps the bot from ever clicking a square the server does not offer
    (forced jumps, maximum-capture rules, colour/orientation mistakes, ...).
    """
    return [
        move
        for move in board.generate_moves()
        if first_landing(move) in probe.get(move.from_square, [])
    ]


READ_STATE_JS = r"""
() => {
  // Scope everything to the board that actually holds the pieces: the page can
  // contain more than one .game-table (previews, leftovers from an old game).
  const boards = Array.from(document.querySelectorAll('.game-table'));
  let board = null;
  let best = -1;
  for (const candidate of boards) {
    const count = candidate.querySelectorAll('.game-stone[stone-absolute]').length;
    if (count > best) { best = count; board = candidate; }
  }
  if (!board) board = document;

  const squareSeen = new Set();
  const squares = [];
  board.querySelectorAll('.game-square[item-absolute-id]').forEach((el) => {
    const abs = parseInt(el.getAttribute('item-absolute-id'), 10);
    const x = parseInt(el.getAttribute('item-x'), 10);
    const y = parseInt(el.getAttribute('item-y'), 10);
    if (!isFinite(abs) || !isFinite(x) || !isFinite(y)) return;
    if (squareSeen.has(abs)) return;
    squareSeen.add(abs);
    squares.push({
      absolute: abs, x: x, y: y,
      active: el.classList.contains('active'),
      playable: el.getAttribute('item-color') === 'black',
    });
  });

  const stoneSeen = new Set();
  const domStones = [];
  board.querySelectorAll('.game-stone[stone-absolute]').forEach((el) => {
    const id = el.getAttribute('stone-id');
    if (id !== null) {
      if (stoneSeen.has(id)) return;
      stoneSeen.add(id);
    }
    domStones.push({
      id: id,
      absolute: parseInt(el.getAttribute('stone-absolute'), 10),
      color: el.getAttribute('stone-color'),
      king: el.getAttribute('stone-king') === 'yes',
      clickable: el.getAttribute('stone-clickable') === 'yes',
      highlighted: el.classList.contains('highlight'),
    });
  });

  // jokergame.cc keeps the authoritative state in `init_game`: the server's
  // stone list, whose turn it is and whether a click is accepted right now.
  const game = window.init_game || null;
  const data = (game && game.game_data) || null;
  const info = (data && data.info) || {};
  const server = {
    available: !!game,
    canAction: game ? game.canAction : null,
    status: game ? game.status : null,
    step: info.step === undefined ? null : info.step,
    stepTimeout: info.step_timeout === undefined ? null : info.step_timeout,
    playerAction: data && data.player ? data.player.action : null,
    opponentAction: data && data.opponent ? data.opponent.action : null,
    playerType: data && data.player ? data.player.type : null,
    playerColor: data && data.player && data.player.color ? data.player.color : null,
  };

  let stones = domStones;
  const list = data && data.stones && data.stones.list ? data.stones.list : null;
  if (list && list.length) {
    stones = list
      .filter((s) => s.active !== 'no')
      .map((s) => ({
        id: String(s.id),
        absolute: parseInt(s.absolute, 10),
        color: s.color,
        king: s.king === 'yes' || s.king === true,
        clickable: s.clickable === 'yes',
        highlighted: false,
      }));
    // our own stones are moved forward in game_stones between server updates, but
    // those positions are the client's optimistic view: a captured stone can still
    // sit there for a moment.  The server's list is the same data the referee uses,
    // so the board model is built from it alone and only the *clickable* hint (which
    // the client repaints at once) is taken from the DOM.
    if (domStones.length) {
      const dom = new Map(domStones.map((s) => [s.id, s]));
      stones.forEach((stone) => {
        const hint = dom.get(stone.id);
        if (!hint) return;
        if (hint.clickable) stone.clickable = true;
        if (hint.highlighted) stone.highlighted = true;
      });
    }
  }

  const finishDialog = !!document.querySelector('.game-finish-dialog');

  return {
    squares: squares,
    stones: stones,
    server: server,
    finish_dialog: finishDialog,
    dialog: !!document.querySelector('.big-dialog'),
    waiting: !!document.querySelector('.waiting-opponent'),
    my_turn: server.playerAction !== null
      ? server.playerAction === 'yes'
      : stones.some((s) => s.clickable),
    boards: boards.length,
  };
}
"""


FIT_VIEW_JS = r"""
(margin) => {
  const gap = typeof margin === 'number' ? margin : 10;
  const panels = Array.from(document.querySelectorAll('.user-info'));
  const frame = document.querySelector('.game-table-frame');
  if (!frame) return null;

  // start from the site's own layout
  panels.forEach((el) => { el.style.transform = ''; });
  frame.style.transform = '';

  const vh = window.innerHeight;
  const vw = window.innerWidth;
  const measure = () => {
    const f = frame.getBoundingClientRect();
    const list = panels
      .map((el) => el.getBoundingClientRect())
      .filter((r) => r.width > 0 && r.height > 0);
    const top = Math.min(f.top, ...list.map((r) => r.top));
    const bottom = Math.max(f.bottom, ...list.map((r) => r.bottom));
    return {top: Math.round(top), bottom: Math.round(bottom),
            frame: {top: Math.round(f.top), bottom: Math.round(f.bottom),
                    size: Math.round(f.width)}};
  };

  // 1) pull the info panels inside the window (a transform does not move layout)
  panels.forEach((el) => {
    const r = el.getBoundingClientRect();
    if (r.height <= 0) return;
    if (r.top < gap) el.style.transform = 'translateY(' + Math.round(gap - r.top) + 'px)';
    else if (r.bottom > vh - gap) {
      el.style.transform = 'translateY(' + Math.round(vh - gap - r.bottom) + 'px)';
    }
  });

  // 2) if board + panels still does not fit, shrink the board around its centre
  let info = measure();
  const available = vh - gap * 2;
  const needed = info.bottom - info.top;
  if (needed > available) {
    const scale = Math.max(0.55, Math.min(0.99, available / needed));
    frame.style.transformOrigin = '50% 50%';
    frame.style.transform = 'translate(-50%, -50%) scale(' + scale.toFixed(3) + ')';
    info = measure();
  }
  return {vw: vw, vh: vh, needed: needed, available: available,
          top: info.top, bottom: info.bottom, board: info.frame.size,
          fits: info.top >= -1 && info.bottom <= vh + 1};
}
"""


class SiteState:
    """One snapshot of the board exactly as the website paints it."""

    __slots__ = (
        "squares",
        "stones",
        "finished",
        "waiting",
        "dialog",
        "my_turn",
        "boards",
        "server",
        "can_click",
        "player_action",
        "player_color",
        "step",
        "step_timeout",
        "by_absolute",
        "absolute_by_square",
        "stone_by_square",
    )

    def __init__(self, raw: Dict[str, object]) -> None:
        self.squares: List[Dict[str, object]] = list(raw.get("squares") or [])
        self.stones: List[Dict[str, object]] = list(raw.get("stones") or [])
        self.waiting = bool(raw.get("waiting"))
        self.dialog = bool(raw.get("dialog"))
        self.my_turn = bool(raw.get("my_turn"))
        self.boards = int(raw.get("boards") or 0)

        server: Dict[str, object] = dict(raw.get("server") or {})
        self.server = server
        status = str(server.get("status") or "")
        self.finished = bool(raw.get("finished")) or bool(raw.get("finish_dialog")) or status in (
            "finish",
            "finished",
        )
        # the client only accepts clicks while its own canAction is exactly "yes"
        # ("none" = a request is in flight, "no" = it is not our turn, so a click
        # would just raise a toast and be swallowed)
        self.can_click = str(server.get("canAction") or "yes") == "yes"
        self.player_action = server.get("playerAction")
        self.player_color = server.get("playerColor")
        self.step = server.get("step")
        self.step_timeout = server.get("stepTimeout")

        self.by_absolute: Dict[int, Dict[str, object]] = {}
        self.absolute_by_square: Dict[int, int] = {}
        for square in self.squares:
            absolute = int(square["absolute"])
            self.by_absolute.setdefault(absolute, square)
            self.absolute_by_square.setdefault(
                square_from_xy(int(square["x"]), int(square["y"])), absolute
            )
        self.stone_by_square: Dict[int, Dict[str, object]] = {}
        for stone in self.stones:
            square = self.by_absolute.get(int(stone["absolute"]))
            if square is None:
                continue
            index = square_from_xy(int(square["x"]), int(square["y"]))
            self.stone_by_square[index] = stone

    # -- queries ---------------------------------------------------------
    def is_ready(self) -> bool:
        return len(self.squares) >= 32

    def active_squares(self) -> List[int]:
        """Engine square indexes of the destination squares the site offers."""
        result = []
        for square in self.squares:
            if not square.get("active"):
                continue
            result.append(square_from_xy(int(square["x"]), int(square["y"])))
        return result

    def absolute_for(self, square_index: int) -> Optional[int]:
        return self.absolute_by_square.get(square_index)

    def clickable_colors(self) -> List[str]:
        colors = {stone["color"] for stone in self.stones if stone.get("clickable")}
        return sorted(color for color in colors if color)

    def highlighted_colors(self) -> List[str]:
        colors = {stone["color"] for stone in self.stones if stone.get("highlighted")}
        return sorted(color for color in colors if color)

    def highlighted_stones(self) -> List[int]:
        """Engine squares of the pieces the site says we must move."""
        result = []
        for index, stone in self.stone_by_square.items():
            if stone.get("highlighted"):
                result.append(index)
        return sorted(result)

    def stone_at(self, square_index: int) -> Optional[Dict[str, object]]:
        return self.stone_by_square.get(square_index)

    def describe(self) -> str:
        parts = [f"{len(self.stones)} stones", f"{len(self.squares)} squares"]
        if self.step is not None:
            parts.append(f"step {self.step}")
        if self.step_timeout is not None:
            parts.append(f"{self.step_timeout}s left")
        if self.my_turn:
            parts.append("my turn")
        if not self.can_click:
            parts.append("clicks disabled")
        if self.waiting:
            parts.append("waiting")
        if self.finished:
            parts.append("finished")
        return ", ".join(parts)


class SiteError(Exception):
    """Raised when the page could not be read or a click could not be sent."""


class Site(Protocol):
    """What :class:`checkersbot.bot.CheckersBot` needs from a web page.

    Both backends implement this: ``web_bot.JokerGameSite`` on top of
    Playwright and ``checkersbot_ios.site.WebViewSite`` on top of
    ``toga.WebView``.  ``CheckersBot`` never touches a browser API directly, so
    the same bot plays on Windows and on an iPhone.
    """

    def read_state(self) -> Optional[SiteState]:
        """One snapshot of the board, or ``None`` when the page cannot be read."""

    def wait_for_board(self, timeout_ms: int = 90000) -> bool:
        """Wait until the checkers board is on the page."""

    def clear_highlights(self) -> None:
        """Drop the site's cosmetic "this piece may move" hint."""

    def click_stone(self, stone_id: str, wait_response: bool = True) -> bool:
        """Click one of our stones; True when the site answered."""

    def click_square(self, absolute: int, wait_response: bool = True) -> bool:
        """Click a destination square; True when the site answered."""

    def _wait_can_click(self, timeout_ms: int = 2500) -> bool:
        """Wait until the client accepts clicks (``canAction === "yes"``)."""

    def wait_for_active(self, timeout_ms: int = 8000) -> bool:
        """Wait until the site offers at least one destination square."""

    def fit_view(self, margin: int = 10) -> Optional[Dict[str, object]]:
        """Scale/nudge the page so the whole board is visible."""

    def screenshot(self, path: Path) -> None:
        """Save a screenshot for debugging (a no-op when unsupported)."""

    def dump_html(self, path: Path) -> None:
        """Save the page HTML for debugging (a no-op when unsupported)."""

    def has_auth_dialog(self) -> bool:
        """Is the site's sign-in dialog open?"""

    def needs_sign_in(self) -> bool:
        """Does the page ask us to sign in?"""

    def sign_in(self, email: str, password: str) -> bool:
        """Fill in and submit the sign-in dialog."""

    def enter_game(self, coins: int = 0) -> bool:
        """Walk the lobby and open a table with the given stake."""
