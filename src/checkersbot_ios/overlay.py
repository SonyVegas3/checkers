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


def anchor_view(view: Any) -> Any:
    """The view the pin should be attached to.

    Preferring the **root view controller's view** (a real layout container) over
    the raw ``UIWindow``: Auto Layout is much happier with a normal ``UIView`` as
    the common ancestor, and that view already has the window's bounds minus the
    parts UIKit manages.
    """
    try:
        window = view.window()
    except Exception:
        window = None
    if window is not None:
        try:
            controller = window.rootViewController()
            if controller is not None:
                root = controller.view()
                if root is not None:
                    return root
        except Exception:
            pass
    return top_ancestor(view)


def pin_to_window(widget_impl: Any, layouts: Any = None,
                  anchor: Any = None) -> List[Any]:
    """Make the widget cover the whole window, independent of the Toga layout.

    Returns the constraints that were created (an empty list when the widget
    could not be pinned: a missing native view, an unknown Toga version, ...).
    """
    layouts = layouts or _layouts()
    native = getattr(widget_impl, "native", None)
    if native is None:
        return []
    host = host_view(widget_impl)
    root = anchor if anchor is not None else anchor_view(native)
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


def describe_view(view: Any, depth: int = 0) -> List[str]:
    """One line per view: class, frame, bounds, clipping, hidden, alpha, insets."""
    lines: List[str] = []
    try:
        frame = view.frame()
        bounds = view.bounds()
        name = type(view).__name__
        try:
            name = str(view.objc_class.name)
        except Exception:
            pass
        lines.append(
            "  " * depth + f"{name} frame={frame.size.width:.0f}x{frame.size.height:.0f}"
            f"@{frame.origin.x:.0f},{frame.origin.y:.0f}"
            f" bounds={bounds.size.width:.0f}x{bounds.size.height:.0f}"
            f" clip={bool(view.clipsToBounds)}"
            f" hidden={bool(view.isHidden())}"
            f" alpha={float(view.alpha()):.2f}"
            f" opaque={bool(view.isOpaque())}"
            f" subs={len(view.subviews())}"
        )
    except Exception as error:
        lines.append("  " * depth + f"(unreadable view: {error})")
    return lines


def diagnostics(view: Any, max_depth: int = 6, max_children: int = 6) -> List[str]:
    """Dump the native view hierarchy - the only way to see what UIKit really did.

    Every line has the class, frame, bounds, clipping flag, hidden/alpha state and
    the number of subviews, so a wrong size, a covering view, a clipping parent or
    an invisible overlay can be told apart instead of guessed.
    """
    lines: List[str] = []
    stack = [(view, 0)]
    while stack:
        current, depth = stack.pop()
        lines.extend(describe_view(current, depth))
        if depth >= max_depth or current is None:
            continue
        try:
            children = list(current.subviews())
        except Exception:
            continue
        # deepest child last, so the output reads top-down like the screen
        for child in reversed(children[-max_children:]):
            stack.append((child, depth + 1))
    return lines


def install(webview_impl: Any, sheet_impl: Any = None, *,
            platform: Optional[str] = None, log: Any = None,
            pin: Any = None, front: Any = None,
            enabled: Optional[bool] = None) -> List[Any]:
    """Pin the game and put the sheet in front of it; returns the constraints.

    This is the whole policy of the overlay in one testable place: it only runs
    when it is enabled *and* the platform is iOS, it never raises, it says exactly
    what happened in the log, and it returns an empty list when the pin is
    impossible (the caller then keeps the plain layout).  ``pin``/``front``/
    ``platform``/``log``/``enabled`` are injectable for tests.
    """
    platform = sys.platform if platform is None else platform
    enabled = (platform == "ios") if enabled is None else bool(enabled)
    log = log if log is not None else (lambda text: None)
    pin = pin or pin_to_window
    front = front or bring_to_front
    if not enabled or platform != "ios":
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
    "describe_view",
    "diagnostics",
    "anchor_view",
    "top_ancestor",
    "host_view",
]

