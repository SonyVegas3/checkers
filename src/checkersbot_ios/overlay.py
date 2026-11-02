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
from typing import Any, List, Optional, Tuple

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
    """Walk up to the outermost view (the window) - the pin target.

    ``superview`` is an Objective-C **property**; rubicon-objc exposes it as one, so
    ``view.superview`` is correct and ``view.superview()`` raises
    ``'UIView' object is not callable``.  ``read`` accepts either shape.
    """
    seen = 0
    while view is not None and seen < 32:
        parent = read(view, "superview")
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
    return read(native, "superview") if native is not None else None


def anchor_view(view: Any) -> Any:
    """The view the pin should be attached to.

    Preferring the **root view controller's view** (a real layout container) over
    the raw ``UIWindow``: Auto Layout is much happier with a normal ``UIView`` as
    the common ancestor, and that view already has the window's bounds minus the
    parts UIKit manages.  All four lookups go through :func:`read` because UIKit
    declares them as properties (``window``, ``rootViewController``, ``view``) and
    calling them raises ``'UIWindow' object is not callable`` - the exact bug that
    kept the settings panel in the Toga layout on iOS builds <= ``ios-30``.
    """
    window = read(view, "window")
    if window is not None:
        controller = read(window, "rootViewController")
        if controller is not None:
            root = read(controller, "view")
            if root is not None:
                return root
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
        parent = read(native, "superview")
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
        frame = read(native, "frame")
        window = read(top_ancestor(native), "frame")
        return (
            f"webview {number(read(frame, 'size'), 'width'):.0f}"
            f"x{number(read(frame, 'size'), 'height'):.0f} "
            f"@ {number(read(frame, 'origin'), 'x'):.0f},"
            f"{number(read(frame, 'origin'), 'y'):.0f} | "
            f"window {number(read(window, 'size'), 'width'):.0f}"
            f"x{number(read(window, 'size'), 'height'):.0f}"
        )
    except Exception as error:                  # pragma: no cover - device only
        return f"(cannot read the native frames: {error})"


def read(obj: Any, name: str, default: Any = None, *alternates: str) -> Any:
    """Read a value whether UIKit exposes it as a property or as a method.

    rubicon-objc hands back **structs as properties** (``view.frame`` is an
    ``NSRect`` - calling it raises ``'NSRect' object is not callable``), while
    some other attributes are methods.  This helper accepts both, plus alternate
    spellings (``isHidden`` / ``hidden``).
    """
    for candidate in (name,) + alternates:
        try:
            value = getattr(obj, candidate)
        except Exception:
            continue
        if callable(value):
            try:
                return value()
            except Exception:
                continue
        return value
    return default


def number(value: Any, attribute: str, default: float = 0.0) -> float:
    """``number(frame.size, 'width')`` - tolerant of struct/property shapes."""
    try:
        return float(getattr(value, attribute))
    except Exception:
        try:
            return float(value[attribute])
        except Exception:
            return default


def describe_view(view: Any, depth: int = 0) -> List[str]:
    """One line per view: class, frame, bounds, clipping, hidden, alpha, subviews."""
    lines: List[str] = []
    try:
        frame = read(view, "frame")
        size = read(frame, "size", frame)
        origin = read(frame, "origin", frame)
        bounds = read(view, "bounds")
        bsize = read(bounds, "size", bounds)
        name = type(view).__name__
        try:
            name = str(read(read(view, "objc_class"), "name", name))
        except Exception:
            pass
        subviews = read(view, "subviews", []) or []
        lines.append(
            "  " * depth
            + f"{name} frame={number(size, 'width'):.0f}x{number(size, 'height'):.0f}"
            f"@{number(origin, 'x'):.0f},{number(origin, 'y'):.0f}"
            f" bounds={number(bsize, 'width'):.0f}x{number(bsize, 'height'):.0f}"
            f" clip={bool(read(view, 'clipsToBounds', False))}"
            f" hidden={bool(read(view, 'hidden', False, 'isHidden'))}"
            f" alpha={float(read(view, 'alpha', 1.0)):.2f}"
            f" subs={len(subviews)}"
        )
    except Exception as error:
        lines.append("  " * depth + f"(unreadable view: {error})")
    return lines


