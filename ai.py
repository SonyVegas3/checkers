"""Backwards compatible alias for :mod:`checkersbot.ai`.

The search used to live in this file; it moved into the ``checkersbot`` package
so that the desktop bot and the iOS app can share it.  ``from ai import
CheckersAI`` keeps working for older scripts and for the tests.
"""

from checkersbot.ai import *             # noqa: F401,F403
