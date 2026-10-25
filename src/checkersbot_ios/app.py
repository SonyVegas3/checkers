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
DEPTH = 9            # engine search depth (the panel's default)
SECONDS = 5.0        # seconds per move
THINK_DELAYS = (2.0, 3.0, 5.0, 7.0)   # human-like pauses (the first move is quick)
QUICK_DELAYS = (1.0, 2.0, 3.0)        # used when the move was obvious (e.g. a capture)
STAKE = 0            # lobby stake: 0 = 50 coins (the cheapest table)


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
        self._stop = threading.Event()
        self._lose_flag = False

    # -- window content -------------------------------------------------
    def build_content(self, app, **kwargs) -> Any:
        """Called by Toga before the main window is shown; returns the content."""
        self._capture_loop()
        try:
            return self._build_ui()
        except Exception:
            return self._boot_error(traceback.format_exc())

    def _build_ui(self) -> Any:
        self.start_button = toga.Button(
            "▶ დაწყება", on_press=self.on_start, style=Pack(width=130, margin=6)
        )
        self.reload_button = toga.Button(
            "↻ განახლება", on_press=self.on_reload, style=Pack(width=120, margin=6)
        )
        self.lose_button = toga.Button(
            "😞 მარცხი", on_press=self.on_lose, style=Pack(width=100, margin=6)
        )
        self.status = toga.Label(
            "CheckersBot 2.0 — იტვირთება...", style=Pack(margin=(6, 6, 0, 6))
        )
        self.webview = toga.WebView(style=Pack(flex=1), url=DEFAULT_URL)
        self.webview.on_webview_load = self.on_webview_load
        self.log_view = toga.MultilineTextInput(
            style=Pack(height=90, margin=6), readonly=True
        )

        buttons = toga.Box(
            style=Pack(direction=ROW),
            children=[self.start_button, self.lose_button, self.reload_button],
        )
        root = toga.Box(
            style=Pack(direction=COLUMN, flex=1, background_color="#101418"),
            children=[self.status, buttons, self.webview, self.log_view],
        )
        self.log_line("აპი გაშვებულია — გახსენი მაგიდა და დააჭირე „დაწყებას“")
        return root

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
        view = getattr(self, "log_view", None)
        status = getattr(self, "status", None)
        if view is not None:
            current = view.value or ""
            lines = (current + text + "\n").splitlines()[-LOG_LINES:]
            view.value = "\n".join(lines) + "\n"
        if status is not None:
            status.text = text[:120]

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

    def on_start(self, widget, **kwargs) -> None:
        self._capture_loop()
        if self._bot_thread is not None and self._bot_thread.is_alive():
            self._stop.set()
            self.status.text = "ჩერდება..."
            self.start_button.text = "… ჩერდება"
            return

        self._stop = threading.Event()
        self._lose_flag = False
        self.log_view.value = ""
        self.start_button.text = "■ გაჩერება"
        self.status.text = "ვეძებ მაგიდას..."

        site = WebViewSite(self.evaluate, log=self.log_line, log_dir=self.paths.logs)
        bot = CheckersBot(
            site,
            depth=DEPTH,
            time_limit=SECONDS,
            poll=1.0,
            # fail fast when no table is open (the panel's 120 s is pointless here)
            turn_timeout=25.0,
            think_delays=THINK_DELAYS,
            quick_delays=QUICK_DELAYS,
            # the phone layout already fits; scaling it would only shrink the board
            apply_fit=False,
            # the app plays until it is stopped: rematch, then a new opponent
            lobby_walk=True,
            coins=STAKE,
            use_probe=True,
            rules="joker",
            log=self.log_line,
            should_stop=self._stop.is_set,
            should_lose=lambda: self._lose_flag,
            log_dir=str(self.paths.logs),
        )
        self._bot_thread = threading.Thread(
            target=self._run_bot, args=(bot,), name="checkersbot", daemon=True
        )
        self._set_screen_awake(True)
        self._bot_thread.start()
        self.log_line("ბოტი მუშაობს — თამაშობს უწყვეტად (რემატჩი ან ახალი ოპონენტი)")
        self.status.text = "ვეძებ მაგიდას..."

    def _run_bot(self, bot: CheckersBot) -> None:
        code = 1
        try:
            code = bot.run()
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
        self.start_button.text = "▶ დაწყება"
        self.status.text = "დასრულდა" if code == 0 else f"შეჩერდა ({code})"

    def on_lose(self, widget, **kwargs) -> None:
        """Ask the bot to lose this game - with average-player moves."""
        if not (self._bot_thread is not None and self._bot_thread.is_alive()):
            self.log_line("ჯერ დააჭირე „დაწყებას“")
            return
        self._lose_flag = True
        self.log_line("😞 მარცხი ჩართულია - ბოტი საშუალო დონეზე ითამაშებს")

    async def on_reload(self, widget, **kwargs) -> None:
        try:
            await self.webview.load_url(self.webview.url or DEFAULT_URL)
        except Exception as error:
            self.log_line(f"! {error}")


def main() -> CheckersBotApp:
    """Briefcase/Toga entry point."""
    return CheckersBotApp()


if __name__ == "__main__":
    main().main_loop()

