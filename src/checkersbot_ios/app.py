"""The Toga UI of the iOS app: a WebView with the bot attached to it.

Toga 0.5 changed the startup contract: instead of overriding ``App.startup``
and creating a window yourself, you hand Toga a **startup callable that returns
the main window's content**.  Returning ``None`` (or making the method async,
which Toga never awaits) leaves the app on an empty - black - window, which is
exactly what the first build did.  So :meth:`CheckersBotApp.build_content`
returns the root widget, and everything in it is wrapped in ``try/except``: a
widget that cannot be created on the device is reported *on the screen* instead
of a blank window.

The screen is deliberately simple: a status line, start/stop + reload buttons,
the ``toga.WebView`` that shows jokergame.cc (log in there, open a table) and a
small log.  The page title is read through the same JavaScript bridge the bot
uses, so a blank WebView is immediately distinguishable from a broken layout.

The bot runs in a worker thread (a 3 s search must not freeze the UI) and talks
to the WebView through :meth:`CheckersBotApp.evaluate`, which marshals every
JavaScript call onto the Toga event loop and blocks the worker until it returns.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import traceback
from typing import Any, Optional

import toga
from toga.style import Pack
from toga.style.pack import COLUMN, ROW

from checkersbot.bot import CheckersBot
from checkersbot.site import SiteError

from .site import WebViewSite

DEFAULT_URL = "https://jokergame.cc/checkers"
LOG_LINES = 40
LOG_REFRESH_S = 0.5   # the log view is redrawn at most twice a second (CPU/heat)
DEPTH = 18           # engine search depth cap (the clock decides what fits)
SECONDS = 6.0        # hard cap per move - the search never runs longer than this
SOFT_SECONDS = 3.0   # no new iteration starts after this: the phone stays cool
NARROW_SECONDS = 2.0  # budget for positions with 2-3 legal moves (no long plan)
BOARD_POLL_S = 1.0   # board polling: every read is a JavaScript round trip
THINK_DELAYS = (2.0, 3.0, 5.0, 7.0)   # human-like pauses (the first move is quick)
QUICK_DELAYS = (1.0, 2.0, 3.0)        # used when the move was obvious (e.g. a capture)
STAKE = 0            # lobby stake: 0 = 50 coins (the cheapest table)

#: The collapsible sheet: the game keeps the whole screen, the controls only take
#: the height of the bar while they are closed.
SHEET_BAR_H = 50
SHEET_BODY_H = 360
LOG_VIEW_H = 120

#: Every setting the panel owns, with its default.  ``CheckersBot`` is built from
#: this dict, and :func:`push_live_settings` copies it into a *running* bot.
DEFAULT_SETTINGS = {
    "strength": float(DEPTH),       # engine depth cap
    "seconds": float(SECONDS),      # hard limit per move
    "soft": float(SOFT_SECONDS),    # soft limit (heat)
    "poll": float(BOARD_POLL_S),    # board polling interval
    "max_games": 0.0,               # 0 = unlimited   (needs a restart)
    "max_moves": 0.0,               # 0 = unlimited   (needs a restart)
    "rematch_wait": 30.0,
    "chat_wait": 5.0,
    "stake": float(STAKE),
    "think": 1.0,                   # human "thinking" pauses
    "lose": 0.0,                    # lose on purpose (average-player moves)
    "accept_stake": 0.0,            # agree to a raised stake
    "auto_rematch": 1.0,            # wait for the opponent's rematch offer
    "dry_run": 0.0,                 # read-only mode, no clicks
    "trace": 0.0,                   # log the board / the opponent's answers
    "probe": 1.0,                   # verify every move against the site
}

#: Settings that are copied into the running bot immediately.
LIVE_KEYS = (
    "strength", "seconds", "soft", "poll", "rematch_wait", "chat_wait", "stake",
    "think", "lose", "accept_stake", "auto_rematch", "dry_run", "trace", "probe",
)

#: Settings the bot reads once, when the run starts: they need a restart.
RESTART_KEYS = ("max_games", "max_moves")

#: Live status indicator: name -> (dot colour, Georgian label).
STATES = {
    "stopped": ("#95a5a6", "გაჩერებული"),
    "running": ("#2ecc71", "მუშაობს"),
    "waiting": ("#f1c40f", "ელოდება"),
    "error": ("#e74c3c", "შეცდომა"),
}

#: Log lines that mean "the bot is doing nothing but waiting for the opponent".
WAITING_MARKERS = ("waiting", "ველოდებით", "no table yet", "thinking", "სვლას")


def state_text(state: str) -> str:
    """``● მუშაობს`` - the small status pill in the bar."""
    colour, label = STATES.get(state, STATES["stopped"])
    return f"● {label}"


def state_color(state: str) -> str:
    colour, _ = STATES.get(state, STATES["stopped"])
    return colour


def state_from_log(line: str) -> Optional[str]:
    """Guess the status from one log line (the bot's own words)."""
    text = str(line)
    if text.lstrip().startswith("!"):
        return "error"
    lowered = text.lower()
    if any(marker in text or marker in lowered for marker in WAITING_MARKERS):
        return "waiting"
    if "our move" in lowered:
        return "running"
    return None


def restart_only(name: str) -> bool:
    """Does changing ``name`` need the bot to be restarted?"""
    return name in RESTART_KEYS


def push_live_settings(bot: Any, settings: dict) -> list:
    """Copy the panel's settings into a *running* bot and say what was applied.

    Only :data:`LIVE_KEYS` are copied - every one of them is read by the bot on
    its next loop iteration (``poll``), move (``seconds``/``soft``/``dry_run``...)
    or game (``rematch_wait``), so no restart and no page reload is needed.
    ``max_games``/``max_moves`` are arguments of :meth:`CheckersBot.run`; they are
    deliberately **not** applied here (the UI says so instead).
    """
    if bot is None:
        return []
    applied: list = []
    engine_map = {
        "strength": ("ai", "max_depth", int),
        "seconds": (None, "time_limit", float),
        "soft": (None, "soft_time", float),
        "poll": (None, "poll", float),
        "rematch_wait": (None, "rematch_wait", float),
        "chat_wait": (None, "chat_wait", float),
        "stake": (None, "coins", int),
        "dry_run": (None, "dry_run", bool),
        "trace": (None, "trace_board", bool),
        "probe": (None, "use_probe", bool),
        "accept_stake": (None, "accept_stake", bool),
        "auto_rematch": (None, "rematch", bool),
    }
    for key in LIVE_KEYS:
        if key not in settings:
            continue
        value = settings[key]
        if key in engine_map:
            owner, attribute, caster = engine_map[key]
            target = getattr(bot, owner) if owner else bot
            try:
                setattr(target, attribute, caster(value))
                applied.append(key)
            except Exception:                    # a missing attribute, a bad cast
                continue
        elif key == "think":
            applied.append(key)                  # the app polls its own flag
    return applied


class CheckersBotApp(toga.App):
    """A checkers board in a WebView, with the engine playing it."""

    def __init__(self) -> None:
        # Toga 0.5: the startup callable returns the main window's content.
        super().__init__(
            formal_name="CheckersBot",
            app_id="cc.jokergame.checkersbot",
            startup=self.build_content,
        )
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._bot_thread: Optional[threading.Thread] = None
        self._bot: Optional[CheckersBot] = None
        self._stop = threading.Event()
        self._lose_flag = False
        #: off by default: a bigger stake costs coins, so the bot only agrees when
        #: the "💰 ფსონის გაზრდაზე დათანხმება" switch is on
        self._accept_stake = False
        #: wait for the opponent's rematch offer after every game (the 🔁 switch
        #: turns that waiting off; the bot never offers a rematch itself)
        self._auto_rematch = True
        #: human "thinking" pauses (the ⏱ switch) - polled by the bot
        self._human_think = True
        #: every setting the sheet owns; ``push_live_settings`` copies it into the
        #: running bot, so the sheet is the single source of truth
        self.cfg: dict = dict(DEFAULT_SETTINGS)
        self._state_name = "stopped"
        #: log lines waiting for the next (throttled) log-view redraw
        self._pending_log: list = []
        self._log_refresh_at = None

    # -- window content -------------------------------------------------
    def build_content(self, app, **kwargs) -> Any:
        """Called by Toga before the main window is shown; returns the content."""
        self._capture_loop()
        try:
            return self._build_ui()
        except Exception:
            return self._boot_error(traceback.format_exc())

    def _build_ui(self) -> Any:
        """Full-screen game + a collapsible sheet that holds every control.

        One window, two children: the ``stage`` (the WebView, ``flex=1``) and the
        ``sheet``.  Closed, the sheet is only a 50 pt bar, so the game keeps ~93 %
        of the screen (the old fixed panel took ~35 %).  Opening the sheet hides
        and shows *widgets of the sheet* - the WebView is never re-created,
        reloaded or zoomed, it simply gets its space back when the sheet closes.
        """
        self.cfg = dict(DEFAULT_SETTINGS)
        self._state_name = "stopped"
        self._style_book = self._sheet_styles()
        self._build_game()
        self._build_bar()
        self._build_body()
        self._build_log()
        self._build_actions()
        self.sheet = toga.Box(
            style=Pack(direction=COLUMN, background_color="#111a22"),
            children=[self.bar, self.body, self.log_view, self.actions],
        )
        root = toga.Box(
            style=Pack(direction=COLUMN, flex=1, background_color="#0b0f14"),
            children=[self.stage, self.sheet],
        )
        self.log_line("აპი გაშვებულია — გახსენი მაგიდა, დააჭირე ▶-ს, ⚙ = პარამეტრები")
        return root

    # -- building blocks -------------------------------------------------
    def _build_game(self) -> None:
        """The game keeps the whole screen; nothing is stacked above it."""
        self.webview = toga.WebView(style=Pack(flex=1), url=DEFAULT_URL)
        self.webview.on_webview_load = self.on_webview_load
        self.stage = toga.Box(style=Pack(direction=COLUMN, flex=1),
                              children=[self.webview])

    def _build_bar(self) -> None:
        """⚙ settings · ● status · ▶/■ start-stop · 📜 log - one 50 pt row."""
        style = self._style_book
        self.sheet_button = toga.Button(
            "⚙", on_press=self.on_toggle_sheet,
            style=Pack(width=52, margin=0),
        )
        self.status = toga.Label(
            state_text("stopped"),
            style=Pack(flex=1, margin=(0, 8, 0, 8), color=state_color("stopped"),
                       font_size=13, font_weight="bold"),
        )
        self.start_button = toga.Button(
            "▶", on_press=self.on_start, style=Pack(width=52, margin=0)
        )
        self.log_button = toga.Button(
            "📜", on_press=self.on_toggle_log, style=Pack(width=52, margin=0)
        )
        self.bar = toga.Box(
            style=Pack(direction=ROW, height=SHEET_BAR_H, background_color="#111a22",
                       margin=(4, 6, 4, 6)),
            children=[self.sheet_button, self.status, self.start_button, self.log_button],
        )

    def _build_log(self) -> None:
        self.log_view = toga.MultilineTextInput(
            readonly=True,
            style=Pack(height=LOG_VIEW_H, display="none", margin=(0, 6, 4, 6),
                       font_size=11),
        )

    def _build_actions(self) -> None:
        style = self._style_book
        self.refresh_button = toga.Button(
            "↻ განახლება", on_press=self.on_refresh,
            style=Pack(flex=1, margin=(0, 4, 0, 6)),
        )
        self.reset_button = toga.Button(
            "⟲ ლობი", on_press=self.on_reload, style=Pack(width=110, margin=(0, 6, 0, 4))
        )
        self.actions = toga.Box(
            style=Pack(direction=ROW, height=44, display="none"),
            children=[self.refresh_button, self.reset_button],
        )

    def _sheet_styles(self) -> dict:
        """Colours/spacing of the sheet in one place (dark, phone friendly)."""
        return {
            "group": {"background_color": "#16212b", "margin": (6, 6, 8, 6)},
            "label": {"font_size": 12, "color": "#cfe3f5", "margin": (0, 6, 0, 0)},
            "value": {"font_size": 12, "color": "#7fd1ff", "font_weight": "bold",
                      "margin": (0, 0, 0, 6)},
        }

    # -- control handlers ------------------------------------------------
    def _make_slider_handler(self, key: str, value_label: Any):
        def handler(widget, **kwargs) -> None:
            self.cfg[key] = float(widget.value)
            value_label.text = self._format_value(key)
            self._on_setting_changed(key)
        return handler

    def _make_number_handler(self, key: str):
        def handler(widget, **kwargs) -> None:
            try:
                self.cfg[key] = float(widget.value or 0)
            except (TypeError, ValueError):
                return
            self._on_setting_changed(key)
        return handler

    def _make_selection_handler(self, key: str, items: list):
        def handler(widget, **kwargs) -> None:
            try:
                self.cfg[key] = float(items.index(widget.value))
            except (ValueError, AttributeError):
                return
            self._on_setting_changed(key)
        return handler

    def _make_switch_handler(self, key: str):
        def handler(widget, **kwargs) -> None:
            self.cfg[key] = 1.0 if widget.value else 0.0
            self._on_setting_changed(key)
        return handler

    # -- sheet / log toggles ---------------------------------------------
    def on_toggle_sheet(self, widget=None, **kwargs) -> None:
        """Open/close the settings body - the WebView keeps its page."""
        opening = self.body.style.display == "none"
        self.body.style.display = "pack" if opening else "none"
        self.actions.style.display = "pack" if opening else "none"
        self.sheet_button.text = "▾" if opening else "⚙"
        if opening:
            self._show_hint("")
        self.log_line("⚙ პარამეტრები " + ("გაიხსნა" if opening else "დაიხურა"))

    def on_toggle_log(self, widget=None, **kwargs) -> None:
        """Show/hide the log viewer (works with the settings sheet closed too)."""
        opening = self.log_view.style.display == "none"
        self.log_view.style.display = "pack" if opening else "none"
        if opening:
            self._flush_log()

    def _show_hint(self, text: str) -> None:
        hint = getattr(self, "hint", None)
        if hint is not None:
            hint.text = text

    def _on_setting_changed(self, key: str) -> None:
        """A control moved: push it into the running bot, or say why not."""
        if restart_only(key):
            self._show_hint(
                f"⚠ „{key}“ შემდეგ გაშვებაზე შევა ძალაში (გაშვებული ბოტი არ იცვლება)")
            return
        bot = self._bot
        self._lose_flag = bool(self.cfg["lose"])
        self._accept_stake = bool(self.cfg["accept_stake"])
        self._auto_rematch = bool(self.cfg["auto_rematch"])
        self._human_think = bool(self.cfg["think"])
        if bot is None:
            self._show_hint(
                f"💾 {key} = {self._format_value(key)} — ბოტი გაჩერებულია, "
                "გაშვებისას გამოყენდება")
            return
        applied = push_live_settings(bot, self.cfg)
        self._show_hint(
            f"✓ {key} = {self._format_value(key)} — მაშინვე მოქმედებს"
            if key in applied else "")

    def _set_state(self, state: str, detail: str = "") -> None:
        """Update the ● indicator (Running / Stopped / Waiting / Error)."""
        self._state_name = state
        label = getattr(self, "status", None)
        if label is not None:
            label.text = state_text(state)
            try:
                label.style.color = state_color(state)
            except Exception:                     # a backend without colour support
                pass

    def _build_body(self) -> None:
        """Grouped settings in a touch-scrollable container (hidden by default)."""
        groups = [
            ("🎚 ძრავა", [
                self._slider_row("strength", "სიძლიერე (სიღრმე)", 4, 24, 1,
                                 "სიღრმის ზღვარი — მეტი = ძლიერი, ნელი"),
                self._slider_row("seconds", "წამი/სვლა (ლიმიტი)", 1, 15, 0.5,
                                 "მაქსიმუმ რამდენ ხანს ეძებს ძრავი ერთ სვლაზე"),
                self._slider_row("soft", "რბილი ლიმიტი", 0.5, 10, 0.5,
                                 "ამის შემდეგ ახალ ვარიანტს აღარ იწყებს — ტელეფონი ნაკლებად ცხელდება"),
                self._slider_row("poll", "poll (წმ)", 0.3, 3, 0.1,
                                 "რამდენ ხანში ერთხელ კითხულობს დაფას"),
            ]),
            ("🎮 თამაში", [
                self._number_row("max_games", "მაქს. თამაში (0 = ∞)", 999),
                self._number_row("max_moves", "მაქს. სვლა (0 = ∞)", 999),
                self._slider_row("rematch_wait", "რემატჩის ლოდინი (წმ)", 5, 60, 5, ""),
                self._slider_row("chat_wait", "ჩატის ლოდინი (წმ)", 2, 20, 1, ""),
                self._selection_row("stake", "ფსონი"),
            ]),
            ("🧠 ქცევა", [
                self._switch_row("think", "⏱ ადამიანური ფიქრი (2/3/5/7 წმ)"),
                self._switch_row("lose", "😞 მარცხი (ადამიანურად)"),
                self._switch_row("accept_stake", "💰 ფსონის გაზრდაზე დათანხმება"),
                self._switch_row("auto_rematch", "🔁 ავტო-რემატჩი"),
            ]),
            ("🔍 დიაგნოსტიკა", [
                self._switch_row("dry_run", "👁 მხოლოდ ჩვენება (dry-run, არ აჭერს)"),
                self._switch_row("trace", "📋 დაფის ტრეისი + ოპონენტის პასუხები"),
                self._switch_row("probe", "🔎 probe — სვლების შემოწმება საიტზე"),
            ]),
        ]
        children: list = []
        for title, rows in groups:
            children.append(toga.Label(title, style=Pack(
                font_size=13, font_weight="bold", color="#8fb8d8", margin=(10, 6, 2, 6))))
            children.append(toga.Box(
                style=Pack(direction=COLUMN, **self._style_book["group"]), children=rows))
        self.hint = toga.Label("", style=Pack(font_size=11, color="#f1c40f",
                                              margin=(6, 8, 0, 8)))
        children.append(self.hint)
        children.append(toga.Label(
            "ცვლილებები მაშინვე მოქმედებს გაშვებულ ბოტზეც — გარდა „მაქს. თამაში / "
            "მაქს. სვლა“, რომლებიც შემდეგ გაშვებაზე შედის ძალაში.",
            style=Pack(font_size=10, color="#7f8f9f", margin=(4, 8, 8, 8))))
        self.body = toga.Box(
            style=Pack(direction=COLUMN, height=SHEET_BODY_H, display="none"),
            children=[toga.ScrollContainer(
                content=toga.Box(style=Pack(direction=COLUMN), children=children),
                horizontal=False,
                style=Pack(flex=1, background_color="#111a22"),
            )],
        )

    # -- one row per setting ---------------------------------------------
    def _slider_row(self, key: str, label: str, low: float, high: float, step: float,
                    hint: str) -> Any:
        value_label = toga.Label(self._format_value(key),
                                 style=Pack(**self._style_book["value"]))
        slider = toga.Slider(
            value=float(self.cfg[key]), min=float(low), max=float(high),
            on_change=self._make_slider_handler(key, value_label),
            style=Pack(flex=1, margin=(0, 6, 0, 0)),
        )
        setattr(self, f"{key}_slider", slider)
        head = toga.Box(
            style=Pack(direction=ROW, align_items="center"),
            children=[toga.Label(label, style=Pack(flex=1, **self._style_book["label"])),
                      value_label],
        )
        children = [head, slider]
        if hint:
            children.append(toga.Label(hint, style=Pack(font_size=10, color="#7f8f9f")))
        return toga.Box(style=Pack(direction=COLUMN, margin=(2, 0, 6, 0)),
                        children=children)

    def _number_row(self, key: str, label: str, high: int) -> Any:
        field = toga.NumberInput(
            value=int(self.cfg[key]), min=0, max=high, step=1,
            on_change=self._make_number_handler(key), style=Pack(width=90),
        )
        setattr(self, f"{key}_input", field)
        return toga.Box(
            style=Pack(direction=ROW, align_items="center", margin=(2, 0, 6, 0)),
            children=[toga.Label(label, style=Pack(flex=1, **self._style_book["label"])),
                      field],
        )

    def _selection_row(self, key: str, label: str) -> Any:
        items = ["50", "5 000", "50 000", "200 000", "1 000 000", "10 000 000"]
        selection = toga.Selection(
            items=items, value=items[int(self.cfg[key])],
            on_change=self._make_selection_handler(key, items), style=Pack(width=140),
        )
        setattr(self, f"{key}_selection", selection)
        return toga.Box(
            style=Pack(direction=ROW, align_items="center", margin=(2, 0, 6, 0)),
            children=[toga.Label(label, style=Pack(flex=1, **self._style_book["label"])),
                      selection],
        )

    def _switch_row(self, key: str, label: str) -> Any:
        switch = toga.Switch(
            label, value=bool(self.cfg[key]),
            on_change=self._make_switch_handler(key), style=Pack(margin=(2, 0, 2, 0)),
        )
        setattr(self, f"{key}_switch", switch)
        return switch

    def _format_value(self, key: str) -> str:
        value = float(self.cfg.get(key, 0))
        if key == "strength":
            return f"{int(value)}"
        if key in ("seconds", "soft"):
            return f"{value:.1f}s"
        return f"{value:.1f}"

    def _boot_error(self, text: str) -> Any:
        """Paint the traceback on the screen - a black window tells us nothing."""
        try:
            print(text, flush=True)
        except Exception:
            pass
        return toga.Box(
            style=Pack(direction=COLUMN, flex=1, background_color="#2a0000"),
            children=[toga.Label(text[-1500:], style=Pack(margin=8))],
        )

    # -- JavaScript bridge ----------------------------------------------
    def evaluate(self, script: str) -> Any:
        """Run JavaScript in the WebView from the worker thread and wait for it."""
        loop = self._loop
        if loop is None or loop.is_closed():
            raise SiteError("the app has not captured its event loop yet")
        future = asyncio.run_coroutine_threadsafe(self._evaluate(script), loop)
        return future.result(timeout=20)

    async def _evaluate(self, script: str) -> Any:
        try:
            return await self.webview.evaluate_javascript(script)
        except Exception as error:            # a navigation in flight, a dead view
            raise SiteError(str(error)) from error

    def _capture_loop(self) -> None:
        if self._loop is not None:
            return
        try:
            self._loop = self.loop          # Toga's own event loop
        except Exception:
            self._loop = None
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = None

    def on_webview_load(self, widget, **kwargs) -> None:
        self._capture_loop()
        self.log_line("გვერდი ჩაიტვირთა")
        try:
            asyncio.ensure_future(self._report_title())
        except RuntimeError:
            pass

    async def _report_title(self) -> None:
        """Proves the JS bridge works, even when the page looks blank."""
        try:
            title = await self.webview.evaluate_javascript("document.title")
            self.status.text = f"გვერდი: {title}"
        except Exception as error:
            self.log_line(f"! JS bridge: {error}")

    # -- logging --------------------------------------------------------
    def log_line(self, message: str) -> None:
        text = str(message)
        try:
            print(text, flush=True)           # shows up in the device console
        except Exception:
            pass
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._append_log, text)

    def _append_log(self, text: str) -> None:
        """Batch the log view: a Toga text view redraw costs real CPU on iOS."""
        view = getattr(self, "log_view", None)
        self._pending_log.append(text)
        state = state_from_log(text)
        if state is not None:
            self._set_state(state)            # ● მუშაობს / ● ელოდება / ● შეცდომა
        if view is None or self._log_refresh_at is not None:
            return                                   # a refresh is already queued
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        self._log_refresh_at = loop.call_later(LOG_REFRESH_S, self._flush_log)

    def _flush_log(self) -> None:
        """Redraw the log view at most once every ``LOG_REFRESH_S`` seconds."""
        self._log_refresh_at = None
        view = getattr(self, "log_view", None)
        if view is None or not self._pending_log:
            return
        pending, self._pending_log = self._pending_log, []
        current = (view.value or "").splitlines()
        lines = (current + pending)[-LOG_LINES:]
        view.value = "\n".join(lines) + "\n"

    # -- start / stop ---------------------------------------------------
    def _set_screen_awake(self, awake: bool) -> None:
        """Keep the phone awake while the bot plays.

        iOS suspends an app that goes to the background, and a game needs the
        WebView alive, so the practical answer is to stop the screen from
        locking: ``idleTimerDisabled`` keeps the app (and the game) running even
        when nobody touches the phone.
        """
        if sys.platform != "ios":
            return
        try:
            from rubicon.objc import ObjCClass

            UIApplication = ObjCClass("UIApplication")
            UIApplication.sharedApplication.idleTimerDisabled = bool(awake)
        except Exception as error:                  # never break the app for this
            self.log_line(f"  (screen-awake unavailable: {error})")

    def on_start(self, widget=None, **kwargs) -> None:
        self._capture_loop()
        if self._bot_thread is not None and self._bot_thread.is_alive():
            self._stop.set()
            self.start_button.text = "…"
            self._set_state("stopped")
            self.log_line("… გაჩერების მოთხოვნა (მიმდინარე სვლის შემდეგ)")
            return

        self._stop = threading.Event()
        self._lose_flag = bool(self.cfg["lose"])
        self.log_view.value = ""
        self.start_button.text = "■"
        self._set_state("running")

        site = WebViewSite(self.evaluate, log=self.log_line, log_dir=self.paths.logs)
        bot = CheckersBot(
            site,
            depth=int(self.cfg["strength"]),
            time_limit=float(self.cfg["seconds"]),
            soft_time=float(self.cfg["soft"]),
            narrow_time=NARROW_SECONDS,
            poll=max(0.1, float(self.cfg["poll"])),
            # fail fast when no table is open (the panel's 120 s is pointless here)
            turn_timeout=25.0,
            think_delays=THINK_DELAYS,
            quick_delays=QUICK_DELAYS,
            # the live switches are polled by the bot, so they work mid-game
            should_think_delays=lambda: self._human_think,
            rematch_wait=max(1.0, float(self.cfg["rematch_wait"])),
            chat_wait=max(1.0, float(self.cfg["chat_wait"])),
            # the phone layout already fits; scaling it would only shrink the board
            apply_fit=False,
            # the app plays until it is stopped: rematch, then a new opponent
            lobby_walk=True,
            coins=int(self.cfg["stake"]),
            use_probe=bool(self.cfg["probe"]),
            trace_board=bool(self.cfg["trace"]),
            dry_run=bool(self.cfg["dry_run"]),
            accept_stake=bool(self.cfg["accept_stake"]),
            should_accept_stake=lambda: self._accept_stake,
            rematch=bool(self.cfg["auto_rematch"]),
            should_rematch=lambda: self._auto_rematch,
            log=self.log_line,
            should_stop=self._stop.is_set,
            should_lose=lambda: self._lose_flag,
            log_dir=str(self.paths.logs),
        )
        self._bot = bot
        self._bot_thread = threading.Thread(
            target=self._run_bot, args=(bot,), name="checkersbot", daemon=True
        )
        self._set_screen_awake(True)
        self._bot_thread.start()
        self.log_line(
            "ბოტი მუშაობს — სიღრმე {}, {} წმ/სვლა (რბილი {}), poll {}".format(
                int(self.cfg["strength"]), self.cfg["seconds"], self.cfg["soft"],
                self.cfg["poll"])
        )
        self.log_line(
            "💰 ფსონის გაზრდა: " + ("ჩართულია" if self._accept_stake else "გამორთულია")
            + " | 🔁 ავტო-რემატჩი: " + ("ჩართულია" if self._auto_rematch else "გამორთულია")
            + " | 👁 dry-run: " + ("კი" if self.cfg["dry_run"] else "არა")
        )

    def on_refresh(self, widget=None, **kwargs) -> None:
        """↻ განახლება: restart the bot thread with the current settings.

        The WebView is *not* touched - the game page keeps running, only the bot
        is stopped and started again (needed by ``max_games`` / ``max_moves``).
        """
        if self._bot_thread is not None and self._bot_thread.is_alive():
            self._stop.set()
            self.log_line("↻ განახლება: ვაჩერებ ბოტს…")
            self._restart_when_idle()
            return
        self.log_line("↻ ბოტი თავიდან იშვება (გვერდი არ იტვირთება)")
        self.on_start()

    def _restart_when_idle(self, attempt: int = 0) -> None:
        if self._bot_thread is not None and self._bot_thread.is_alive() and attempt < 60:
            loop = self._loop
            if loop is not None and not loop.is_closed():
                loop.call_later(0.5, self._restart_when_idle, attempt + 1)
            return
        self.on_start()

    def _run_bot(self, bot: CheckersBot) -> None:
        code = 1
        try:
            code = bot.run(
                max_moves=int(self.cfg.get("max_moves", 0) or 0),
                max_games=int(self.cfg.get("max_games", 0) or 0),
            )
        except SiteError as error:
            self.log_line(f"! {error}")
        except Exception as error:                  # never lose the UI thread
            self.log_line(f"! ბოტი შეწყდა: {error}")
        finally:
            loop = self._loop
            if loop is not None and not loop.is_closed():
                loop.call_soon_threadsafe(self._bot_finished, code)

    def _bot_finished(self, code: int) -> None:
        self._set_screen_awake(False)
        self._lose_flag = False
        switch = getattr(self, "lose_switch", None)
        if switch is not None:                     # the 😞 switch goes back to off
            try:
                switch.value = False
            except Exception:
                pass
        self.cfg["lose"] = 0.0
        self.start_button.text = "▶"
        self._set_state("stopped" if code == 0 else "error")
        self._show_hint(f"ბოტი გაჩერდა (კოდი {code})" if code else "")

    async def on_reload(self, widget, **kwargs) -> None:
        """⟲ ლობი: reload the *page* (the only action that navigates the WebView)."""
        try:
            self.log_line("⟲ გვერდი გადაიტვირთა (ლობი)")
            await self.webview.load_url(self.webview.url or DEFAULT_URL)
        except Exception as error:
            self.log_line(f"! {error}")


def main() -> CheckersBotApp:
    """Briefcase/Toga entry point."""
    return CheckersBotApp()


if __name__ == "__main__":
    main().main_loop()

