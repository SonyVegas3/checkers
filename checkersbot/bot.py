"""The bot loop - engine plus referee, with no browser library of its own.

:class:`CheckersBot` only talks to the ``site`` object it is given (see
:class:`checkersbot.site.Site`).  On the desktop that is
``web_bot.JokerGameSite`` (Playwright); in the iOS app it is
``checkersbot_ios.site.WebViewSite`` (WKWebView).  Everything in this file -
probing the site for the moves it really offers, picking one with the engine,
clicking it and verifying every step - is shared by both.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .ai import CheckersAI
from .board import (
    BLACK,
    BLACK_KING,
    BLACK_MAN,
    EMPTY,
    RED,
    RED_KING,
    RED_MAN,
    Board,
    Move,
    square_name,
)
from .site import (
    SITE_COLORS,
    Site,
    SiteError,
    SiteState,
    first_landing,
    move_landing_path,
    site_legal_moves,
    xy_from_square,
)


class CheckersBot:
    """Glue between the website (``SiteState``) and the engine (``Board``/``CheckersAI``)."""

    def __init__(
        self,
        site: Site,
        *,
        site_color: str = "auto",
        side: str = "auto",
        depth: int = 7,
        time_limit: Optional[float] = 3.0,
        poll: float = 1.0,
        dry_run: bool = False,
        max_errors: int = 5,
        verbose: bool = True,
        log_dir: str = "logs",
        use_probe: bool = True,
        turn_timeout: float = 120.0,
        coins: int = 0,
        apply_fit: bool = True,
        email: Optional[str] = None,
        password: Optional[str] = None,
        trace_board: bool = False,
        rules: str = "joker",
        rematch_wait: float = 30.0,
        log=None,
        should_stop=None,
        lobby_walk: bool = True,
    ) -> None:
        if site_color not in ("auto",) + SITE_COLORS:
            raise ValueError("site_color must be auto, white or black")
        if side not in ("auto", "red", "black"):
            raise ValueError("side must be auto, red or black")
        if rules not in ("english", "russian", "brazilian", "joker"):
            raise ValueError("rules must be english, russian, brazilian or joker")

        self.site = site
        self.requested_site_color = site_color
        self.requested_side = side
        self.my_site_color: Optional[str] = None if site_color == "auto" else site_color
        self.my_engine_color: Optional[int] = None
        if side == "red":
            self.my_engine_color = RED
        elif side == "black":
            self.my_engine_color = BLACK

        self.ai = CheckersAI(color=BLACK, max_depth=depth, time_limit=time_limit or None)
        self.poll = max(0.1, poll)
        self.dry_run = dry_run
        self.max_errors = max(1, max_errors)
        self.verbose = verbose
        self.log_dir = Path(log_dir)
        self.use_probe = use_probe
        self.turn_timeout = max(10.0, turn_timeout)
        self.coins = max(0, int(coins))
        self.apply_fit = apply_fit
        self._view_fitted = False
        self.email = email or None
        self.password = password or None
        self._signed_in = False
        self.trace_board = trace_board
        self.rules = rules
        # how long the opponent gets to accept a rematch before the bot leaves the
        # table and looks for somebody new (the site itself waits 30 s)
        self.rematch_wait = max(1.0, float(rematch_wait))
        # the GUI pipes every line into a text widget and can ask the loop to stop
        self._log = log
        self.should_stop = should_stop
        self.lobby_walk = lobby_walk

        # calibrated against the site: which engine colour we are, plus whatever
        # the site answered on the last probe ({from_square: [first_landing, ...]})
        self.calibrated = self.my_engine_color is not None
        self.probe_map: Dict[int, List[int]] = {}

        self.history: List[object] = []
        self.moves_played = 0
        self.games_played = 0
        self.failures = 0
        self._last_wait_log = 0.0

    # ------------------------------------------------------------------
    # logging / diagnostics
    # ------------------------------------------------------------------
    def log(self, message: str) -> None:
        if self._log is not None:
            self._log(message)
        else:
            print(message, flush=True)

    def _stop_requested(self) -> bool:
        return bool(self.should_stop is not None and self.should_stop())

    def _save_debug(self, tag: str) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.site.screenshot(self.log_dir / f"{tag}-{stamp}.png")
        self.site.dump_html(self.log_dir / f"{tag}-{stamp}.html")
        self.log(f"  debug files written to {self.log_dir}/ ({tag}-{stamp}.*)")

    @staticmethod
    def _names(squares: Sequence[int]) -> str:
        return ", ".join(square_name(square) for square in squares) or "-"

    @staticmethod
    def _first_landings(moves: Sequence[Move], origin: int) -> set:
        return {first_landing(move) for move in moves if move.from_square == origin}

    def _plausible_offers(self, origin: int, offers: Sequence[int]) -> bool:
        """Could these squares really be destinations of a stone on ``origin``?

        Checkers moves are diagonal: one or two squares for men (and for kings
        under the English rules), any distance for the flying kings of Russian
        draughts.  Anything else is a leftover from a previous selection (the site
        repaints its ``.active`` squares only when the server answers).
        """
        origin_x, origin_y = xy_from_square(origin)
        longest = 7 if self.rules in ("russian", "brazilian", "joker") else 2
        for square in offers:
            x, y = xy_from_square(square)
            delta_x, delta_y = abs(x - origin_x), abs(y - origin_y)
            if delta_x != delta_y or not 1 <= delta_x <= longest:
                return False
        return True

    # ------------------------------------------------------------------
    # colours
    # ------------------------------------------------------------------
    def _resolve_site_color(self, state: SiteState) -> bool:
        """Which colour on the site are our stones?"""
        authoritative = str(state.player_color or "").lower()
        clickable = state.clickable_colors()
        if authoritative in SITE_COLORS:
            if self.my_site_color != authoritative:
                self.log(f"site says our stones are {authoritative}")
                if self.my_site_color is not None:
                    self.my_engine_color = None
                    self.calibrated = False
                self.my_site_color = authoritative
        if self.my_site_color is None:
            if len(clickable) == 1:
                self.my_site_color = clickable[0]
                self.log(f"detected our stones: {self.my_site_color}")
            elif len(state.squares) >= 32 and authoritative not in SITE_COLORS:
                self.log("! cannot detect our colour - pass --site-color white|black")
                return False
        elif clickable and self.my_site_color not in clickable:
            self.log(f"! {self.my_site_color} stones are not clickable "
                     f"({', '.join(clickable)}) - switching to {clickable[0]}")
            self.my_site_color = clickable[0]
            self.my_engine_color = None
            self.calibrated = False
        return True

    # ------------------------------------------------------------------
    # the site is the referee: probe which moves it really offers
    # ------------------------------------------------------------------
    def _clickable_stones(self, state: SiteState) -> List[int]:
        return [
            index
            for index, stone in state.stone_by_square.items()
            if stone.get("color") == self.my_site_color and stone.get("clickable")
        ]

    def _probe_stone(
        self, square_index: int, stone: Dict[str, object], timeout: float = 1.5
    ) -> Optional[List[int]]:
        """Click one of our stones and return the squares the site offers.

        The site repaints ``.game-square.active`` only after its ``check_stone``
        request comes back, so the answer is accepted only when it is a
        geometrically possible move of *this* stone (see ``_plausible_offers``).
        ``[]`` means the site refused the piece (mandatory capture elsewhere),
        ``None`` means the page could not be read at all.
        """
        self.site.clear_highlights()             # makes the "refused" hint reliable
        answered = self.site.click_stone(str(stone["id"]))
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.site.read_state()
            if state is None:
                time.sleep(0.06)
                continue
            offers = state.active_squares()
            if offers and self._plausible_offers(square_index, offers):
                return offers
            if not offers and state.highlighted_stones():
                return []                        # "not this piece - move another one"
            time.sleep(0.06)
        # nothing plausible arrived in time: we do not know what the site offers
        if not answered:
            self.log(f"  (the site did not answer while probing "
                     f"{square_name(square_index)} - will verify the click instead)")
        return None

    def probe_moves(
        self,
        state: SiteState,
        origins: Optional[Sequence[int]] = None,
        limit: Optional[int] = None,
    ) -> Dict[int, List[int]]:
        """Ask the site for the legal destinations of our stones.

        Returns ``{from_square: [first_landing, ...]}`` exactly as the site
        answers them, so the engine can be restricted to moves the server accepts.

        Every probe is a real click plus a board read, so this is the slowest
        part of a turn: ``limit`` stops after that many answers (the calibration
        only needs a couple of them) and stones the site highlights - the ones a
        forced capture allows - are asked first.
        """
        if origins is None:
            origins = self._probe_order(state)
        if not self.site._wait_can_click(2500):
            # the client refuses clicks until the server hands the turn over, so a
            # probe now would only raise toasts and read an empty board
            self.log("  (the site is not ready for clicks - skipping the probe)")
            return {}
        probe: Dict[int, List[int]] = {}
        for index in origins:
            if limit is not None and sum(1 for offers in probe.values() if offers) >= limit:
                break
            stone = state.stone_at(index)
            if stone is None or not stone.get("clickable"):
                continue
            offers = self._probe_stone(index, stone)
            if offers is None:
                continue
            probe[index] = offers
            state = self.site.read_state() or state
        return probe

    def _probe_order(self, state: SiteState) -> List[int]:
        """Clickable stones, the ones the site highlights (forced capture) first."""
        clickable = self._clickable_stones(state)
        highlighted = state.highlighted_stones()
        if not highlighted:
            return clickable
        first = [index for index in clickable if index in highlighted]
        rest = [index for index in clickable if index not in highlighted]
        return first + rest

    def _calibrate(self, state: SiteState, probe: Dict[int, List[int]]) -> bool:
        """Decide which engine colour we are by matching the site's move offers."""
        if self.my_engine_color is not None:
            self.calibrated = True
            return True
        if not probe:
            self.log("! the site did not offer a single move - cannot calibrate")
            return False

        scores: Dict[int, int] = {}
        for candidate in (RED, BLACK):
            board = self.build_board(state, engine_color=candidate)
            moves = board.generate_moves()
            scores[candidate] = sum(
                len(self._first_landings(moves, origin) & set(offers))
                for origin, offers in probe.items()
            )

        best = RED if scores[RED] >= scores[BLACK] else BLACK
        self.my_engine_color = best
        self.ai.color = best
        self.calibrated = True
        self.log(
            "calibrated: our engine side is "
            + ("RED (our men move up)" if best == RED else "BLACK (our men move down)")
            + f"   [site match red={scores[RED]} black={scores[BLACK]}]"
        )
        if scores[RED] == 0 and scores[BLACK] == 0:
            self.log("! the site's answers match neither orientation - run --discover")
        return True

    def _infer_engine_color(self, state: SiteState) -> int:
        """Fallback when probing is off: men of the side moving up are RED."""
        mine: List[int] = []
        theirs: List[int] = []
        for index, stone in state.stone_by_square.items():
            if stone.get("king"):
                continue
            (mine if stone.get("color") == self.my_site_color else theirs).append(index // 8)
        if mine and theirs:
            mine_row = sum(mine) / len(mine)
            their_row = sum(theirs) / len(theirs)
            if abs(mine_row - their_row) > 0.5:
                return RED if mine_row > their_row else BLACK
        self.log("! no men on the board to read the direction - assuming RED "
                 "(pass --side red|black to force it)")
        return RED

    # ------------------------------------------------------------------
    # board conversion
    # ------------------------------------------------------------------
    def build_board(self, state: SiteState, engine_color: Optional[int] = None) -> Board:
        """Turn a website snapshot into the board our engine understands."""
        color = self.my_engine_color if engine_color is None else engine_color
        cells = [EMPTY] * 64
        for index, stone in state.stone_by_square.items():
            mine = stone.get("color") == self.my_site_color
            stone_color = color if mine else -color
            king = bool(stone.get("king"))
            if stone_color == RED:
                cells[index] = RED_KING if king else RED_MAN
            else:
                cells[index] = BLACK_KING if king else BLACK_MAN
        turn = color if state.my_turn else -color
        return Board(cells, turn, self.rules)

    # ------------------------------------------------------------------
    # one move
    # ------------------------------------------------------------------
    def play_one_move(self) -> Optional[Move]:
        """Read the board, pick a move and click it.  ``None`` if nothing was done."""
        state = self.site.read_state()
        if state is None or state.finished or not state.my_turn:
            return None
        if not state.can_click:
            # the client disables clicks while a request is in flight
            time.sleep(0.4)
            return None
        if not self._resolve_site_color(state):
            # not an error yet: the server has not told us which stones are ours
            time.sleep(0.5)
            return None
        if not state.clickable_colors():
            # the table is open but the game has not started (bet dialog, opponent
            # search, ...) - waiting is the right thing to do
            self._log_waiting(state)
            time.sleep(self.poll)
            return None

        # 1) settle which engine colour we are - the site's answers decide it
        if not self.calibrated:
            if self.use_probe:
                # two or three answers are enough to tell the orientation apart,
                # and every probe costs a click plus a board read
                probe = self.probe_moves(state, limit=3)
                self.probe_map = probe
                if not self._calibrate(state, probe):
                    self._log_current_view()
                    self.failures += 1
                    return None
            else:
                self.my_engine_color = self._infer_engine_color(state)
                self.ai.color = self.my_engine_color
                self.calibrated = True
            state = self.site.read_state() or state

        board = self.build_board(state)
        legal = board.generate_moves()
        if not legal:
            self.log("! the site says it is our turn but our rules find no legal move")
            self._log_current_view()
            self._save_debug("no-legal-move")
            self.failures += 1
            return None

        self.history.append(board.position_key())
        if self.trace_board:
            self.log(f"board (our stones are "
                     f"{'r/R' if self.my_engine_color == RED else 'b/B'}):")
            for line in str(board).splitlines():
                self.log("   " + line)
        move = self.ai.choose_move(board, history=self.history)
        if move is None:
            self.failures += 1
            return None

        stone = state.stone_at(move.from_square)
        if stone is None or not stone.get("clickable"):
            self.log(f"! {square_name(move.from_square)} holds no clickable stone of ours - "
                     "asking the site which pieces may move")
            return self._recover_with_site_moves(state)

        # 2) the site is the referee: only play what it offers
        if self.use_probe:
            offers = self._probe_stone(move.from_square, stone)
            if offers is not None and (stone.get("king") or self.trace_board):
                predicted = sorted(self._first_landings(legal, move.from_square))
                self.log(f"  probe {square_name(move.from_square)}"
                         f"{' (king)' if stone.get('king') else ''}: "
                         f"site {self._names(offers)} | engine {self._names(predicted)}")
            if offers is None:
                self.log("  (no clear answer from the site - playing our move and "
                         "verifying every click on the board)")
            elif first_landing(move) not in offers:
                self.log(f"! the site does not offer {square_name(first_landing(move))} from "
                         f"{square_name(move.from_square)} (it offers: {self._names(offers)})")
                return self._recover_with_site_moves(state)

        self.log(f"our move: {move.notation()}   [{self.ai.stats}]")
        if self.dry_run:
            self.log("dry run - no clicks were sent")
            return move
        if not self._execute_move(move, state, already_selected=self.use_probe):
            self.failures += 1
            if self.failures > 1:                  # do not spam dumps for blips
                self._save_debug("move-rejected")
            return None

        self.failures = 0
        self.moves_played += 1
        return move

    def _recover_with_site_moves(self, state: SiteState) -> Optional[Move]:
        """Our move was not offered: re-probe everything and play a site-legal move."""
        state = self.site.read_state() or state      # always work from a fresh snapshot
        probe = self.probe_moves(state)
        self.probe_map = probe
        if not probe:
            # the site answered nothing at all - usually it is still busy with the
            # previous step; waiting is better than counting a failure
            self.log("the site did not answer any stone - waiting a moment")
            self._log_current_view()
            time.sleep(0.8)
            return None

        board = self.build_board(state)
        candidates = site_legal_moves(board, probe)
        if not candidates and not self.calibrated:
            # Only *before* the first successful calibration may our side be wrong.
            # Flipping it later would wreck a perfectly good board, so never do it.
            other = BLACK if self.my_engine_color == RED else RED
            board = self.build_board(state, engine_color=other)
            candidates = site_legal_moves(board, probe)
            if candidates:
                self.my_engine_color = other
                self.ai.color = other
                self.log("! switched our engine side (first calibration)")

        if candidates:
            move = self.ai.choose_move(board, history=self.history, allowed=candidates)
            if move is not None:
                self.log(f"our move (site-synced): {move.notation()}   [{self.ai.stats}]")
                if self.dry_run:
                    self.log("dry run - no clicks were sent")
                    return move
                if self._execute_move(move, state):
                    self.failures = 0
                    self.moves_played += 1
                    return move
            self.failures += 1
            if self.failures > 1:
                self._save_debug("move-rejected")
            return None

        # Last resort: the site and our board model disagree (a stale king flag and
        # similar).  Play the move the site itself offers so the game keeps going.
        return self._play_site_move(state, probe)

    def _play_site_move(self, state: SiteState, probe: Dict[int, List[int]]) -> Optional[Move]:
        """Click a move the site offers even when our board model disagrees."""
        for origin in sorted(probe):
            destinations = probe[origin]
            if not destinations:
                continue
            landing = destinations[0]
            stone = state.stone_at(origin)
            absolute = state.absolute_for(landing)
            if stone is None or absolute is None:
                continue
            self.log(f"! our board disagrees with the site - playing its move "
                     f"{square_name(origin)} -> {square_name(landing)}")
            if self.dry_run:
                self.log("dry run - no clicks were sent")
                return Move(origin, landing)
            try:
                self.site.click_stone(str(stone["id"]))
                self.site.click_square(absolute)
            except SiteError as error:
                self.log(f"! the site's move failed: {error}")
                continue
            self._complete_any_chain()          # finish a jump the site is still running
            time.sleep(0.3)
            self._wait_for_turn_to_pass()
            self.failures = 0
            self.moves_played += 1
            return Move(origin, landing)

        self.log("! the site offers no clickable move - skipping this turn")
        self._log_current_view()
        self.failures += 1
        if self.failures > 1:
            self._save_debug("site-mismatch")
        return None

    def _execute_move(self, move: Move, state: SiteState, already_selected: bool = False) -> bool:
        """Click stone -> destination(s), the way the site's own client does it."""
        stone = state.stone_at(move.from_square)
        if stone is None:
            self.log(f"! {square_name(move.from_square)} holds no stone")
            return False

        path = move_landing_path(move)
        self.log("  clicking " + square_name(move.from_square) + " -> "
                 + " -> ".join(square_name(square) for square in path))

        if not already_selected:
            self.site.click_stone(str(stone["id"]))

        current = move.from_square
        for index, landing in enumerate(path):
            if not self._wait_for_active(landing, current):
                if index == 0:
                    self.log(f"! the site never offered {square_name(landing)} as a destination")
                    self._log_current_view()
                    return False
                # a chain is already under way: finish it with what the site allows
                self.log(f"! the site ended the jump at {square_name(current)} - "
                         "finishing the chain with the squares it offers")
                return self._complete_any_chain()

            absolute = state.absolute_for(landing)
            if absolute is None:
                self.log(f"! cannot map {square_name(landing)} to a site square")
                return False
            self.site.click_square(absolute)

            if not self._wait_for_stone_at(landing):
                state_now = self.site.read_state()
                if state_now is not None and state_now.finished:
                    # the game ended on this very move (the opponent is wiped out)
                    self.log("  the game ended - no more clicks needed")
                    return True
                self.log(f"! the click on {square_name(landing)} did not move the stone")
                self._log_current_view()
                if index == 0:
                    return False
                return self._complete_any_chain()
            current = landing

        return self._wait_until_move_done(move)

    def _wait_for_active(self, square_index: int, origin: Optional[int] = None,
                         timeout: float = 8.0) -> bool:
        """Wait until the site marks ``square_index`` as a plausible destination."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.site.read_state()
            if state is not None:
                offers = state.active_squares()
                if square_index in offers and (
                    origin is None or self._plausible_offers(origin, offers)
                ):
                    return True
            time.sleep(0.08)
        return False

    def _wait_for_stone_at(self, square_index: int, timeout: float = 4.0) -> bool:
        """Wait until one of our stones really sits on ``square_index``."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.site.read_state()
            if state is not None:
                stone = state.stone_at(square_index)
                if stone is not None and stone.get("color") == self.my_site_color:
                    return True
            time.sleep(0.06)
        return False

    def _complete_any_chain(self, max_steps: int = 8) -> bool:
        """Never leave a half-played jump: keep clicking the squares the site offers."""
        for _ in range(max_steps):
            state = self.site.read_state()
            if state is None:
                return False
            offers = state.active_squares()
            if not offers:
                return True
            absolute = state.absolute_for(offers[0])
            if absolute is None:
                return False
            self.log(f"  continuing the jump to {square_name(offers[0])}")
            try:
                self.site.click_square(absolute)
            except SiteError as error:
                self.log(f"! clicking {square_name(offers[0])} failed: {error}")
                return False
            time.sleep(0.15)
        return False

    def _log_current_view(self) -> None:
        """Log what we can see right now - used when a click is rejected."""
        state = self.site.read_state()
        if state is None:
            self.log("  (the board could not be read)")
            return
        actives = self._names(state.active_squares())
        clickable = ", ".join(state.clickable_colors()) or "-"
        highlighted = ", ".join(state.highlighted_colors()) or "-"
        self.log(f"  destinations offered: {actives}")
        self.log(f"  clickable colours   : {clickable}   highlighted: {highlighted}")
        if self.probe_map:
            offered = "; ".join(
                f"{square_name(origin)} -> {self._names(destinations)}"
                for origin, destinations in sorted(self.probe_map.items())
            )
            self.log(f"  site offers         : {offered}")
        if self.my_site_color is not None and self.my_engine_color is not None:
            self.log(f"  our ({self.my_site_color}) stones are r/R in this dump:")
            for line in str(self.build_board(state)).splitlines():
                self.log("   " + line)

    def _wait_until_move_done(self, move: Move, timeout: float = 10.0) -> bool:
        """Confirm the move and wait until the server hands the turn over."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.site.read_state()
            if state is not None and not state.active_squares():
                destination = state.stone_at(move.to_square)
                arrived = destination is not None and destination.get("color") == self.my_site_color
                origin = state.stone_at(move.from_square)
                left = origin is None or origin.get("color") != self.my_site_color
                if arrived or left:
                    # player.action stays "yes" until the site's next step update,
                    # so wait for it instead of trying to move twice in one turn
                    self._wait_for_turn_to_pass()
                    time.sleep(0.3)          # let the client finish its slide animation
                    return True
            time.sleep(0.1)
        self.log("! could not confirm the move on the board")
        self._save_debug("move-not-confirmed")
        return False

    def _wait_for_turn_to_pass(self, timeout: float = 8.0) -> None:
        """Wait until the site no longer reports our turn (step update arrived)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.site.read_state()
            if state is None:
                time.sleep(0.2)
                continue
            if not state.my_turn or state.finished:
                return
            time.sleep(0.2)

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------
    def _log_waiting(self, state: SiteState) -> None:
        now = time.time()
        if now - self._last_wait_log < 10.0:
            return
        self._last_wait_log = now
        if self.verbose:
            suffix = " (waiting for an opponent)" if state.waiting else ""
            if state.dialog:
                suffix += " (a site dialog is open - confirm or close it)"
            self.log(f"waiting for our turn... [{state.describe()}]{suffix}")

    def _sign_in_if_needed(self) -> bool:
        """Log in when the site asks for it.  Returns False when that is blocking."""
        dialog = self.site.has_auth_dialog()
        if not (self.site.needs_sign_in() or dialog):
            return True
        if not (self.email and self.password):
            self.log("! the site asks to sign in - pass --email/--password "
                     "(or log in once with --profile, the cookies stay there)")
            return False
        self._signed_in = self.site.sign_in(self.email, self.password)
        return self._signed_in

    def _open_board(self) -> bool:
        """Wait for the board, walking the site's lobby when needed."""
        self._sign_in_if_needed()
        if self.site.wait_for_board(timeout_ms=4000):
            self._after_board()
            return True
        if not self.lobby_walk:
            # "start from an already open table": never touch the lobby, just wait
            self.log("waiting for an open table - open one in the browser")
            if self.site.wait_for_board(timeout_ms=int(self.turn_timeout * 1000)):
                self._after_board()
                return True
            self.log("! no board is open - open a table in the browser first "
                     "(or let the bot walk the lobby)")
            return False
        for _ in range(6):
            if not self._sign_in_if_needed():
                return False
            if not self.site.enter_game(self.coins):
                break
            self.log("waiting for the table to open...")
            if self.site.wait_for_board(timeout_ms=25000):
                self._after_board()
                return True
        if self.site.wait_for_board(timeout_ms=int(self.turn_timeout * 1000)):
            self._after_board()
            return True
        return False

    def _after_board(self) -> None:
        """Make the whole game visible and report the geometry once."""
        if not self.apply_fit or self._view_fitted:
            return
        self._view_fitted = True
        info = self.site.fit_view()
        if not info:
            return
        self.log(
            f"view: {info.get('vw')}x{info.get('vh')} window, board {info.get('board')}px, "
            f"board+panels {info.get('needed')}px -> "
            f"{'everything fits' if info.get('fits') else 'still overflowing'}"
        )

    # ------------------------------------------------------------------
    # after a game: rematch, or a new opponent
    # ------------------------------------------------------------------
    def _reset_for_new_game(self) -> None:
        """Forget the previous table - a rematch can swap the colours."""
        self.history.clear()
        self.probe_map = {}
        self.failures = 0
        self.my_site_color = (
            None if self.requested_site_color == "auto" else self.requested_site_color
        )
        if self.requested_side == "red":
            self.my_engine_color = RED
        elif self.requested_side == "black":
            self.my_engine_color = BLACK
        else:
            self.my_engine_color = None
        self.calibrated = self.my_engine_color is not None

    def _next_game(self, state: SiteState) -> bool:
        """Try a rematch; if nobody wants one, leave and find a new opponent.

        The site's own finish dialog offers "Rematch" for 30 s (it POSTs
        ``act=rematch_request`` and then waits); on acceptance it reloads the page
        with a new ``game_id``, and on refusal it drops back to the lobby.  The
        bot does the same thing: ask once, wait, then walk the lobby again.
        """
        self.games_played += 1
        self.log(
            f"game {self.games_played} is over - waiting up to "
            f"{int(self.rematch_wait)}s for a rematch"
        )
        request_sent = False
        fresh_reads = 0
        deadline = time.time() + self.rematch_wait
        while time.time() < deadline:
            if self._stop_requested():
                return False
            current = self.site.read_state()
            # ``status`` stays "finish" while the site shows its dialogs, so the
            # board only looks unfinished once the server really started a new
            # game - requiring two reads keeps a flicker from counting
            if current is not None and not current.finished:
                fresh_reads += 1
                if fresh_reads >= 2:
                    self._reset_for_new_game()
                    self.log("a new game started - playing on")
                    return True
            else:
                fresh_reads = 0
            if not request_sent:
                answer = str(self.site.request_rematch())
                request_sent = answer not in ("", "none", "None")
                if request_sent:
                    self.log(f"rematch requested ({answer})")
                else:
                    self.log("the site offers no rematch - the opponent left")
                    break
            time.sleep(0.5)

        self._reset_for_new_game()
        if not self.lobby_walk:
            self.log("lobby walking is off - stopping after this game")
            return False

        self.log("leaving the table and looking for a new opponent")
        self.site.open_lobby()
        time.sleep(2.0)
        for _ in range(8):
            if self._stop_requested():
                return False
            if self.site.enter_game(self.coins):
                if self.site.wait_for_board(timeout_ms=30000):
                    self._after_board()
                    self.log("new table found - playing")
                    return True
            time.sleep(2.0)
        self.log("! no new table was found")
        return False

    def run(self, max_moves: int = 0, once: bool = False, max_games: int = 0) -> int:
        if not self._open_board():
            self.log("! the checkers board never appeared. Open a table in the browser "
                     "(or use --url / --cdp) and try again.")
            self._save_debug("no-board")
            return 1

        self.log("board detected - bot running (Ctrl+C stops it)")
        while True:
            try:
                if self._stop_requested():
                    self.log("stopped by the user")
                    return 0
                state = self.site.read_state()
                if state is None:
                    time.sleep(self.poll)
                    continue
                if state.finished:
                    if (
                        once
                        or (max_moves and self.moves_played >= max_moves)
                        or (max_games and self.games_played >= max_games)
                    ):
                        self.log("the game is over - stopping")
                        break
                    if not self._next_game(state):
                        self.log("! could not start another game - stopping")
                        return 0
                    continue
                if not state.my_turn:
                    self._log_waiting(state)
                    time.sleep(self.poll)
                    continue

                self._last_wait_log = 0.0
                move = self.play_one_move()
                if move is None:
                    if self.failures > self.max_errors:
                        self.log("! too many failures in a row - stopping")
                        return 1
                    # back off a little when the site keeps disagreeing
                    time.sleep(min(5.0, self.poll * (1 + self.failures)))
                    continue
                if once or (max_moves and self.moves_played >= max_moves):
                    break
                time.sleep(self.poll * 0.5)
            except KeyboardInterrupt:
                self.log("stopped by the user")
                return 0
            except Exception as error:
                # both backends raise SiteError for page problems, but a closed
                # browser surfaces as PlaywrightError and a lost WKWebView as a
                # runtime error - the bot counts all of them and keeps going
                self.failures += 1
                self.log(f"! site error: {error}")
                self._save_debug("site-error")
                if self.failures > self.max_errors:
                    return 1
                time.sleep(min(10.0, self.poll * 2))

        self.log(f"finished - {self.moves_played} move(s) in "
                 f"{self.games_played} game(s)")
        return 0
