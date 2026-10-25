"""Tkinter control panel for the jokergame.cc checkers bot.

This is also the entry point of the packaged ``CheckersBot.exe``:

* choose where the run starts - **sign in -> lobby** (walks the site's dialogs),
  **lobby only** (the browser profile is already signed in) or **already open
  table** (no lobby at all, just play the board that is on screen);
* choose the stake the lobby picks (``0`` = 50 coins ... ``5`` = 10 000 000);
* engine settings (depth, seconds per move), browser profile and channel;
* start / stop, "check the rules" (``--diagnose``), open the log and profile
  folders, and a live log of everything the bot prints.

All the real work is done by :func:`web_bot.run_bot`, so the GUI never has its
own copy of the game logic.  Passwords are kept in memory only - the saved
settings file never contains them.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

try:                                    # tkinter is missing only in odd Python builds
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except Exception:                       # pragma: no cover - the window cannot open
    tk = ttk = filedialog = messagebox = None  # type: ignore[assignment]

from web_bot import (
    DEFAULT_URL,
    build_parser,
    bundled_chromium_installed,
    installed_browser_channel,
    run_bot,
)

APP_NAME = "CheckersBot"

# the stake ladder the site renders itself (`.v2-coins-dialog .play-coins`)
STAKES: List[tuple] = [
    (0, "50"),
    (1, "5 000"),
    (2, "50 000"),
    (3, "200 000"),
    (4, "1 000 000"),
    (5, "10 000 000"),
]

# where the run should start
MODES: Dict[str, str] = {
    "auth_lobby": "ავტორიზაცია → ლობი",
    "lobby": "ლობი (პროფილი უკვე შესულია)",
    "board": "უკვე ღია დაფა (ლობის გარეშე)",
}

CHANNELS: Dict[str, str] = {
    "": "Playwright-ის Chromium",
    "chrome": "Google Chrome (ინსტალირებული)",
    "msedge": "Microsoft Edge (ინსტალირებული)",
}

MAX_LOG_LINES = 4000


# ----------------------------------------------------------------------
# settings
# ----------------------------------------------------------------------
def settings_path() -> Path:
    """Where the panel keeps its own settings (never the password)."""
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / APP_NAME / "settings.json"
    return Path.home() / f".{APP_NAME.lower()}" / "settings.json"


@dataclass
class Settings:
    """Everything the panel remembers between runs."""

    mode: str = "lobby"
    email: str = ""
    password: str = field(default="", repr=False)   # never written to disk
    stake: int = 0
    profile: str = ".browser-profile"
    log_dir: str = "logs"
    url: str = DEFAULT_URL
    depth: int = 9
    time: float = 5.0
    poll: float = 1.0
    turn_timeout: float = 120.0
    max_moves: int = 0
    max_games: int = 0
    rematch_wait: float = 30.0
    think_delays: str = "2,3,5,7"
    channel: str = ""
    window_size: str = ""
    headless: bool = False
    close_browser: bool = False     # default: leave the window open (Rematch / lobby)
    trace_board: bool = False
    dry_run: bool = False
    no_probe: bool = False
    no_fit: bool = False
    diagnose: bool = False
    cdp: str = ""


    # -- persistence ---------------------------------------------------
    def to_dict(self) -> Dict[str, object]:
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__          # type: ignore[attr-defined]
            if key != "password"
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "Settings":
        fields = cls.__dataclass_fields__                 # type: ignore[attr-defined]
        known = {key: value for key, value in (data or {}).items()
                 if key in fields and key != "password"}
        return cls(**known)

    def save(self, path: Optional[Path] = None) -> Path:
        target = Path(path) if path else settings_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
                          encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Settings":
        target = Path(path) if path else settings_path()
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        try:
            return cls.from_dict(data)
        except TypeError:
            return cls()

    # -- command line --------------------------------------------------
    def to_argv(self) -> List[str]:
        """Build the ``web_bot.py`` command line for these settings."""
        argv = [
            "--url", self.url,
            "--profile", self.profile,
            "--log-dir", self.log_dir,
            "--coins", str(int(self.stake)),
            "--depth", str(int(self.depth)),
            "--time", str(float(self.time)),
            "--poll", str(float(self.poll)),
            "--turn-timeout", str(float(self.turn_timeout)),
        ]
        if self.mode == "board":
            argv.append("--no-lobby")
        if self.mode == "auth_lobby":
            if self.email:
                argv += ["--email", self.email]
            if self.password:
                argv += ["--password", self.password]
        if self.cdp:
            argv += ["--cdp", self.cdp]
        if self.channel:
            argv += ["--channel", self.channel]
        if self.window_size:
            argv += ["--window-size", self.window_size]
        if self.max_moves:
            argv += ["--max-moves", str(int(self.max_moves))]
        if self.max_games:
            argv += ["--max-games", str(int(self.max_games))]
        argv += ["--rematch-wait", str(float(self.rematch_wait))]
        argv += ["--think-delays", self.think_delays]
        for flag, enabled in (
            ("--headless", self.headless),
            ("--close-browser", self.close_browser),
            ("--trace-board", self.trace_board),
            ("--dry-run", self.dry_run),
            ("--no-probe", self.no_probe),
            ("--no-fit", self.no_fit),
            ("--diagnose", self.diagnose),
        ):
            if enabled:
                argv.append(flag)
        return argv


def parse_settings_argv(settings: Settings):
    """Turn settings into a validated ``web_bot`` namespace (raises SystemExit)."""
    return build_parser().parse_args(settings.to_argv())


def stake_label(stake: int) -> str:
    for index, amount in STAKES:
        if index == stake:
            return f"{index} — {amount} coins"
    return f"0 — {STAKES[0][1]} coins"


def code_to_text(code: int) -> str:
    return {
        0: "დასრულდა წარმატებით",
        1: "შეჩერდა შეცდომით (იხ. ლოგი)",
        2: "Playwright არ არის დაყენებული",
    }.get(code, f"დასრულდა (კოდი {code})")


# ----------------------------------------------------------------------
# which browser can we actually launch?
# ----------------------------------------------------------------------
def detect_channel() -> str:
    """Pick the browser that is most likely to work on this machine."""
    if bundled_chromium_installed():
        return ""
    return installed_browser_channel() or ""


# ----------------------------------------------------------------------
# the window
# ----------------------------------------------------------------------
class BotWindow:
    """The control panel itself."""

    def __init__(self, root, settings: Optional[Settings] = None,
                 log_file: Optional[Path] = None, exit_when_done: bool = False) -> None:
        self.root = root
        first_run = not settings_path().exists()
        self.settings = settings or Settings.load()
        if first_run and not self.settings.channel:
            # no settings yet: use the browser that is actually installed here
            self.settings.channel = detect_channel()
        self.log_file = Path(log_file) if log_file else None
        self.exit_when_done = exit_when_done
        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.moves_seen = 0

        root.title("Checkers Bot — jokergame.cc")
        root.geometry("980x760")
        root.minsize(860, 640)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build()
        self._load_into_widgets()
        self.root.after(150, self.pump)

    # -- widgets -------------------------------------------------------
    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        # 1) where to start -------------------------------------------------
        start = ttk.LabelFrame(outer, text="1. საიდან დაიწყოს თამაში", padding=8)
        start.pack(fill="x", **pad)
        self.mode_var = tk.StringVar(value="lobby")
        for index, (key, label) in enumerate(MODES.items()):
            ttk.Radiobutton(start, text=label, value=key, variable=self.mode_var,
                            command=self._sync_state).grid(row=0, column=index,
                                                           sticky="w", padx=(0, 18))
        ttk.Label(start, text="„უკვე ღია დაფა“ = ლობის გავლის გარეშე, პირდაპირ თამაშზე.",
                  foreground="#555").grid(row=1, column=0, columnspan=3, sticky="w",
                                          pady=(4, 0))

        # 2) sign in --------------------------------------------------------
        auth = ttk.LabelFrame(outer, text="2. ავტორიზაცია (მხოლოდ „ავტორიზაცია → ლობი“)",
                              padding=8)
        auth.pack(fill="x", **pad)
        ttk.Label(auth, text="ელ. ფოსტა / ტელეფონი:").grid(row=0, column=0, sticky="w")
        self.email_var = tk.StringVar()
        self.email_entry = ttk.Entry(auth, textvariable=self.email_var, width=34)
        self.email_entry.grid(row=0, column=1, sticky="w", padx=(6, 18))
        ttk.Label(auth, text="პაროლი:").grid(row=0, column=2, sticky="w")
        self.password_var = tk.StringVar()
        self.password_entry = ttk.Entry(auth, textvariable=self.password_var, width=24,
                                        show="•")
        self.password_entry.grid(row=0, column=3, sticky="w", padx=(6, 0))
        ttk.Label(auth,
                  text="პაროლი არ ინახება დისკზე — მხოლოდ ამ ფანჯრის მეხსიერებაში. "
                       "ცარიელი = უკვე შესული სესია.",
                  foreground="#555").grid(row=1, column=0, columnspan=4, sticky="w",
                                          pady=(4, 0))

        # 3) table / stake --------------------------------------------------
        table = ttk.LabelFrame(outer, text="3. მაგიდა და თანხა", padding=8)
        table.pack(fill="x", **pad)
        ttk.Label(table, text="თანხა (ლობის არჩევანი):").grid(row=0, column=0, sticky="w")
        self.stake_var = tk.StringVar()
        self.stake_box = ttk.Combobox(
            table, textvariable=self.stake_var, width=24, state="readonly",
            values=[stake_label(index) for index, _ in STAKES],
        )
        self.stake_box.grid(row=0, column=1, sticky="w", padx=(6, 18))
        ttk.Label(table, text="პროფილი (ბრაუზერის სესია):").grid(row=0, column=2, sticky="w")
        self.profile_var = tk.StringVar()
        ttk.Entry(table, textvariable=self.profile_var, width=26).grid(
            row=0, column=3, sticky="w", padx=(6, 6))
        ttk.Button(table, text="…", width=3,
                   command=lambda: self._pick_dir(self.profile_var)).grid(row=0, column=4)
        ttk.Label(table, text="ბრაუზერი:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.channel_var = tk.StringVar()
        self.channel_box = ttk.Combobox(
            table, textvariable=self.channel_var, width=24, state="readonly",
            values=[CHANNELS[key] for key in CHANNELS],
        )
        self.channel_box.grid(row=1, column=1, sticky="w", padx=(6, 18), pady=(6, 0))
        ttk.Label(table, text="ლოგების საქაღალდე:").grid(row=1, column=2, sticky="w",
                                                         pady=(6, 0))
        self.logdir_var = tk.StringVar()
        ttk.Entry(table, textvariable=self.logdir_var, width=26).grid(
            row=1, column=3, sticky="w", padx=(6, 6), pady=(6, 0))
        ttk.Button(table, text="…", width=3,
                   command=lambda: self._pick_dir(self.logdir_var)).grid(row=1, column=4,
                                                                        pady=(6, 0))


        # 4) engine ---------------------------------------------------------
        engine = ttk.LabelFrame(outer, text="4. ძრავა", padding=8)
        engine.pack(fill="x", **pad)
        ttk.Label(engine, text="სიღრმე:").grid(row=0, column=0, sticky="w")
        self.depth_var = tk.StringVar()
        ttk.Spinbox(engine, from_=1, to=20, width=5, textvariable=self.depth_var).grid(
            row=0, column=1, sticky="w", padx=(6, 16))
        ttk.Label(engine, text="წამი/სვლა:").grid(row=0, column=2, sticky="w")
        self.time_var = tk.StringVar()
        ttk.Spinbox(engine, from_=0, to=60, increment=1, width=5,
                    textvariable=self.time_var).grid(row=0, column=3, sticky="w",
                                                     padx=(6, 16))
        ttk.Label(engine, text="poll (წმ):").grid(row=0, column=4, sticky="w")
        self.poll_var = tk.StringVar()
        ttk.Spinbox(engine, from_=0.2, to=5, increment=0.2, width=5,
                    textvariable=self.poll_var).grid(row=0, column=5, sticky="w",
                                                     padx=(6, 16))
        ttk.Label(engine, text="მაქს. სვლა:").grid(row=0, column=6, sticky="w")
        self.maxmoves_var = tk.StringVar()
        ttk.Spinbox(engine, from_=0, to=999, width=5, textvariable=self.maxmoves_var).grid(
            row=0, column=7, sticky="w", padx=(6, 0))

        ttk.Label(engine, text="მაქს. თამაში:").grid(row=0, column=8, sticky="w",
                                                    padx=(16, 0))
        self.maxgames_var = tk.StringVar()
        ttk.Spinbox(engine, from_=0, to=999, width=5,
                    textvariable=self.maxgames_var).grid(row=0, column=9, sticky="w",
                                                         padx=(6, 16))
        ttk.Label(engine, text="რემატჩის ლოდინი (წმ):").grid(row=0, column=10, sticky="w")
        self.rematch_var = tk.StringVar()
        ttk.Spinbox(engine, from_=2, to=120, increment=5, width=5,
                    textvariable=self.rematch_var).grid(row=0, column=11, sticky="w",
                                                        padx=(6, 0))

        ttk.Label(engine, text="ფიქრი (წმ):").grid(row=1, column=8, sticky="w",
                                                  padx=(16, 0), pady=(8, 0))
        self.think_var = tk.StringVar()
        ttk.Entry(engine, textvariable=self.think_var, width=12).grid(
            row=1, column=9, columnspan=3, sticky="w", padx=(6, 0), pady=(8, 0))

        # The board rules are fixed: the bot always plays the jokergame.cc rule
        # set (Brazilian rules + a man crowned mid-capture keeps capturing), so
        # there is nothing to choose here.
        checks = ttk.Frame(engine)
        checks.grid(row=1, column=0, columnspan=8, sticky="w", pady=(8, 0))
        self.headless_var = tk.BooleanVar()
        self.closebrowser_var = tk.BooleanVar()
        self.trace_var = tk.BooleanVar()
        self.dryrun_var = tk.BooleanVar()
        self.noprobe_var = tk.BooleanVar()
        self.lose_var = tk.BooleanVar()
        for index, (text, variable) in enumerate((
            ("უხილავი (headless)", self.headless_var),
            ("ბრაუზერის დახურვა დასრულების შემდეგ", self.closebrowser_var),
            ("დაფის ტრეისი ლოგში", self.trace_var),
            ("მხოლოდ ჩვენება (dry-run)", self.dryrun_var),
            ("probe-ის გამორთვა", self.noprobe_var),
            ("😞 მარცხი (ადამიანურად)", self.lose_var),
        )):
            # a grid, not a single row: six checkboxes do not fit the window
            ttk.Checkbutton(checks, text=text, variable=variable).grid(
                row=index // 3, column=index % 3, sticky="w", padx=(0, 18), pady=2)

        # 5) buttons --------------------------------------------------------
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", **pad)
        self.start_button = ttk.Button(buttons, text="▶ დაწყება", command=self.on_start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="■ შეჩერება", command=self.on_stop,
                                      state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        self.diagnose_button = ttk.Button(buttons, text="✔ წესების შემოწმება",
                                          command=self.on_diagnose)
        self.diagnose_button.pack(side="left", padx=(8, 0))
        self.lose_button = ttk.Button(buttons, text="😞 მარცხი", command=self.on_lose)
        self.lose_button.pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="📂 ლოგები",
                   command=lambda: self._open_path(self.logdir_var.get())).pack(
            side="left", padx=(8, 0))
        ttk.Button(buttons, text="📂 პროფილი",
                   command=lambda: self._open_path(self.profile_var.get())).pack(
            side="left", padx=(8, 0))
        ttk.Button(buttons, text="პარამეტრების შენახვა",
                   command=self.save_settings).pack(side="right")

        # 6) log ------------------------------------------------------------
        logframe = ttk.LabelFrame(outer, text="5. ლოგი", padding=6)
        logframe.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(logframe, wrap="none", height=16, font=("Consolas", 9),
                                background="#111", foreground="#e6e6e6",
                                insertbackground="#e6e6e6")
        scroll_y = ttk.Scrollbar(logframe, orient="vertical", command=self.log_text.yview)
        scroll_x = ttk.Scrollbar(logframe, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        logframe.rowconfigure(0, weight=1)
        logframe.columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value="მზადაა")
        ttk.Label(outer, textvariable=self.status_var, foreground="#0a5").pack(
            fill="x", padx=10, pady=(0, 6))


    # -- widgets <-> settings ------------------------------------------
    def _load_into_widgets(self) -> None:
        s = self.settings
        self.mode_var.set(s.mode if s.mode in MODES else "lobby")
        self.email_var.set(s.email)
        self.password_var.set(s.password)
        self.stake_var.set(stake_label(s.stake))
        self.profile_var.set(s.profile)
        self.channel_var.set(CHANNELS.get(s.channel, CHANNELS[""]))
        self.logdir_var.set(s.log_dir)
        self.depth_var.set(str(s.depth))
        self.time_var.set(str(s.time))
        self.poll_var.set(str(s.poll))
        self.maxmoves_var.set(str(s.max_moves))
        self.maxgames_var.set(str(s.max_games))
        self.rematch_var.set(str(int(s.rematch_wait)))
        self.think_var.set(s.think_delays)
        self.headless_var.set(bool(s.headless))
        self.closebrowser_var.set(bool(s.close_browser))
        self.trace_var.set(bool(s.trace_board))
        self.dryrun_var.set(bool(s.dry_run))
        self.noprobe_var.set(bool(s.no_probe))
        self._sync_state()

    def _collect(self) -> Settings:
        """Read every widget back into a :class:`Settings`."""
        s = Settings()
        s.mode = self.mode_var.get()
        s.email = self.email_var.get().strip()
        s.password = self.password_var.get()
        label = self.stake_var.get()
        s.stake = self._as_int(label.split("—")[0], 0) if "—" in label else 0
        s.profile = self.profile_var.get().strip() or ".browser-profile"
        s.log_dir = self.logdir_var.get().strip() or "logs"
        channel_label = self.channel_var.get()
        s.channel = next((key for key, text in CHANNELS.items() if text == channel_label), "")
        s.depth = max(1, self._as_int(self.depth_var.get(), 9))
        s.time = max(0.0, self._as_float(self.time_var.get(), 5.0))
        s.poll = max(0.1, self._as_float(self.poll_var.get(), 1.0))
        s.max_moves = max(0, self._as_int(self.maxmoves_var.get(), 0))
        s.max_games = max(0, self._as_int(self.maxgames_var.get(), 0))
        s.rematch_wait = max(2.0, self._as_float(self.rematch_var.get(), 30.0))
        s.think_delays = self.think_var.get().strip() or "2,3,5,7"
        s.headless = bool(self.headless_var.get())
        s.close_browser = bool(self.closebrowser_var.get())
        s.trace_board = bool(self.trace_var.get())
        s.dry_run = bool(self.dryrun_var.get())
        s.no_probe = bool(self.noprobe_var.get())
        # values with no widget stay as they were loaded
        s.no_fit = self.settings.no_fit
        s.window_size = self.settings.window_size
        s.cdp = self.settings.cdp
        s.url = self.settings.url
        s.turn_timeout = self.settings.turn_timeout
        return s

    @staticmethod
    def _as_int(text: str, default: int) -> int:
        try:
            return int(float(str(text).replace(",", ".")))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _as_float(text: str, default: float) -> float:
        try:
            return float(str(text).replace(",", "."))
        except (TypeError, ValueError):
            return default

    def _sync_state(self) -> None:
        """Enable the credential fields only for the sign-in mode."""
        state = "normal" if self.mode_var.get() == "auth_lobby" else "disabled"
        for widget in (self.email_entry, self.password_entry):
            widget.configure(state=state)

    # -- small helpers --------------------------------------------------
    def _pick_dir(self, variable) -> None:
        chosen = filedialog.askdirectory(initialdir=variable.get() or ".")
        if chosen:
            variable.set(chosen)

    def _open_path(self, path_text: str) -> None:
        target = Path(path_text or ".")
        try:
            target.mkdir(parents=True, exist_ok=True)
            if sys.platform.startswith("win"):
                os.startfile(str(target))            # type: ignore[attr-defined]
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(target)])
        except Exception as error:
            self.log_line(f"! ვერ გავხსენი {target}: {error}")

    def log_line(self, message: str) -> None:
        """Thread safe: the worker thread only enqueues."""
        self.queue.put(("log", str(message)))

    # -- log pump -------------------------------------------------------
    def pump(self) -> None:
        drained = 0
        while drained < 400:
            try:
                kind, payload = self.queue.get_nowait()
            except queue.Empty:
                break
            drained += 1
            if kind == "log":
                self._append_log(payload)
            elif kind == "done":
                self._on_worker_done(int(payload))
        self.root.after(120, self.pump)

    def _append_log(self, line: str) -> None:
        self.log_text.insert("end", line + "\n")
        if self.log_file is not None:
            try:
                self.log_file.parent.mkdir(parents=True, exist_ok=True)
                with self.log_file.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                self.log_file = None       # never let logging break the bot
        if line.strip().startswith("our move"):
            self.moves_seen += 1
            self.status_var.set(f"მუშაობს — {self.moves_seen} სვლა")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            self.log_text.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
        self.log_text.see("end")


    # -- actions --------------------------------------------------------
    def on_start(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo(APP_NAME, "ბოტი უკვე მუშაობს.")
            return
        settings = self._collect()
        if settings.mode == "auth_lobby" and not (settings.email or settings.password):
            if not messagebox.askokcancel(
                APP_NAME,
                "ავტორიზაციის ველები ცარიელია.\n"
                "გავაგრძელო იმედით, რომ ბრაუზერის პროფილში სესია უკვე შენახულია?",
            ):
                return
        self.settings = settings
        self.save_settings(quiet=True)
        self._launch(settings)

    def on_diagnose(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo(APP_NAME, "ბოტი უკვე მუშაობს.")
            return
        settings = self._collect()
        settings.diagnose = True
        self.settings = self._collect()
        self.save_settings(quiet=True)
        self._launch(settings)

    def _launch(self, settings: Settings) -> None:
        try:
            parse_settings_argv(settings)            # fail early on bad numbers
        except SystemExit as error:
            messagebox.showerror(APP_NAME, f"პარამეტრები არასწორია (კოდი {error.code}).")
            return

        self.stop_event.clear()
        self.moves_seen = 0
        self.log_text.delete("1.0", "end")
        self.log_line("=== იწყება ===")
        self.log_line("web_bot.py " + " ".join(self._masked_argv(settings)))
        self.start_button.configure(state="disabled")
        self.diagnose_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("მუშაობს…")

        self.worker = threading.Thread(target=self._worker, args=(settings,), daemon=True)
        self.worker.start()

    @staticmethod
    def _masked_argv(settings: Settings) -> List[str]:
        argv = settings.to_argv()
        return ["•••" if index and argv[index - 1] == "--password" else item
                for index, item in enumerate(argv)]

    def _worker(self, settings: Settings) -> None:
        code = 1
        try:
            args = parse_settings_argv(settings)
            args.should_stop = self.stop_event.is_set
            args.should_lose = self.lose_var.get
            code = run_bot(args, log=self.log_line)
        except SystemExit as error:                  # argparse validation
            code = int(error.code or 0)
        except Exception as error:                   # browser / site / driver problems
            self.log_line(f"! შეცდომა: {type(error).__name__}: {error}")
            code = 1
        finally:
            self.queue.put(("done", code))

    def _on_worker_done(self, code: int) -> None:
        self.start_button.configure(state="normal")
        self.diagnose_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        text = code_to_text(code)
        self.status_var.set(f"{text} — {self.moves_seen} სვლა")
        self.log_line(f"=== {text} ===")
        if self.exit_when_done:
            self.root.after(400, self.root.destroy)

    def on_stop(self) -> None:
        if self.worker is None or not self.worker.is_alive():
            return
        self.stop_event.set()
        self.log_line("… გაჩერების მოთხოვნა (მიმდინარე სვლის შემდეგ)")
        self.status_var.set("ჩერდება…")

    def on_lose(self) -> None:
        """Ask the running bot to lose this game (with a believable weak move)."""
        if self.worker is None or not self.worker.is_alive():
            messagebox.showinfo(APP_NAME, "ჯერ დააჭირე „დაწყებას“.")
            return
        self.lose_var.set(True)
        self.log_line("😞 მარცხი ჩართულია - ბოტი საშუალო დონეზე ითამაშებს")

    def save_settings(self, quiet: bool = False) -> None:
        try:
            path = self.settings.save()
        except OSError as error:
            if not quiet:
                messagebox.showerror(APP_NAME, f"ვერ შევინახე პარამეტრები: {error}")
            return
        if not quiet:
            self.log_line(f"პარამეტრები შენახულია: {path}")

    def on_close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askokcancel(APP_NAME, "ბოტი მუშაობს. დავხურო?"):
                return
            self.stop_event.set()
        self.settings = self._collect()
        self.save_settings(quiet=True)
        self.root.destroy()


# ----------------------------------------------------------------------
# entry point
# ----------------------------------------------------------------------
def run_gui(selftest: bool = False, selftest_log: Optional[str] = None,
            log_file: Optional[str] = None, autostart: bool = False,
            exit_when_done: bool = False) -> int:
    """Open the window (and, for the build test, close it again right away)."""
    if tk is None:
        print("tkinter is not available in this Python build.", file=sys.stderr)
        return 2
    root = tk.Tk()
    window = BotWindow(root, log_file=Path(log_file) if log_file else None,
                       exit_when_done=exit_when_done)
    if autostart:
        root.after(500, window.on_start)
    if selftest:
        window.log_line("selftest: window created")
        root.update_idletasks()
        if selftest_log:
            Path(selftest_log).write_text("ok\n", encoding="utf-8")
        root.after(600, root.destroy)
    root.mainloop()
    return 0


def _option(argv: List[str], name: str) -> Optional[str]:
    """Read ``--name value`` from a plain argument list."""
    if name in argv:
        index = argv.index(name)
        if index + 1 < len(argv):
            return argv[index + 1]
    return None


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    return run_gui(
        selftest="--selftest" in argv,
        selftest_log=_option(argv, "--selftest-log"),
        log_file=_option(argv, "--log-file"),
        autostart="--autostart" in argv,
        exit_when_done="--exit-when-done" in argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
