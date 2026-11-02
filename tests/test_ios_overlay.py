"""Tests for the native overlay that keeps the iOS game at a constant size.

The UIKit side cannot run on Windows, so the *constraint surgery* is exercised
against small stand-ins for the ObjC objects.  That is exactly the part that can
go wrong (which constraint is deactivated, where the new ones are attached, what
happens when an older Toga has no ``setActive_``), and it is fully testable.
"""

from __future__ import annotations

import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from checkersbot_ios import overlay            # noqa: E402


class FakeConstraint:
    """Stand-in for ``NSLayoutConstraint``."""

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.active = True
        self.constant = kwargs.get("constant", 0.0)

    @classmethod
    def constraintWithItem(cls, item, **kwargs):
        return cls(item=item, **kwargs)

    def setActive_(self, value) -> None:
        self.active = bool(value)


class OldConstraint(FakeConstraint):
    """A constraint without ``setActive_`` (the fallback path)."""

    setActive_ = None


class FakeView:
    def __init__(self, name: str, parent=None, children=None) -> None:
        self.name = name
        self.parent = parent
        self.constraints: list = []
        self.front = None
        self.translatesAutoresizingMaskIntoConstraints = True
        self.clipsToBounds = False
        self.children: list = list(children or [])
        self.translates = True
        # UIKit exposes the class through ``objc_class.name``; the fake mirrors it
        self.objc_class = types.SimpleNamespace(name=name)

    def superview(self):
        return self.parent

    def subviews(self):
        return self.children

    def addConstraint(self, constraint) -> None:
        self.constraints.append(constraint)

    def removeConstraint(self, constraint) -> None:
        if constraint in self.constraints:
            self.constraints.remove(constraint)

    def bringSubviewToFront_(self, view) -> None:
        self.front = view

    def frame(self):
        return types.SimpleNamespace(
            size=types.SimpleNamespace(width=390, height=844),
            origin=types.SimpleNamespace(x=0, y=0),
        )

    def bounds(self):
        return self.frame()

    def isHidden(self):
        return False

    def alpha(self):
        return 1.0

    def isOpaque(self):
        return True


def _tree():
    """window -> root -- stage -> webview, with the sheet as a sibling."""
    window = FakeView("window")
    root = FakeView("root", window)
    stage = FakeView("stage", root)
    sheet = FakeView("sheet", root)
    webview = FakeView("webview", stage)
    return window, root, stage, sheet, webview


def _impl(native, container, constraint_class=FakeConstraint):
    toga_constraints = types.SimpleNamespace(
        container=types.SimpleNamespace(native=container),
        left_constraint=constraint_class(),
        top_constraint=constraint_class(),
        width_constraint=constraint_class(),
        height_constraint=constraint_class(),
    )
    return types.SimpleNamespace(native=native, constraints=toga_constraints)


LAYOUTS = {
    "left": 1, "right": 2, "top": 3, "bottom": 4,
    "constraint": FakeConstraint, "equal": 0,
}


class PinTests(unittest.TestCase):
    def test_the_webview_is_pinned_to_the_window(self):
        window, root, stage, sheet, webview = _tree()
        impl = _impl(webview, stage)
        created = overlay.pin_to_window(impl, layouts=LAYOUTS)

        self.assertEqual(len(created), 4)                       # four edges
        # the new constraints hang on the *window* (the top ancestor), so the
        # game keeps the window's size no matter what Toga does with the layout
        self.assertEqual(len(window.constraints), 4)
        self.assertEqual(len(root.constraints), 0)
        self.assertEqual(len(stage.constraints), 0)
        for constraint in created:
            self.assertIs(constraint.kwargs["item"], webview)
            self.assertIs(constraint.kwargs["toItem"], window)
            self.assertTrue(constraint.active)
        sides = {(c.kwargs["attribute__1"], c.kwargs["attribute__2"]) for c in created}
        self.assertEqual(sides, {(1, 1), (2, 2), (3, 3), (4, 4)})
        # Toga's own constraints are out of the way but still owned by the impl
        for name in overlay.CONSTRAINT_NAMES:
            self.assertFalse(getattr(impl.constraints, name).active)
        self.assertFalse(webview.translatesAutoresizingMaskIntoConstraints)

    def test_a_constraint_without_set_active_is_detached_from_the_host(self):
        window, root, stage, sheet, webview = _tree()
        impl = _impl(webview, stage, constraint_class=OldConstraint)
        stage.addConstraint(impl.constraints.left_constraint)   # what Toga does
        overlay.pin_to_window(impl, layouts=LAYOUTS)
        self.assertNotIn(impl.constraints.left_constraint, stage.constraints)

    def test_pinning_without_a_native_view_does_nothing(self):
        impl = types.SimpleNamespace(native=None, constraints=None)
        self.assertEqual(overlay.pin_to_window(impl, layouts=LAYOUTS), [])

    def test_pinning_a_root_view_does_nothing(self):
        window, root, stage, sheet, webview = _tree()
        impl = _impl(window, window)                  # already the top of the tree
        self.assertEqual(overlay.pin_to_window(impl, layouts=LAYOUTS), [])

    def test_release_deactivates_the_pin(self):
        window, root, stage, sheet, webview = _tree()
        impl = _impl(webview, stage)
        created = overlay.pin_to_window(impl, layouts=LAYOUTS)
        overlay.release_pin(impl, created)
        for constraint in created:
            self.assertFalse(constraint.active)

    def test_bring_to_front_puts_the_sheet_above_the_game(self):
        window, root, stage, sheet, webview = _tree()
        self.assertTrue(overlay.bring_to_front(types.SimpleNamespace(native=sheet)))
        self.assertIs(root.front, sheet)
        loner = FakeView("loner")
        self.assertFalse(overlay.bring_to_front(types.SimpleNamespace(native=loner)))

    def test_describe_pin_reports_both_frames(self):
        window, root, stage, sheet, webview = _tree()
        text = overlay.describe_pin(types.SimpleNamespace(native=webview))
        self.assertIn("webview 390x844", text)
        self.assertIn("window 390x844", text)
        self.assertEqual(overlay.describe_pin(types.SimpleNamespace(native=None)),
                         "no native view")


