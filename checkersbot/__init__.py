"""The reusable core of the checkers bot.

=====================  =======================================================
:mod:`checkersbot.board`  rules: 8x8 board, move generation, make/unmake
:mod:`checkersbot.ai`     negamax search, evaluation, dynamic depth
:mod:`checkersbot.site`   jokergame.cc protocol: selectors, JS, ``SiteState``
:mod:`checkersbot.bot`    ``CheckersBot`` - the loop that plays a game
=====================  =======================================================

Nothing in here imports a browser library.  ``checkersbot.site.Site`` is the
interface the bot needs; ``web_bot.JokerGameSite`` implements it with Playwright
(Windows/Linux/macOS) and ``checkersbot_ios.site.WebViewSite`` implements it on
top of the ``toga.WebView`` of the iOS app, so one bot runs on both platforms.
"""

from __future__ import annotations

__version__ = "2.0.0"
