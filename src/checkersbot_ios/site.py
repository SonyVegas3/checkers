"""Drive jokergame.cc through a ``toga.WebView`` (WKWebView on iOS).

The desktop bot talks to Playwright; a phone has no Playwright, but it does have
a real WebKit engine inside ``toga.WebView``.  Everything the bot needs is
already plain JavaScript in :mod:`checkersbot.site`, so this module only has to
evaluate those scripts and dispatch clicks - exactly what
``web_bot.JokerGameSite`` does with ``page.evaluate`` / ``page.click``.

Three Playwright-only conveniences are replaced with the bot's own logic:

* no ``expect_response`` - clicks are dispatched and the bot verifies them on
  the board (``.game-square.active`` / ``window.init_game.canAction``), which it
  already does on every move;
* no screenshots - :meth:`WebViewSite.screenshot` only logs a hint, while the
  HTML dump still works;
* no sign-in automation - the user signs in once in the WebView and WebKit
  keeps the cookies, so the app never stores a password.

``evaluate`` is a **synchronous** callable supplied by the app (it marshals the
call onto the Toga event loop), so the bot stays a plain blocking loop and runs
in a worker thread: the UI never freezes while the engine is thinking.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from checkersbot.site import (
    FIT_VIEW_JS,
    LOBBY_STEP_JS,
    LOBBY_URL,
    READ_STATE_JS,
    REMATCH_JS,
    SELECTORS,
    SiteError,
    SiteState,
)

#: how often the polling helpers re-read the page
POLL_SECONDS = 0.15


def _js_string(value: str) -> str:
    """A JavaScript string literal (``json.dumps`` escapes it correctly)."""
    return json.dumps(value)


class WebViewSite:
    """The :class:`checkersbot.site.Site` backend for a ``toga.WebView``.

    ``evaluate(script)`` must run the script in the page and return its result;
    the app implements it as ``webview.evaluate_javascript`` on the event loop.
    Every script below returns a JSON string, so the result always arrives as a
    ``str`` no matter how the platform bridge converts values.
    """

    def __init__(
        self,
        evaluate: Callable[[str], Any],
        log: Callable[[str], None] = print,
        log_dir: Optional[Path] = None,
    ) -> None:
        self._evaluate = evaluate
        self.log = log
        self.log_dir = Path(log_dir) if log_dir else None
        self._warned_screenshot = False

    # -- primitives -----------------------------------------------------
    def _eval(self, script: str) -> Any:
        """Run a script that returns ``JSON.stringify(...)`` and decode it."""
        try:
            raw = self._evaluate(script)
        except SiteError:
            raise
        except Exception as error:              # a lost/detached WKWebView
            raise SiteError(str(error)) from error
        if not isinstance(raw, str):
            return raw
        text = raw.strip()
        if text in ("", "null", "undefined"):
            return None
        try:
            return json.loads(text)
        except ValueError:
            return raw

    def _call(self, function_source: str, *args: Any) -> Any:
        """Call one of the shared JS functions (they are arrow functions)."""
        call_args = ", ".join(json.dumps(arg) for arg in args)
        return self._eval(f"JSON.stringify(({function_source})({call_args}))")

    def _run(self, body: str) -> Any:
        """Run a small statement body and return its value as JSON."""
        return self._eval(f"JSON.stringify((function(){{{body}}})())")

    def _click_selector(self, selector: str, label: str) -> bool:
        clicked = self._run(
            f"var el=document.querySelector({_js_string(selector)});"
            " if(!el) return false;"
            " el.click();"
            " return true;"
        )
        if not clicked:
            self.log(f"  (the page has no {label} to click)")
            return False
        return True


    # -- reading --------------------------------------------------------
    def read_state(self) -> Optional[SiteState]:
        raw = self._call(READ_STATE_JS)
        if not raw:
            return None
        state = SiteState(raw)
        return state if state.is_ready() else None

    def wait_for_board(self, timeout_ms: int = 90000) -> bool:
        deadline = time.time() + max(0.0, timeout_ms / 1000.0)
        while time.time() < deadline:
            if self.read_state() is not None:
                return True
            time.sleep(POLL_SECONDS)
        return False

    def wait_for_active(self, timeout_ms: int = 8000) -> bool:
        deadline = time.time() + max(0.0, timeout_ms / 1000.0)
        while time.time() < deadline:
            state = self.read_state()
            if state is not None and state.active_squares():
                return True
            time.sleep(POLL_SECONDS)
        return False

    def _wait_can_click(self, timeout_ms: int = 2500) -> bool:
        """The client only accepts clicks while ``canAction`` is exactly "yes"."""
        deadline = time.time() + max(0.0, timeout_ms / 1000.0)
        while time.time() < deadline:
            ready = self._run(
                "return !window.init_game || window.init_game.canAction === 'yes';"
            )
            if ready:
                return True
            time.sleep(POLL_SECONDS)
        return False

    # -- clicking -------------------------------------------------------
    def click_stone(self, stone_id: str, wait_response: bool = True) -> bool:
        selector = f'.game-stone[stone-id="{stone_id}"]'
        return self._click_selector(selector, f"stone {stone_id}")

    def click_square(self, absolute: int, wait_response: bool = True) -> bool:
        selector = f'.game-square[item-absolute-id="{absolute}"]'
        return self._click_selector(selector, f"square {absolute}")

    def clear_highlights(self) -> None:
        self._run(
            "document.querySelectorAll('.game-stone.highlight')"
            ".forEach(function(el){ el.classList.remove('highlight'); });"
            "return true;"
        )

    # -- view -----------------------------------------------------------
    def fit_view(self, margin: int = 10) -> Optional[Dict[str, object]]:
        info = self._call(FIT_VIEW_JS, int(margin))
        return dict(info) if isinstance(info, dict) else None

    def screenshot(self, path: Path) -> None:
        if self._warned_screenshot:
            return
        self._warned_screenshot = True
        self.log("  (screenshots are not available in the iOS app - html dump only)")

    def dump_html(self, path: Path) -> None:
        html = self._eval("JSON.stringify(document.documentElement.outerHTML)")
        if not isinstance(html, str):
            return
        target = Path(path)
        if self.log_dir is not None:
            target = self.log_dir / target.name
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(html, encoding="utf-8")
            self.log(f"  html dump: {target.name}")
        except OSError as error:
            self.log(f"  (could not write the html dump: {error})")

    # -- sign in / lobby ------------------------------------------------
    def has_auth_dialog(self) -> bool:
        return bool(self._run(
            "return Boolean(document.querySelector("
            f"{_js_string(SELECTORS['auth_dialog'])}));"
        ))

    def needs_sign_in(self) -> bool:
        """Never block the bot on a login: the user signs in inside the WebView.

        Returning ``False`` here means the app keeps waiting for a board instead
        of complaining that it has no password - WebKit's cookie store already
        holds the session once the user logged in by hand.
        """
        return False

    def sign_in(self, email: str, password: str) -> bool:
        self.log("! sign in inside the app's browser - the bot never stores a password")
        return False

    def enter_game(self, coins: int = 0) -> bool:
        """Walk the lobby with the site's own dialogs (stake → random → play).

        The caller keeps calling this: the site asks one question per dialog, so
        each call advances one step until the table opens.
        """
        script = LOBBY_STEP_JS % {
            "stake": json.dumps(SELECTORS["lobby_stake"].format(coins=coins)),
            "random": json.dumps(SELECTORS["lobby_random"]),
            "play": json.dumps(SELECTORS["lobby_play"]),
        }
        result = str(self._eval(f"JSON.stringify({script})") or "none")
        if result == "disabled":
            self.log(f"! stake option {coins} is disabled (not enough coins)")
            return False
        if result == "none":
            return False
        self.log(f"lobby: {result}")
        return True

    def request_rematch(self) -> str:
        """Ask for a rematch through the site's own client API."""
        return str(self._eval(f"JSON.stringify({REMATCH_JS})") or "none")

    def open_lobby(self) -> bool:
        """Navigate the WebView back to the lobby (leaves the finished table)."""
        try:
            self._eval(
                "JSON.stringify((function(){location.href=%s;return true;})())"
                % json.dumps(LOBBY_URL)
            )
        except SiteError:
            pass                       # the navigation tears the JS context down
        return True


__all__ = ["WebViewSite", "POLL_SECONDS"]
