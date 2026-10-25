#!/usr/bin/env python3
"""Play checkers on jokergame.cc with the Minimax engine of this project.

How the bot talks to the site
-----------------------------
jokergame.cc renders the board as plain DOM (see ``ui/js/games/checkers``):

=======================  =====================================================
``.game-table``          board container
``.game-square``         one element per square, ``item-x`` / ``item-y`` are
                         1-based coordinates, ``item-color="black"`` marks the
                         playable squares, ``item-absolute-id`` is the site's
                         own square id
``.game-stone``          one element per piece with ``stone-id``,
                         ``stone-absolute`` (square id), ``stone-color``,
                         ``stone-king`` and ``stone-clickable``
``.game-square.active``  destination squares offered by the server
``.game-stone.highlight`` pieces the server asks us to move (forced jump)
=======================  =====================================================

So one move is two clicks: click our stone, then click the highlighted
destination.  For chained jumps the stone stays selected and every further
landing square is clicked in order.

The site is always the referee: before playing, the bot clicks its pieces and
reads the squares the server offers (``.game-square.active``), which also tells
it whether the board is drawn upside down and how mandatory captures are
enforced.  Only moves the site offers are ever played - see ``site_legal_moves``.

The board, the turn and the shot clock are taken from the site's own game object
(``window.init_game``: ``canAction``, ``game_data.player.action``,
``game_data.stones.list``, ``game_data.info.step``/``step_timeout``), with the
DOM as a fallback, so the bot knows exactly when it is its turn.

This file is only the *Playwright* backend plus the CLI: the board rules, the
search, the JavaScript and the bot loop live in ``checkersbot/`` and are shared
with the iOS app (``src/checkersbot_ios``), which drives the same scripts through
``toga.WebView`` instead of a Chromium.

Usage
-----
1. install the browser once::

       python -m pip install -r requirements.txt
       python -m playwright install chromium

2. open a checkers table and log in (``--profile`` keeps the cookies)::

       python web_bot.py --profile .browser-profile

   ...or attach to a Chrome that is already running::

       chrome.exe --remote-debugging-port=9222
       python web_bot.py --cdp http://localhost:9222

3. dry run first - reads the board, prints the move, never clicks::

       python web_bot.py --dry-run --verbose

4. let it play::

       python web_bot.py --site-color white --side red --time 3
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Sequence

from checkersbot.board import RED, square_name
from checkersbot.bot import CheckersBot
from checkersbot.site import (  # noqa: F401 - re-exported for the tests / CLI
    ACCEPT_COIN_OFFER_JS,
    CANCEL_SEARCH_JS,
    DECLINE_CHAT_JS,
    FIT_VIEW_JS,
    LOBBY_URL,
    READ_STATE_JS,
    REMATCH_JS,
    SEARCH_STATUS_JS,
    SELECTORS,
    SiteState,
    first_landing,
    move_landing_path,
    site_legal_moves,
    square_from_xy,
    xy_from_square,
)

try:                                        # optional dependency, --help still works
    from playwright.sync_api import sync_playwright
    from playwright.sync_api import Error as PlaywrightError
except Exception:                            # pragma: no cover - environment dependent
    sync_playwright = None

    class PlaywrightError(Exception):
        """Placeholder when Playwright is not installed."""


DEFAULT_URL = "https://jokergame.cc/checkers"


def chromium_browsers_dir() -> Path:
    """Where Playwright keeps its downloaded browsers on this machine."""
    custom = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if custom and custom != "0":
        return Path(custom)
    if os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def bundled_chromium_installed() -> bool:
    """True when Playwright's own Chromium is downloaded (``playwright install``)."""
    root = chromium_browsers_dir()
    if not root.is_dir():
        return False
    for folder in root.glob("chromium*"):
        # the layout differs between versions: chrome-win, chrome-win64, ...
        if any(folder.glob("**/chrome.exe")) or any(folder.glob("**/chrome")):
            return True
    return False


def installed_browser_channel() -> Optional[str]:
    """An installed Chrome/Edge that Playwright can drive (``channel=``), if any."""
    for channel in ("chrome", "msedge"):
        if _installed_browser_path(channel) is not None:
            return channel
    return None


def _installed_browser_path(channel: str) -> Optional[Path]:
    """The executable of an installed ``chrome``/``msedge``."""
    if channel == "chrome":
        paths = [
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Google/Chrome/Application/chrome.exe",
        ]
    else:
        paths = [
            Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
            / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
            / "Microsoft/Edge/Application/msedge.exe",
        ]
    for path in paths:
        if path.exists():
            return path
    return None


def browser_executable(playwright, channel: Optional[str]) -> Optional[Path]:
    """Path of a Chromium/Chrome/Edge binary we can start as our own process."""
    if channel:
        found = _installed_browser_path(channel)
        if found is not None:
            return found
    try:
        path = Path(playwright.chromium.executable_path)
    except Exception:
        return None
    return path if path.exists() else None