def describe_overlay(view: Any, label: str = "overlay") -> str:
    """One line about a *single* view: parent, z-order, visibility and frame.

    This is the report the panel needs: it says where the panel really lives
    (which superview), whether it is on top (its index among its siblings), whether
    it is visible at all, and its native frame - so "the panel is below the game"
    can be read off one log line instead of being guessed.
    """
    if view is None:
        return f"{label}: (no native view)"
    try:
        frame = read(view, "frame")
        size = read(frame, "size", frame)
        origin = read(frame, "origin", frame)
        parent = read(view, "superview")
        siblings = list(read(parent, "subviews", []) or []) if parent is not None else []
        try:
            order = siblings.index(view)
        except ValueError:
            order = -1
        parent_name = "None"
        if parent is not None:
            parent_name = str(read(read(parent, "objc_class"), "name", type(parent).__name__))
        return (
            f"{label}: parent={parent_name} "
            f"z={order}/{len(siblings)} "
            f"hidden={bool(read(view, 'hidden', False, 'isHidden'))} "
            f"alpha={float(read(view, 'alpha', 1.0)):.2f} "
            f"frame={number(size, 'width'):.0f}x{number(size, 'height'):.0f}"
            f"@{number(origin, 'x'):.0f},{number(origin, 'y'):.0f}"
        )
    except Exception as error:                  # pragma: no cover - device only
        return f"{label}: (unreadable: {error})"


def toga_active(panel_impl: Any) -> List[str]:
    """Which of Toga's four constraints are active again (empty list = pinned)."""
    active: List[str] = []
    constraints = getattr(panel_impl, "constraints", None)
    for name in CONSTRAINT_NAMES:
        constraint = getattr(constraints, name, None) if constraints else None
        if constraint is None:
            continue
        if bool(read(constraint, "isActive", False)):
            active.append(name)
    return active


def body_visible(body_impl: Any, panel_impl: Any) -> Tuple[bool, str]:
    """Is the settings body really inside the visible panel area?

    Returns ``(visible, reason)``.  This is what decides whether the floating panel
    worked or whether the app must fall back to the plain layout sheet - a panel
    whose body landed outside its own bounds looks exactly like "the ⚙ button does
    nothing", which is precisely the iOS bug this exists to catch.
    """
    try:
        body = read(body_impl, "frame")
        panel = read(panel_impl, "frame")
    except Exception as error:                  # pragma: no cover - device only
        return False, f"frames unreadable ({error})"
    body_h = number(read(body, "size"), "height")
    body_y = number(read(body, "origin"), "y")
    panel_h = number(read(panel, "size"), "height")
    if body_h <= 1:
        return False, f"body height {body_h:.0f} pt"
    if panel_h <= 1:
        return False, f"panel height {panel_h:.0f} pt"
    if body_y < -1 or body_y + body_h > panel_h + 2:
        return False, (f"body {body_h:.0f} pt at y={body_y:.0f} "
                       f"is outside a {panel_h:.0f} pt panel")
    return True, f"body {body_h:.0f} pt at y={body_y:.0f} in a {panel_h:.0f} pt panel"


def root_controller(view: Any) -> Any:
    """The view controller that can present an alert (``window.rootViewController``)."""
    window = read(view, "window")
    return read(window, "rootViewController") if window is not None else None


def show_alert(controller: Any, title: str, message: str, limit: int = 1200) -> bool:
    """Present a native ``UIAlertController`` - diagnostics without the panel.

    When the settings panel cannot open, the log lives *inside* that panel, so there
    is no way to read why.  An alert is presented by UIKit itself: it does not need
    the Toga layout, the panel, or the WebView, so it always works.
    """
    if controller is None:
        return False
    text = message if len(message) <= limit else message[:limit] + "\n…"
    try:
        from rubicon.objc import ObjCClass      # noqa: PLC0415 - device only

        alert_class = ObjCClass("UIAlertController")
        action_class = ObjCClass("UIAlertAction")
        alert = alert_class.alertControllerWithTitle(
            str(title), message=str(text), preferredStyle=1,   # 1 == UIAlert
        )
        action = action_class.actionWithTitle("დახურვა", style=0, handler=None)
        alert.addAction(action)
        controller.presentViewController(
            alert, animated=True, completion=None,
        )
        return True
    except Exception:
        return False


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
        if current is None:
            continue
        lines.extend(describe_view(current, depth))
        if depth >= max_depth:
            continue
        try:
            children = list(read(current, "subviews", []) or [])
        except Exception:
            continue
        for child in reversed(children[-max_children:]):
            stack.append((child, depth + 1))
    return lines


def safe_anchor(view: Any) -> Any:
    """The root controller's content view, or its safe-area layout guide."""
    anchor = anchor_view(view)
    try:
        guide = getattr(anchor, "safeAreaLayoutGuide", None)
        if callable(guide):
            guide = guide()
        if guide is not None:
            return guide
    except Exception:
        pass
    return anchor


