"""Backwards compatible alias for :mod:`checkersbot.board`.

The rules engine used to live in this file; it moved into the ``checkersbot``
package so that the desktop bot and the iOS app can share it.  ``from board
import Board`` keeps working for older scripts and for the tests.
"""

from checkersbot.board import *          # noqa: F401,F403
