"""The Toga UI of the iOS app: a WebView with the bot attached to it.

The window is deliberately tiny, because a phone screen belongs to the board:

* a toolbar with **start/stop**, the engine depth and the seconds per move;
* the ``toga.WebView`` that shows jokergame.cc (log in there, open a table);
* a small log so you can see what the bot is doing.

The bot runs in a worker thread (a 3 s search must not freeze the UI) and talks
to the WebView through :meth:`CheckersBotApp.evaluate`, which marshals every
JavaScript call onto the Toga event loop and blocks the worker until it returns.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Optional

import toga
from toga.style import Pack
from toga.style.pack import COLUMN, ROW

from checkersbot.bot import CheckersBot
from checkersbot.site import SiteError

from .site import WebViewSite

DEFAULT_URL = "https://jokergame.cc/checkers"
LOG_LINES = 40


class CheckersBotApp(toga.App):
    """A checkers board in a WebView, with the engine playing it."""

    # -- construction ---------------------------------------------------
    async def startup(self) -> None:
        # the loop that owns the WebView; the worker thread posts to it
        self._loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        self._bot_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        self.start_button = toga.Button(
            "▶ დაწყება", on_press=self.on_start, style=Pack(width=118, margin=6)
        )
        self.reload_button = toga.Button(
            "↻", on_press=self.on_reload, style=Pack(width=44, margin=(6, 0, 6, 0))
        )
        self.depth_input = toga.NumberInput(style=Pack(width=58, margin=(6, 4)))
        self.time_input = toga.NumberInput(style=Pack(width=64, margin=(6, 4)))
        self.webview = toga.WebView(style=Pack(flex=1))
        self.log_view = toga.MultilineTextInput(
            style=Pack(height=92, margin=(0, 6, 6, 6))
        )
        self.status = toga.Label("მზადაა", style=Pack(flex=1, margin=(0, 6)))

        self.depth_input.value = 7
        self.time_input.value = 3.0
        self.log_view.readonly = True
        self.log_view.value = ""

        toolbar = toga.Box(
            style=Pack(direction=ROW, align_items="center"),
            children=[
                self.start_button,
                toga.Label("სიღრმე", style=Pack(margin=(6, 0, 6, 0))),
                self.depth_input,
                toga.Label("წმ/სვლა", style=Pack(margin=(6, 0, 6, 0))),
                self.time_input,
                self.reload_button,
            ],
        )
        status_bar = toga.Box(
            style=Pack(direction=ROW, align_items="center"),
            children=[self.status],
        )
        root = toga.Box(
            style=Pack(direction=COLUMN, flex=1),
            children=[toolbar, status_bar, self.webview, self.log_view],
        )

        self.main_window = toga.MainWindow(title=self.formal_name)
        self.main_window.content = root
        self.main_window.show()

        self.webview.url = DEFAULT_URL
        self.log_line("გახსენი მაგიდა ზემოთ ფანჯარაში და დააჭირე „დაწყებას“")

    # -- JavaScript bridge ----------------------------------------------
    def evaluate(self, script: str) -> Any:
        """Run JavaScript in the WebView from the worker thread and wait for it."""
        loop = self._loop
        if loop is None or loop.is_closed():
            raise SiteError("the app has not started yet")
        future = asyncio.run_coroutine_threadsafe(self._evaluate(script), loop)
        return future.result(timeout=20)

    async def _evaluate(self, script: str) -> Any:
        try:
            return await self.webview.evaluate_javascript(script)
        except Exception as error:            # a navigation in flight, a dead view
            raise SiteError(str(error)) from error

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
        current = self.log_view.value or ""
        lines = (current + text + "\n").splitlines()[-LOG_LINES:]
        self.log_view.value = "\n".join(lines) + "\n"
        self.status.text = text[:120]

    # -- start / stop ---------------------------------------------------
    @staticmethod
    def _number(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def on_start(self, widget, **kwargs) -> None:
        if self._bot_thread is not None and self._bot_thread.is_alive():
            self._stop.set()
            self.status.text = "ჩერდება..."
            self.start_button.text = "… ჩერდება"
            return

        depth = int(max(1, self._number(self.depth_input.value, 7.0)))
        seconds = self._number(self.time_input.value, 3.0)
        self._stop = threading.Event()
        self.log_view.value = ""
        self.start_button.text = "■ გაჩერება"
        self.status.text = "ვეძებ მაგიდას..."

        site = WebViewSite(self.evaluate, log=self.log_line, log_dir=self.paths.logs)
        bot = CheckersBot(
            site,
            depth=depth,
            time_limit=seconds if seconds > 0 else None,
            poll=1.0,
            # the phone layout already fits; scaling it would only shrink the board
            apply_fit=False,
            # the user opens the table; the app never walks the lobby or logs in
            lobby_walk=False,
            use_probe=True,
            rules="brazilian",
            log=self.log_line,
            should_stop=self._stop.is_set,
            log_dir=str(self.paths.logs),
        )
        self._bot_thread = threading.Thread(
            target=self._run_bot, args=(bot,), name="checkersbot", daemon=True
        )
        self._bot_thread.start()

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
        self.start_button.text = "▶ დაწყება"
        self.status.text = "დასრულდა" if code == 0 else f"შეჩერდა ({code})"

    async def on_reload(self, widget, **kwargs) -> None:
        try:
            await self.webview.load_url(self.webview.url or DEFAULT_URL)
        except Exception as error:
            self.log_line(f"! {error}")


def main() -> CheckersBotApp:
    """Briefcase/Toga entry point."""
    return CheckersBotApp("CheckersBot", "cc.jokergame.checkersbot")


if __name__ == "__main__":
    main().main_loop()