# ----------------------------------------------------------------------
# a browser that outlives this process (the finished game stays on screen)
# ----------------------------------------------------------------------
def browser_port(profile: Path) -> int:
    """A stable CDP port per profile, so a later run reconnects to the same window."""
    digest = hashlib.sha1(str(profile).encode("utf-8")).hexdigest()[:6]
    return 9300 + int(digest, 16) % 200


def _cdp_alive(endpoint: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(endpoint + "/json/version", timeout=timeout) as answer:
            return 200 <= int(answer.status) < 300
    except Exception:
        return False


def _wait_for_cdp(endpoint: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _cdp_alive(endpoint):
            return True
        time.sleep(0.25)
    return False


def start_detached_browser(playwright, *, profile: Path, channel: Optional[str],
                           headless: bool, window_size: Optional[str],
                           log=print) -> Optional[str]:
    """Launch the browser as an independent process and return its CDP endpoint.

    Nothing ties the browser to this Python process any more, so when the bot is
    done the window stays exactly where it is - the finished game with its
    "Rematch" button, or the lobby.  If that profile is already running (e.g. a
    previous run), its debugging port is reused instead of opening a second copy.
    """
    executable = browser_executable(playwright, channel)
    if executable is None:
        log("! no browser binary found for a detached window")
        return None

    port = browser_port(profile)
    endpoint = f"http://127.0.0.1:{port}"
    if _cdp_alive(endpoint):
        log(f"browser already running - reusing it ({endpoint})")
        return endpoint

    arguments = [
        str(executable),
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
    ]
    if headless:
        arguments += ["--headless=new", "--window-size=1280,900"]
    elif window_size:
        arguments.append(f"--window-size={window_size.lower().replace('x', ',')}")
    log("opening the browser as its own process (it stays open after the bot stops)")
    try:
        subprocess.Popen(
            arguments,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0),
        )
    except OSError as error:
        log(f"! could not start the browser: {error}")
        return None

    if _wait_for_cdp(endpoint, 25.0):
        return endpoint
    log("! the browser did not open its debugging port "
        "(is this profile already open in another window?)")
    return None


# --- the Playwright backend ----------------------------------------------------


class JokerGameSite:
    """Reads and clicks the jokergame.cc checkers board through Playwright."""

    def __init__(self, page, log=print) -> None:
        self.page = page
        self.log = log
        # The site answers every click with an AJAX request (check_stone /
        # move_stone) and only then repaints the board.  ``ajax`` is ``None``
        # until the first click tells us whether this page does that at all
        # (the local mock board updates synchronously, the real site does not).
        self.ajax: Optional[bool] = None

    # -- reading ---------------------------------------------------------
    def read_state(self) -> Optional[SiteState]:
        try:
            raw = self.page.evaluate(READ_STATE_JS)
        except PlaywrightError as error:
            self.log(f"! could not read the board: {error}")
            return None
        if not raw:
            return None
        state = SiteState(raw)
        return state if state.is_ready() else None

    def wait_for_board(self, timeout_ms: int = 90000) -> bool:
        try:
            self.page.wait_for_function(
                "() => document.querySelectorAll('.game-square[item-absolute-id]').length >= 32",
                timeout=timeout_ms,
            )
            return True
        except PlaywrightError:
            return False

    # -- signing in ------------------------------------------------------
    def needs_sign_in(self) -> bool:
        """True when the page *shows* a "Sign in" button (we are logged out).

        The site keeps the login container in the DOM after signing in and only
        hides it, so a plain selector check is not enough.
        """
        try:
            return self.page.is_visible(SELECTORS["sign_in_button"])
        except PlaywrightError:
            return False

    def has_auth_dialog(self) -> bool:
        """True while the sign-in dialog is on screen."""
        try:
            return self.page.is_visible(SELECTORS["auth_dialog"])
        except PlaywrightError:
            return False

    def sign_in(self, email: str, password: str) -> bool:
        """Log in through the site's own "Sign in" dialog.

        Credentials are only used here and are never logged or written to disk.
        """
        try:
            button = self.page.query_selector(SELECTORS["sign_in_button"])
        except PlaywrightError:
            button = None
        if button is None:
            return False

        self.log("signing in to jokergame.cc...")
        try:
            button.click(timeout=4000)
        except PlaywrightError:
            try:
                self.page.eval_on_selector(SELECTORS["sign_in_button"], "el => el.click()")
            except PlaywrightError as error:
                self.log(f"! could not open the sign-in dialog: {error}")
                return False

        try:
            self.page.wait_for_selector(SELECTORS["auth_email"], timeout=10000)
            self.page.fill(SELECTORS["auth_email"], email)
            self.page.fill(SELECTORS["auth_password"], password)
            clicked = False
            for candidate in self.page.query_selector_all(
                f"{SELECTORS['auth_dialog']} .big-dialog-button"
            ):
                text = (candidate.inner_text() or "").strip().lower()
                if text == "sign in":
                    candidate.click(timeout=4000)
                    clicked = True
                    break
            if not clicked:
                self.log("! could not find the 'Sign in' button in the dialog")
                return False
            self.page.wait_for_timeout(2500)
        except PlaywrightError as error:
            self.log(f"! signing in failed: {error}")
            return False

        try:
            if self.page.is_visible(SELECTORS["auth_dialog"]):
                self.log("! the sign-in dialog is still open (check the credentials)")
                try:                                # close it so the lobby is reachable
                    for candidate in self.page.query_selector_all(
                        f"{SELECTORS['auth_dialog']} .big-dialog-button"
                    ):
                        if (candidate.inner_text() or "").strip().lower() == "close":
                            candidate.click(timeout=3000)
                            break
                except PlaywrightError:
                    pass
                return False
        except PlaywrightError:
            pass
        self.log("signed in")
        return True

    def _wait_selector(self, selector: str, timeout_ms: int = 6000) -> bool:
        try:
            self.page.wait_for_selector(selector, timeout=timeout_ms, state="attached")
            return True
        except PlaywrightError:
            return False

    def enter_game(self, coins: int = 0) -> bool:
        """Walk the lobby: "Play a game" → "Random people" → stake.

        The site asks three things in a row, each in its own dialog, so the caller
        keeps calling this until the board shows up.  ``coins`` is the stake
        index (0 = 50 coins, 1 = 5 000 … 5 = 10 000 000).  Returns True when a
        click was sent.
        """
        stake_selector = SELECTORS["lobby_stake"].format(coins=coins)
        try:
            # 1) the stake dialog is the last step - handle it first
            if self.page.is_visible(".v2-coins-dialog"):
                element = self.page.query_selector(stake_selector)
                if element is None:
                    self.log(f"! stake option {coins} is not offered for this account")
                    return False
                if element.get_attribute("item-disabled") == "yes":
                    self.log(f"! stake option {coins} is disabled (not enough coins)")
                    return False
                clicked = self._click_element(element, stake_selector, f"stake option {coins}")
                if clicked:
                    self._wait_hidden(".v2-coins-dialog", 8000)
                return clicked

            # 2) "Who do you want to play with?"
            if self.page.is_visible(".play-choose-dialog"):
                if not self._click_selector(SELECTORS["lobby_random"], "'Random people'"):
                    return False
                self._wait_selector(".v2-coins-dialog", 8000)
                return True

            # 3) the lobby itself
            if self.page.is_visible(SELECTORS["lobby_play"]):
                if not self._click_selector(SELECTORS["lobby_play"], "'Play a game'"):
                    return False
                self._wait_selector(".play-choose-dialog", 8000)
                return True
        except PlaywrightError as error:
            self.log(f"! lobby step failed: {error}")
        return False

    def request_rematch(self) -> str:
        """Ask for a rematch exactly like the site's own finish-dialog button."""
        try:
            return str(self.page.evaluate(REMATCH_JS) or "none")
        except PlaywrightError as error:
            self.log(f"  (rematch request failed: {error})")
            return "none"

    def open_lobby(self) -> bool:
        """Leave the finished table and go back to the checkers lobby page."""
        try:
            self.page.goto(LOBBY_URL, wait_until="domcontentloaded", timeout=45000)
            return True
        except PlaywrightError as error:
            self.log(f"! could not open the lobby: {error}")
            return False

    def accept_coin_offer(self) -> str:
        """Press the site's own accept button in the bigger-stake dialog."""
        try:
            return str(self.page.evaluate(ACCEPT_COIN_OFFER_JS) or "none")
        except PlaywrightError as error:
            self.log(f"  (stake offer: {error})")
            return "none"

    def decline_chat_offer(self) -> str:
        """Close the chat invitation (the site itself only hides the dialog)."""
        try:
            return str(self.page.evaluate(DECLINE_CHAT_JS) or "none")
        except PlaywrightError as error:
            self.log(f"  (chat offer: {error})")
            return "none"

    def search_status(self) -> str:
        """What the site's "searching for an opponent" dialog is doing."""
        try:
            return str(self.page.evaluate(SEARCH_STATUS_JS) or "none")
        except PlaywrightError:
            return "none"

    def cancel_search(self) -> str:
        """Press the search dialog's Cancel button."""
        try:
            return str(self.page.evaluate(CANCEL_SEARCH_JS) or "none")
        except PlaywrightError as error:
            self.log(f"  (cancel search: {error})")
            return "none"

    def _wait_hidden(self, selector: str, timeout_ms: int = 6000) -> bool:
        try:
            self.page.wait_for_selector(selector, timeout=timeout_ms, state="hidden")
            return True
        except PlaywrightError:
            return False

    def _click_selector(self, selector: str, label: str) -> bool:
        try:
            element = self.page.query_selector(selector)
        except PlaywrightError:
            return False
        if element is None:
            return False
        return self._click_element(element, selector, label)

    def _click_element(self, element, selector: str, label: str) -> bool:
        self.log(f"lobby: clicking {label}")
        try:
            element.click(timeout=3000)
        except PlaywrightError:
            try:                       # covered by another dialog
                self.page.eval_on_selector(selector, "el => el.click()")
            except PlaywrightError as error:
                self.log(f"! could not click {label}: {error}")
                return False
        self.page.wait_for_timeout(1200)
        return True

    # -- keeping the board inside the window ------------------------------
    def fit_view(self, margin: int = 10) -> Optional[Dict[str, object]]:
        """Nudge the info panels / scale the board so nothing is cut off.

        The site's own layout is a bit taller than the window (the player panel
        always hangs ~26 px below it and the opponent panel 8 px above), so the
        bot corrects that visually - only CSS transforms are touched, the game
        state and the click targets stay where the site put them.
        """
        try:
            return self.page.evaluate(FIT_VIEW_JS, margin)
        except PlaywrightError as error:
            self.log(f"(could not fit the view: {error})")
            return None

    def wait_for_actives(self, timeout_ms: int = 8000) -> bool:
        """Wait until the site marks at least one destination square."""
        try:
            self.page.wait_for_selector(SELECTORS["active_square"], timeout=timeout_ms,
                                        state="attached")
            return True
        except PlaywrightError:
            return False

    # -- clicking --------------------------------------------------------
    def click_stone(self, stone_id: str, wait_response: bool = True) -> bool:
        """Click one of our stones; returns True when the site answered (AJAX)."""
        selector = f'.game-stone[stone-id="{stone_id}"]'
        return self._click(selector, "act=check_stone" if wait_response else None)

    def click_square(self, absolute: int, wait_response: bool = True) -> bool:
        """Click a destination square; returns True when the site answered."""
        selector = f'.game-square[item-absolute-id="{absolute}"]'
        return self._click(selector, "act=move_stone" if wait_response else None)

    def clear_highlights(self) -> None:
        """Drop the site's "these pieces may move" hint so we can read it reliably.

        Only the cosmetic ``.highlight`` class is touched - the game state is
        unaffected, and the client re-adds the hint whenever it needs to.
        """
        try:
            self.page.eval_on_selector_all(
                ".game-stone.highlight",
                "els => els.forEach((el) => el.classList.remove('highlight'))",
            )
        except PlaywrightError:
            pass

    def _uses_ajax(self) -> bool:
        """Does this page answer clicks with AJAX?  Asked once, then cached."""
        if self.ajax is None:
            try:
                self.ajax = bool(
                    self.page.evaluate("() => !!(window.jQuery && window.jQuery.ajax)")
                )
            except PlaywrightError:
                self.ajax = False
        return bool(self.ajax)

    def _wait_can_click(self, timeout_ms: int = 4000) -> bool:
        """Wait until the client accepts clicks (``canAction === "yes"``)."""
        try:
            self.page.wait_for_function(
                "() => !window.init_game || window.init_game.canAction === 'yes'",
                timeout=timeout_ms,
            )
            return True
        except PlaywrightError:
            return False

    def _click(self, selector: str, expect: Optional[str] = None) -> bool:
        """Click an element and wait for the site's AJAX answer when it uses one.

        The client switches ``canAction`` to ``"none"`` while a request is in
        flight and only restores it with the next server answer (or its 1 s
        polling tick), so a click can be swallowed without any request.  We wait
        for ``canAction`` and retry once after a short pause before giving up.
        """
        if expect and self._uses_ajax():
            for attempt in (1, 2, 3):
                if not self._wait_can_click():
                    # the site is busy or it is not our turn: clicking now would only
                    # raise a toast, so leave the board alone and let the caller retry
                    self.log("  (the site is not ready for clicks yet - not clicking)")
                    return False
                try:
                    with self.page.expect_response(
                        lambda response: expect in response.url, timeout=3500
                    ):
                        self._raw_click(selector)
                    self.page.wait_for_timeout(60)     # let jQuery paint the result
                    return True
                except PlaywrightError as error:
                    if attempt < 3:
                        self.page.wait_for_timeout(1000)   # wait for the next poll
                        continue
                    self.log("  (the site ignored the click - "
                             f"{str(error).splitlines()[0][:60]})")
                    return False
        self._raw_click(selector)
        self.page.wait_for_timeout(40)
        return False

    def _raw_click(self, selector: str, timeout_ms: int = 2500) -> None:
        """Normal mouse click, with fallbacks for stones sliding or banners on top."""
        try:
            self.page.click(selector, timeout=timeout_ms)
            return
        except PlaywrightError as error:
            message = str(error).splitlines()[0][:80]
        # stones slide around for 500 ms and may cover the square mid-animation, and a
        # toast/banner can sit over the board - retry at the centre, then dispatch the
        # DOM click as the last resort
        self.log(f"  (click intercepted - {self._covering_element(selector)}; "
                 f"{message})")
        box = None
        try:
            element = self.page.query_selector(selector)
            box = element.bounding_box() if element is not None else None
        except PlaywrightError:
            box = None
        if box:
            try:
                self.page.mouse.click(box["x"] + box["width"] / 2,
                                      box["y"] + box["height"] / 2)
                return
            except PlaywrightError:
                pass
        try:
            self.page.eval_on_selector(selector, "el => el.click()")
        except PlaywrightError:
            pass

    def _covering_element(self, selector: str) -> str:
        """Describe what sits on top of ``selector`` (for the log)."""
        script = """(sel) => {
          const el = document.querySelector(sel);
          if (!el) return 'a missing element';
          const r = el.getBoundingClientRect();
          const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
          if (!top) return 'nothing';
          const cls = (top.className || '').toString().trim().split(/\\s+/).filter(Boolean);
          return top.tagName.toLowerCase() + (cls.length ? '.' + cls.join('.') : '');
        }"""
        try:
            return self.page.evaluate(script, selector)
        except PlaywrightError:
            return "an unknown element"

    # -- debugging -------------------------------------------------------
    def screenshot(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(path), full_page=True)
        except PlaywrightError as error:
            self.log(f"! screenshot failed: {error}")

    def dump_html(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.page.content(), encoding="utf-8")
        except PlaywrightError as error:
            self.log(f"! html dump failed: {error}")


# ----------------------------------------------------------------------
# browser / CLI plumbing
# ----------------------------------------------------------------------
def _pick_page(context, url: str, log=print):
    """Use the page that already shows the game, or open the game page."""
    target = url.rstrip("/")
    pages = list(context.pages)
    for page in pages:
        current = (page.url or "").rstrip("/")
        if "jokergame" in current or current.startswith(target):
            return page

    page = pages[0] if pages else context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
    except PlaywrightError as error:
        log(f"! could not open {url}: {error}")
    return page


def _fit_window(context, page, log=print, size: Optional[str] = None) -> None:
    """Resize the browser window so the whole page fits the physical screen.

    The site's layout is centred and slightly taller than its own window, and on
    small laptop screens the default window is bigger than the usable area - the
    bottom of the board then sits outside the screen.  Resizing the window via
    CDP keeps everything visible without touching the game itself.
    """
    custom: Optional[Dict[str, int]] = None
    if size:
        try:
            width_text, height_text = size.lower().split("x")
            custom = {"width": int(width_text), "height": int(height_text)}
        except ValueError:
            log(f"! --window-size must look like 1200x800, got {size!r}")
    try:
        metrics = page.evaluate(
            "() => ({aw: screen.availWidth, ah: screen.availHeight,"
            " ow: window.outerWidth, oh: window.outerHeight})"
        )
    except PlaywrightError as error:
        log(f"(could not read the screen size: {error})")
        metrics = {"aw": 1280, "ah": 900, "ow": 0, "oh": 0}

    if custom:
        width, height = custom["width"], custom["height"]
    else:
        width = int(max(880, min(int(metrics["aw"]) - 24, 1500)))
        height = int(max(560, min(int(metrics["ah"]) - 70, 1000)))
    if int(metrics["ow"]) <= width + 2 and int(metrics["oh"]) <= height + 2:
        return
    try:
        session = context.new_cdp_session(page)
        window = session.send("Browser.getWindowForTarget")
        session.send(
            "Browser.setWindowBounds",
            {
                "windowId": window["windowId"],
                "bounds": {
                    "left": 0,
                    "top": 0,
                    "width": width,
                    "height": height,
                    "windowState": "normal",
                },
            },
        )
        page.wait_for_timeout(600)
        log(f"window fitted to the screen: {width}x{height}")
    except PlaywrightError as error:
        log(f"(could not resize the window: {error})")


def _diagnose_rules(site: "JokerGameSite", args) -> int:
    """Compare the site's legal moves with our engine's rules, stone by stone.

    Prints, for every one of our pieces, the squares the server offers and the
    squares our rules predict.  Any difference means the site plays by different
    rules (e.g. flying kings) - exactly what this mode is for.
    """
    bot = CheckersBot(
        site,
        site_color=args.site_color,
        side=args.side,
        depth=args.depth,
        time_limit=args.time or None,
        dry_run=True,
        log_dir=args.log_dir,
        coins=args.coins,
        trace_board=args.trace_board,
    )
    if not bot._open_board():
        print("! no board")
        return 1

    deadline = time.time() + max(30.0, args.turn_timeout)
    state = None
    while time.time() < deadline:
        state = site.read_state()
        if state is not None and state.my_turn and state.can_click:
            break
        time.sleep(1)
    if state is None or not state.my_turn:
        print("! it is not our turn - run this again when the site says it is")
        return 1

    if not bot._resolve_site_color(state):
        print("! could not tell which stones are ours")
        return 1
    probe = bot.probe_moves(state)
    state = site.read_state() or state
    if not bot._calibrate(state, probe):
        print("! could not calibrate our side")
        return 1

    board = bot.build_board(state)
    moves = board.generate_moves()
    print(f"our stones: {bot.my_site_color}, engine side: "
          f"{'RED' if bot.my_engine_color == RED else 'BLACK'}")
    print(board)
    print("stone  kind  site offers                    engine offers")
    problems = 0
    origins = sorted(set(probe) | {move.from_square for move in moves})
    for origin in origins:
        stone = state.stone_at(origin)
        king = bool(stone and stone.get("king"))
        site_offers = sorted(probe.get(origin, []))
        engine_offers = sorted(bot._first_landings(moves, origin))
        same = set(site_offers) == set(engine_offers)
        problems += 0 if same else 1
        print(f"{square_name(origin):5s}  {'king' if king else 'man ':4s}  "
              f"{bot._names(site_offers):28s}  {bot._names(engine_offers)}"
              f"{'' if same else '   <-- differs'}")
    print(f"\n{problems} stone(s) differ")
    return 0


def _discover(site: "JokerGameSite", page, args) -> int:
    """Print everything the bot can see - use it to verify the selectors."""
    if not site.wait_for_board(timeout_ms=6000):
        email = args.email or os.environ.get("JOKER_EMAIL")
        password = args.password or os.environ.get("JOKER_PASSWORD")
        if site.needs_sign_in() or site.has_auth_dialog():
            if email and password:
                site.sign_in(email, password)
            else:
                print("! the site asks to sign in - pass --email/--password "
                      "or set JOKER_EMAIL / JOKER_PASSWORD")
        for _ in range(6):
            if not site.enter_game(args.coins):
                break
            print("lobby detected - walking the lobby (Play a game -> Random people -> stake)")
            if site.wait_for_board(timeout_ms=25000):
                break
    if not site.wait_for_board(timeout_ms=int(max(5.0, args.turn_timeout) * 1000)):
        print("! no checkers board found - open a table and run this again")
        site.screenshot(Path(args.log_dir) / "discover.png")
        return 1

    state = site.read_state()
    if state is None:
        print("! the board exists but could not be parsed")
        site.dump_html(Path(args.log_dir) / "discover.html")
        return 1

    print(f"url         : {page.url}")
    print(f"board       : {state.describe()}")
    print(f"server      : canAction={state.server.get('canAction')} "
          f"playerAction={state.player_action} playerColor={state.player_color} "
          f"status={state.server.get('status')}")
    print(f"clickable   : {', '.join(state.clickable_colors()) or '-'}")
    print(f"highlighted : {', '.join(state.highlighted_colors()) or '-'}")
    print("active      : " + (", ".join(square_name(s) for s in state.active_squares()) or "-"))
    print()
    print("stones (engine coordinates):")
    for index in sorted(state.stone_by_square):
        stone = state.stone_by_square[index]
        print(
            f"  {square_name(index):4s} id={stone['id']:>4} color={stone['color']:<5s} "
            f"king={'yes' if stone['king'] else 'no':3s} "
            f"clickable={'yes' if stone['clickable'] else 'no'}"
        )

    probe_bot = CheckersBot(
        site,
        site_color=args.site_color,
        side=args.side,
        depth=args.depth,
        time_limit=args.time or None,
        dry_run=True,
        log_dir=args.log_dir,
        coins=args.coins,
    )
    fit = site.fit_view()
    if fit:
        print(f"\nview        : {fit.get('vw')}x{fit.get('vh')} window, board "
              f"{fit.get('board')}px, board+panels {fit.get('needed')}px -> "
              f"{'fits' if fit.get('fits') else 'overflowing'}")
    if probe_bot._resolve_site_color(state):
        offers = probe_bot.probe_moves(state) if not args.discover_only_board else {}
        print()
        print("site offers (clicked the stones, read .game-square.active):")
        if offers:
            for origin, destinations in sorted(offers.items()):
                print(f"  {square_name(origin):4s} -> {probe_bot._names(destinations)}")
        else:
            print("  -")
        if offers and probe_bot._calibrate(state, offers):
            print()
            print(probe_bot.build_board(state))
            print(f"\nboard parsed from the DOM; our side in the engine: "
                  f"{'RED' if probe_bot.my_engine_color == RED else 'BLACK'}")

    site.screenshot(Path(args.log_dir) / "discover.png")
    site.dump_html(Path(args.log_dir) / "discover.html")
    print(f"\nscreenshot and html written to {args.log_dir}/discover.*")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Play checkers on jokergame.cc with the Minimax engine.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="checkers page to open")
    parser.add_argument("--cdp", metavar="URL",
                        help="attach to an already running Chrome, e.g. http://localhost:9222")
    parser.add_argument("--profile", default=".browser-profile",
                        help="persistent browser profile (keeps the login)")
    parser.add_argument("--headless", action="store_true", help="run the browser without a window")
    parser.add_argument("--window-size", metavar="WxH", default=None,
                        help="force the browser window size, e.g. 1200x760")
    parser.add_argument("--keep-open", action="store_true",
                        help="(kept for compatibility - the browser now always stays open "
                             "unless --close-browser is given)")
    parser.add_argument("--close-browser", action="store_true",
                        help="close the browser when the bot stops (default: leave it open "
                             "on the finished game, so Rematch / the lobby stay clickable)")
    parser.add_argument("--site-color", choices=("auto", "white", "black"), default="auto",
                        help="our colour on the website")
    parser.add_argument("--side", choices=("auto", "red", "black"), default="auto",
                        help="our colour in the engine (auto = read it from the men)")
    parser.add_argument("--depth", type=int, default=7, help="engine base search depth")
    parser.add_argument("--time", type=float, default=3.0, help="engine seconds per move (0 = no limit)")
    parser.add_argument("--poll", type=float, default=1.0, help="seconds between board polls")
    parser.add_argument("--coins", type=int, default=0, choices=range(0, 6),
                        help="lobby stake: 0 = 50 coins, 1 = 5 000 … 5 = 10 000 000")
    parser.add_argument("--email", default=None,
                        help="jokergame.cc login e-mail (or set JOKER_EMAIL)")
    parser.add_argument("--password", default=None,
                        help="jokergame.cc password (or set JOKER_PASSWORD)")
    parser.add_argument("--turn-timeout", type=float, default=120.0,
                        help="seconds to wait for the board when starting")
    parser.add_argument("--max-moves", type=int, default=0, help="stop after N moves (0 = unlimited)")
    parser.add_argument("--max-games", type=int, default=0,
                        help="stop after N games (0 = play until stopped: rematch, "
                             "then a new opponent)")
    parser.add_argument("--rematch-wait", type=float, default=30.0,
                        help="seconds to wait for a rematch after a game (default: 30, "
                             "the site's own window)")
    parser.add_argument("--chat-wait", type=float, default=5.0,
                        help="seconds a chat invitation is left alone before the bot "
                             "declines it and plays on (default: 5)")
    parser.add_argument("--think-delays", default="2,3,5,7",
                        help="comma separated 'thinking' times: the first move of a "
                             "game is quick, every later move waits one of these at "
                             "random (default: 2,3,5,7; 0 = no delay)")
    parser.add_argument("--max-errors", type=int, default=5, help="stop after N consecutive failures")
    parser.add_argument("--once", action="store_true", help="play a single move, then exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="read the board and print the move without clicking")
    parser.add_argument("--discover", action="store_true",
                        help="dump the detected board and selectors, then exit")
    parser.add_argument("--discover-only-board", action="store_true",
                        help="with --discover: do not click stones, only read the board")
    parser.add_argument("--log-dir", default="logs", help="where screenshots and dumps go")
    parser.add_argument("--no-probe", dest="use_probe", action="store_false",
                        help="trust the engine without asking the site for its legal moves "
                             "(faster, but the site may then reject a move)")
    parser.add_argument("--no-fit", dest="apply_fit", action="store_false",
                        help="do not resize/nudge the page to keep the whole board visible")
    parser.add_argument("--trace-board", action="store_true",
                        help="log the board the bot parsed before every move")
    parser.add_argument("--diagnose", action="store_true",
                        help="compare the site's legal moves with our rules and exit")
    parser.add_argument("--no-lobby", dest="lobby_walk", action="store_false",
                        help="never walk the lobby: only play on a board that is already "
                             "open in the browser (useful to continue a game)")
    parser.add_argument("--channel", default=None,
                        help="browser channel to launch, e.g. 'chrome' or 'msedge' "
                             "(uses the installed browser instead of Playwright's Chromium)")
    return parser


def run_bot(args: argparse.Namespace, log=None) -> int:
    """Launch the browser, walk the lobby and play - shared by the CLI and the GUI.

    ``log`` receives every line of output (the GUI pipes it into a text widget);
    ``args.should_stop``, when set, is polled so the caller can stop the loop.
    """
    log = log or print
    if sync_playwright is None:
        log("Playwright is not installed.")
        log("  python -m pip install -r requirements.txt")
        log("  python -m playwright install chromium")
        return 2

    should_stop = getattr(args, "should_stop", None)
    lobby_walk = getattr(args, "lobby_walk", True)
    channel = getattr(args, "channel", None)
    think_delays = tuple(
        float(part)
        for part in str(getattr(args, "think_delays", "") or "").replace(" ", "").split(",")
        if part
    )

    # Inside a frozen (PyInstaller) build Playwright looks for its browsers next to
    # its own driver, so point it at the copy installed on this machine; if there is
    # none we fall back to an installed Chrome/Edge below.
    if not channel and not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        browsers = chromium_browsers_dir()
        if browsers.is_dir():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)

    with sync_playwright() as playwright:
        context = None
        browser = None
        close_browser_on_exit = False
        try:
            profile = Path(args.profile).resolve()
            profile.mkdir(parents=True, exist_ok=True)
            headful = not args.headless
            endpoint = None
            if not args.cdp and not getattr(args, "close_browser", False):
                # The browser is started as its own process, so it stays open when
                # the bot is done: the finished game (Rematch) or the lobby remain
                # on screen and the next run simply reconnects to it.
                endpoint = start_detached_browser(
                    playwright, profile=profile, channel=channel,
                    headless=args.headless, window_size=args.window_size, log=log,
                )
            if args.cdp:
                browser = playwright.chromium.connect_over_cdp(args.cdp)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = _pick_page(context, args.url)
                log(f"attached to the browser at {args.cdp}")
            elif endpoint:
                browser = playwright.chromium.connect_over_cdp(endpoint)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = _pick_page(context, args.url)
                log(f"browser profile: {profile}  (kept open after the run)")
                if headful:
                    _fit_window(context, page, log, args.window_size)
            else:
                close_browser_on_exit = True
                options = {
                    "user_data_dir": str(profile),
                    "headless": args.headless,
                    # headful: let the page follow the real window, so what the user
                    # sees is exactly what the bot measures (no cropped board)
                    "no_viewport": headful,
                    "viewport": None if headful else {"width": 1280, "height": 900},
                    "args": ["--disable-blink-features=AutomationControlled"]
                    + (["--window-position=0,0"] if headful else []),
                }
                if channel:
                    options["channel"] = channel
                try:
                    context = playwright.chromium.launch_persistent_context(**options)
                except PlaywrightError as error:
                    fallback = None if channel else installed_browser_channel()
                    if fallback is None:
                        log(f"! could not launch the browser: {error}")
                        log("  install Playwright's Chromium:  python -m playwright install chromium")
                        log("  or use an installed browser:     --channel chrome")
                        raise
                    log(f"! Playwright's Chromium is not usable here - "
                        f"trying the installed {fallback}")
                    options["channel"] = fallback
                    context = playwright.chromium.launch_persistent_context(**options)
                page = _pick_page(context, args.url)
                log(f"browser profile: {profile}")
                if headful:
                    _fit_window(context, page, log, args.window_size)

            try:
                page.bring_to_front()
            except PlaywrightError:
                pass

            site = JokerGameSite(page, log=log)
            if args.diagnose:
                return _diagnose_rules(site, args)
            if args.discover:
                return _discover(site, page, args)

            bot = CheckersBot(
                site,
                site_color=args.site_color,
                side=args.side,
                depth=args.depth,
                time_limit=args.time or None,
                poll=args.poll,
                dry_run=args.dry_run,
                max_errors=args.max_errors,
                log_dir=args.log_dir,
                use_probe=args.use_probe,
                turn_timeout=args.turn_timeout,
                coins=args.coins,
                apply_fit=args.apply_fit,
                email=args.email or os.environ.get("JOKER_EMAIL"),
                password=args.password or os.environ.get("JOKER_PASSWORD"),
                trace_board=args.trace_board,
                rematch_wait=args.rematch_wait,
                chat_wait=args.chat_wait,
                think_delays=think_delays,
                log=log,
                should_stop=should_stop,
                lobby_walk=lobby_walk,
            )
            code = bot.run(max_moves=args.max_moves, once=args.once,
                           max_games=args.max_games)
            if endpoint:
                log("the browser stays open - 'Rematch' or the lobby are one click away "
                    "(use --close-browser if you want it closed)")
            return code
        finally:
            # The detached browser (the default) is not ours to close: it stays on
            # the finished game / lobby so the user can hit Rematch. Only a browser
            # this process launched itself (--close-browser) is closed here.
            if close_browser_on_exit and not getattr(args, "keep_open", False):
                for closer in (context, browser):
                    if closer is None:
                        continue
                    try:
                        closer.close()
                    except Exception:
                        pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return run_bot(args)


if __name__ == "__main__":
    sys.exit(main())