class InstallPolicyTests(unittest.TestCase):
    """``overlay.install`` is the policy: iOS only, never raises, always logs."""

    def test_it_does_nothing_off_ios(self):
        called: list = []
        created = overlay.install(
            types.SimpleNamespace(native=None), platform="win32",
            pin=lambda impl: called.append(1) or [object()],
        )
        self.assertEqual(created, [])
        self.assertEqual(called, [])            # the pin is never even attempted

    def test_it_does_nothing_when_the_flag_is_off(self):
        # ios-28 shipped the pin on and broke the screen: it must stay opt-in
        called: list = []
        created = overlay.install(
            types.SimpleNamespace(native=None), platform="ios", enabled=False,
            pin=lambda impl: called.append(1) or [object()],
        )
        self.assertEqual(created, [])
        self.assertEqual(called, [])

    def test_it_pins_and_brings_the_sheet_to_front(self):
        lines: list = []
        front: list = []
        constraint = object()
        created = overlay.install(
            types.SimpleNamespace(native=None),
            types.SimpleNamespace(native=None),
            platform="ios",
            log=lines.append,
            pin=lambda impl: [constraint] * 4,
            front=lambda impl: front.append(impl) or True,
        )
        self.assertEqual(len(created), 4)
        self.assertEqual(len(front), 1)         # sheet brought to front
        self.assertTrue(any("pinned to the window" in line for line in lines))

    def test_a_failing_pin_is_reported_and_not_raised(self):
        lines: list = []

        def boom(impl):
            raise RuntimeError("no UIKit here")

        created = overlay.install(
            types.SimpleNamespace(native=None), platform="ios",
            log=lines.append, pin=boom,
        )
        self.assertEqual(created, [])
        self.assertTrue(any("pin failed" in line for line in lines))

    def test_an_impossible_pin_falls_back_to_the_layout_with_a_warning(self):
        lines: list = []
        created = overlay.install(
            types.SimpleNamespace(native=None), platform="ios",
            log=lines.append, pin=lambda impl: [],
        )
        self.assertEqual(created, [])
        self.assertTrue(any("could not be pinned" in line for line in lines))


class DiagnosticsTests(unittest.TestCase):
    """The hierarchy dump must say size / clipping / visibility for every view."""

    def _tree(self):
        webview = FakeView("WKWebView")
        stage = FakeView("stage", children=[webview])
        stage.clipsToBounds = True
        sheet = FakeView("sheet")
        root = FakeView("root", children=[stage, sheet])
        window = FakeView("UIWindow", children=[root])
        for child in (root, stage, sheet, webview):
            child.window = lambda: window
        return window, root, stage, sheet, webview

    def test_it_reports_frames_clipping_and_visibility(self):
        window, root, stage, sheet, webview = self._tree()
        lines = overlay.diagnostics(window, max_depth=4)
        text = "\n".join(lines)
        self.assertIn("UIWindow", text)
        self.assertIn("frame=390x844", text)
        self.assertIn("clip=True", text)             # the stage clips its child
        self.assertIn("hidden=False", text)
        self.assertIn("alpha=1.00", text)
        self.assertIn("subs=2", text)                # root has stage + sheet

    def test_an_unreadable_view_does_not_raise(self):
        broken = types.SimpleNamespace()
        lines = overlay.diagnostics(broken, max_depth=1)
        self.assertTrue(lines)
        self.assertIn("unreadable", "\n".join(lines))

    def test_the_deepest_views_are_included(self):
        window, root, stage, sheet, webview = self._tree()
        lines = overlay.diagnostics(window, max_depth=4)
        self.assertIn("WKWebView", "\n".join(lines))


class ShippedDefaultTests(unittest.TestCase):
    """The risky native pin must not ship enabled without device proof."""

    def test_the_overlay_is_off_by_default_in_the_app(self):
        source = (ROOT / "src" / "checkersbot_ios" / "app.py").read_text(encoding="utf-8")
        self.assertIn("OVERLAY_ENABLED = False", source)
        self.assertIn("enabled=bool(OVERLAY_ENABLED)", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

