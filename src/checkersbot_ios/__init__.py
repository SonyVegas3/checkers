"""CheckersBot for iPhone and iPad.

The app is a thin shell: a ``toga.WebView`` that shows jokergame.cc plus the
shared bot from :mod:`checkersbot` running in a worker thread.  There is no
Playwright and no Chromium on iOS - the WKWebView *is* the browser, and the bot
talks to it through the same JavaScript the desktop bot uses.
"""

from __future__ import annotations

__version__ = "2.0.0"