def pin_panel(panel_impl: Any, height: float = 44.0, anchor: Any = None,
              layouts: Any = None) -> dict:
    """Float a Toga box along the bottom of the screen, outside the layout flow.

    Only the panel's **own** constraints (the ones against its Toga parent) are
    deactivated - its children are untouched, so Toga keeps laying them out inside
    the panel.  The panel gets four new constraints of ours (leading, trailing,
    bottom, height) so the game is never pushed around.

    Returns a state dict for :func:`set_panel_height` / :func:`restore_panel`, or
    ``{}`` when the panel cannot be floated (the caller keeps the plain layout).

    This function **never raises**: the first shipped version let a rubicon-objc
    ``'UIView' object is not callable`` (``superview`` is a property) escape, the
    app caught it and silently fell back to the layout sheet - which is exactly why
    the panel kept taking space below the game.  Failures are reported in the state
    dict (``{"error": ...}``) so the caller can log them.
    """
    try:
        return _pin_panel(panel_impl, height=height, anchor=anchor, layouts=layouts)
    except Exception as error:                  # never break the UI for this
        return {"error": f"{type(error).__name__}: {error}"}


def _pin_panel(panel_impl: Any, height: float = 44.0, anchor: Any = None,
               layouts: Any = None) -> dict:
    layouts = layouts or _layouts()
    native = getattr(panel_impl, "native", None)
    if native is None:
        return {}
    root = anchor if anchor is not None else safe_anchor(native)
    if root is None or root is native:
        return {}
    host = host_view(panel_impl)
    constraints = getattr(panel_impl, "constraints", None)
    for name in CONSTRAINT_NAMES:                # out of the Toga layout
        constraint = getattr(constraints, name, None) if constraints else None
        if constraint is None:
            continue
        try:
            constraint.setActive_(False)
        except Exception:
            try:
                host.removeConstraint(constraint)
            except Exception:
                pass
    try:
        native.translatesAutoresizingMaskIntoConstraints = False
    except Exception:
        pass

    created: dict = {}

    def add(item_attribute: str, to_item: Any, to_attribute: str, constant: float):
        constraint = layouts["constraint"].constraintWithItem(
            native,
            attribute__1=layouts[item_attribute],
            relatedBy=layouts["equal"],
            toItem=to_item,
            attribute__2=layouts[to_attribute],
            multiplier=1.0,
            constant=constant,
        )
        root.addConstraint(constraint)
        return constraint

    # a self-referential bottom/top pair is how "height" is expressed on iOS
    created["height"] = add("bottom", native, "top", float(height))
    created["leading"] = add("left", root, "left", 0.0)
    created["trailing"] = add("right", root, "right", 0.0)
    created["bottom"] = add("bottom", root, "bottom", 0.0)
    return {"constraints": created, "anchor": root, "height": float(height)}


def set_panel_height(state: dict, height: float) -> bool:
    """Resize the floating panel (44 pt closed, taller when the sheet is open)."""
    try:
        state["constraints"]["height"].constant = float(height)
        state["height"] = float(height)
        return True
    except Exception:
        return False


def restore_panel(panel_impl: Any, state: dict) -> bool:
    """Undo :func:`pin_panel`: back to the plain Toga layout (the safe fallback)."""
    restored = False
    for constraint in (state.get("constraints") or {}).values():
        try:
            constraint.setActive_(False)
        except Exception:
            pass
    constraints = getattr(panel_impl, "constraints", None)
    for name in CONSTRAINT_NAMES:
        constraint = getattr(constraints, name, None) if constraints else None
        if constraint is None:
            continue
        try:
            constraint.setActive_(True)
            restored = True
        except Exception:
            pass
    return restored


def layout_now(view: Any) -> bool:
    """Force UIKit to apply pending constraints now (``layoutIfNeeded``)."""
    try:
        method = getattr(view, "layoutIfNeeded")
        if callable(method):
            method()
            return True
    except Exception:
        pass
    return False


def paint_background(view: Any, hex_color: str = "#0b0f14",
                     color_factory: Any = None) -> bool:
    """Paint a view (and its window) so the safe-area band is not glaring white."""
    try:
        if color_factory is None:
            from toga_iOS.libs import UIColor      # noqa: PLC0415 - device only
            color_factory = UIColor.colorWithRed_green_blue_alpha
        red = int(hex_color[1:3], 16) / 255.0
        green = int(hex_color[3:5], 16) / 255.0
        blue = int(hex_color[5:7], 16) / 255.0
        colour = color_factory(red, green, blue, 1.0)
        view.backgroundColor = colour
        try:
            window = read(view, "window")
            if window is not None:
                window.backgroundColor = colour
        except Exception:
            pass
        return True
    except Exception:
        return False


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
    "read",
    "number",
    "anchor_view",
    "safe_anchor",
    "pin_panel",
    "set_panel_height",
    "restore_panel",
    "toga_active",
    "describe_overlay",
    "body_visible",
    "root_controller",
    "show_alert",
    "layout_now",
    "paint_background",
    "top_ancestor",
    "host_view",
]

