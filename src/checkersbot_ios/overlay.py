"""Pin a Toga widget to the window so a panel can float on top of it.

**Why this file exists.**  Toga's iOS backend positions every widget with four
``NSLayoutConstraint`` objects - left, top, width, height - created against the
widget's *container* view (see ``toga_iOS/constraints.py``: ``Constraints.update()``
keeps ``left_constraint``, ``top_constraint``, ``width_constraint`` and
``height_constraint`` on the widget implementation).  The layout is pure Auto
Layout, so a widget can be taken out of the Toga layout **without touching its
native view**: deactivate those four constraints and install four new ones that
pin the same view to the top of the view hierarchy.

That is what makes a real floating sheet possible: the ``WKWebView`` keeps a
constant, window-sized frame while Toga keeps laying the sheet out on top of it,
so opening/closing the sheet never resizes, reflows, zooms or reloads the game.

Everything here is *optional*: if the private Toga attributes are not what this
module expects, :func:`pin_to_window` returns an empty list and the caller falls
back to the plain layout - the app must never break for this.
"""

from __future__ import annotations

import sys
from typing import Any, List, Optional

#: The four constraint slots Toga keeps on every widget implementation.
CONSTRAINT_NAMES = ("left_constraint", "top_constraint",
                    "width_constraint", "height_constraint")


def _layouts() -> Any:
    """The UIKit pieces we need (imported late: only iOS has them)."""
    from toga_iOS.libs import (                 # noqa: PLC0415 - device only
        NSLayoutAttributeBottom,
        NSLayoutAttributeLeft,
        NSLayoutAttributeRight,
        NSLayoutAttributeTop,
        NSLayoutConstraint,
        NSLayoutRelationEqual,
    )

    return {
        "bottom": NSLayoutAttributeBottom,
        "left": NSLayoutAttributeLeft,
        "right": NSLayoutAttributeRight,
        "top": NSLayoutAttributeTop,
        "constraint": NSLayoutConstraint,
        "equal": NSLayoutRelationEqual,
    }


def top_ancestor(view: Any) -> Any:
    """Walk up to the outermost view (the window) - the pin target."""
    seen = 0
    while view is not None and seen < 32:
        parent = view.superview()
        if parent is None:
            return view
        view = parent
        seen += 1
    return view


def host_view(widget_impl: Any) -> Optional[Any]:
    """The view Toga added the widget's constraints to."""
    constraints = getattr(widget_impl, "constraints", None)
    container = getattr(constraints, "container", None) if constraints else None
    if container is not None:
        native = getattr(container, "native", None)
        if native is not None:
            return native
    native = getattr(widget_impl, "native", None)
    return native.superview() if native is not None else None


def pin_to_window(widget_impl: Any, layouts: Any = None) -> List[Any]:
    """Make the widget cover the whole window, independent of the Toga layout.

    Returns the constraints that were created (an empty list when the widget
    could not be pinned: a missing native view, an unknown Toga version, ...).
    """
    layouts = layouts or _layouts()
    native = getattr(widget_impl, "native", None)
    if native is None:
        return []
    host = host_view(widget_impl)
    root = top_ancestor(native)
    if root is None or root is native or host is None:
        return []
    try:
        native.translatesAutoresizingMaskIntoConstraints = False
    except Exception:
        pass

    # 1) take the widget out of Toga's layout: the constraint *objects* stay on
    #    the implementation (Constraints.update() keeps writing their constants),
    #    they are simply no longer active any more.
    constraints = getattr(widget_impl, "constraints", None)
    for name in CONSTRAINT_NAMES:
        constraint = getattr(constraints, name, None) if constraints else None
        if constraint is None:
            continue
        try:
            constraint.setActive_(False)
        except Exception:
            try:
                host.removeConstraint(constraint)
            except Exception:
                pass            # nothing more we can do; the pin below still wins

    # 2) pin the same view to the window edges
    created: List[Any] = []
    for side in ("left", "top", "right", "bottom"):
        constraint = layouts["constraint"].constraintWithItem(
            native,
            attribute__1=layouts[side],
            relatedBy=layouts["equal"],
            toItem=root,
            attribute__2=layouts[side],
            multiplier=1.0,
            constant=0.0,
        )
        root.addConstraint(constraint)
        created.append(constraint)
    return created


def release_pin(widget_impl: Any, created: List[Any]) -> None:
    """Undo :func:`pin_to_window` (used when the overlay is switched off)."""
    root = top_ancestor(getattr(widget_impl, "native", None))
    for constraint in created or []:
        try:
            constraint.setActive_(False)
        except Exception:
            try:
                root.removeConstraint(constraint)
            except Exception:
                pass


def bring_to_front(widget_impl: Any) -> bool:
    """Draw this widget above its siblings (the sheet above the full-size game)."""
    native = getattr(widget_impl, "native", None)
    if native is None:
        return False
    try:
        parent = native.superview()
        if parent is None:
            return False
        parent.bringSubviewToFront_(native)
        return True
    except Exception:
        return False


def describe_pin(widget_impl: Any) -> str:
    """``webview 390x844 | window 390x844`` - proof the pin worked on the device."""
    native = getattr(widget_impl, "native", None)
    if native is None:
        return "no native view"
    try:
        frame = native.frame()
        window = top_ancestor(native).frame()
        return (
            f"webview {frame.size.width:.0f}x{frame.size.height:.0f} "
            f"@ {frame.origin.x:.0f},{frame.origin.y:.0f} | "
            f"window {window.size.width:.0f}x{window.size.height:.0f}"
        )
    except Exception as error:                  # pragma: no cover - device only
        return f"(cannot read the native frames: {error})"


def install(webview_impl: Any, sheet_impl: Any = None, *,
            platform: Optional[str] = None, log: Any = None,
            pin: Any = None, front: Any = None) -> List[Any]:
    """Pin the game and put the sheet in front of it; returns the constraints.

    This is the whole policy of the overlay in one testable place: it only runs on
    iOS, it never raises, it says exactly what happened in the log, and it returns
    an empty list when the pin is impossible (the caller then keeps the plain
    layout).  ``pin``/``front``/``platform``/``log`` are injectable for tests.
    """
    platform = sys.platform if platform is None else platform
    log = log if log is not None else (lambda text: None)
    pin = pin or pin_to_window
    front = front or bring_to_front
    if platform != "ios":
        return []
    try:
        created = pin(webview_impl)
    except Exception as error:                  # never break the app for this
        log(f"! overlay: pin failed: {error}")
        return []
    if not created:
        log("! overlay: webview could not be pinned - the panel uses the layout "
            "and the game shrinks while it is open")
        return []
    if sheet_impl is not None:
        front(sheet_impl)                       # the sheet draws above the game
    log("overlay: the webview is pinned to the window, the panel floats above it")
    log("overlay: " + describe_pin(webview_impl))
    return list(created)


__all__ = [
    "CONSTRAINT_NAMES",
    "install",
    "pin_to_window",
    "release_pin",
    "bring_to_front",
    "describe_pin",
    "top_ancestor",
    "host_view",
]

