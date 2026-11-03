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

    @property
    def isActive(self):
        """UIKit reads this as a property too (``overlay.toga_active``)."""
        return self.active


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
        class Hostile:
            def __getattr__(self, name):
                raise RuntimeError("nope")

        lines = overlay.diagnostics(Hostile(), max_depth=1)
        self.assertEqual(len(lines), 1)
        self.assertIn("frame=0x0", lines[0])       # zeros, but never an exception

    def test_frames_exposed_as_properties_are_read_too(self):
        # rubicon-objc gives NSRect *properties*: ``view.frame()`` raises
        # "'NSRect' object is not callable" - this is the ios-29 crash
        frame = types.SimpleNamespace(
            size=types.SimpleNamespace(width=390, height=752),
            origin=types.SimpleNamespace(x=0, y=0),
        )
        view = types.SimpleNamespace(
            frame=frame, bounds=frame, clipsToBounds=False, hidden=False,
            alpha=1.0, subviews=[],
        )
        lines = overlay.describe_view(view)
        self.assertIn("390x752", lines[0])
        self.assertIn("clip=False", lines[0])

    def test_a_method_style_view_still_works(self):
        window, root, stage, sheet, webview = self._tree()
        lines = overlay.describe_view(webview)
        self.assertIn("frame=390x844", lines[0])   # FakeView exposes methods

    def test_the_deepest_views_are_included(self):
        window, root, stage, sheet, webview = self._tree()
        lines = overlay.diagnostics(window, max_depth=4)
        self.assertIn("WKWebView", "\n".join(lines))


class ShippedDefaultTests(unittest.TestCase):
    """The app no longer does any native layout surgery - it has two pages."""

    def test_no_native_constraint_surgery_is_used(self):
        source = (ROOT / "src" / "checkersbot_ios" / "app.py").read_text(encoding="utf-8")
        # the floating panel is gone: nothing in the app pins or reparents views
        for gone in ("pin_panel(", "pin_to_window(", "restore_panel("):
            self.assertNotIn(gone, source)
        for gone in ("_install_overlay", "_panel_state", "_sync_panel_height",
                     "WEBVIEW_PIN_ENABLED", "PANEL_OVERLAY_ENABLED", "on_toggle_sheet"):
            self.assertNotIn(gone, source)


class PanelTests(unittest.TestCase):
    """The panel is floated the same way a bottom sheet would be: only its own
    constraints are swapped, its children stay untouched."""

    def _panel(self):
        window, root, stage, sheet, webview = _tree()
        guide = FakeView("safe-area")
        guide.superview = lambda: root
        root.safeAreaLayoutGuide = guide
        sheet.window = lambda: window
        window.rootViewController = lambda: types.SimpleNamespace(view=lambda: root)
        impl = _impl(sheet, root)
        return window, root, stage, sheet, webview, guide, impl

    def test_the_panel_is_pinned_to_the_bottom_of_the_content_view(self):
        window, root, stage, sheet, webview, guide, impl = self._panel()
        state = overlay.pin_panel(impl, height=44.0, layouts=LAYOUTS)
        self.assertTrue(state)
        created = state["constraints"]
        self.assertEqual(len(created), 4)                  # leading/trailing/bottom/height
        self.assertEqual(created["height"].constant, 44.0)
        # the panel is out of the Toga layout, the game's stage is not touched
        for name in overlay.CONSTRAINT_NAMES:
            self.assertFalse(getattr(impl.constraints, name).active)
        self.assertEqual(len(stage.constraints), 0)
        self.assertIs(state["anchor"], guide)              # respects the safe area

    def test_the_panel_height_can_change_without_touching_the_game(self):
        window, root, stage, sheet, webview, guide, impl = self._panel()
        state = overlay.pin_panel(impl, height=44.0, layouts=LAYOUTS)
        self.assertTrue(overlay.set_panel_height(state, 344.0))
        self.assertEqual(state["constraints"]["height"].constant, 344.0)
        self.assertEqual(len(stage.constraints), 0)        # WebView layout untouched

    def test_restore_puts_the_panel_back_into_the_layout(self):
        window, root, stage, sheet, webview, guide, impl = self._panel()
        state = overlay.pin_panel(impl, height=44.0, layouts=LAYOUTS)
        self.assertTrue(overlay.restore_panel(impl, state))
        for name in overlay.CONSTRAINT_NAMES:
            self.assertTrue(getattr(impl.constraints, name).active)
        for constraint in state["constraints"].values():
            self.assertFalse(constraint.active)

    def test_painting_the_background_sets_the_view_and_its_window(self):
        window = FakeView("UIWindow")
        view = FakeView("root")
        view.window = lambda: window
        made: list = []

        def factory(red, green, blue, alpha):
            made.append((red, green, blue, alpha))
            return "colour"

        self.assertTrue(overlay.paint_background(view, "#0b0f14", color_factory=factory))
        self.assertEqual(view.backgroundColor, "colour")
        self.assertEqual(window.backgroundColor, "colour")
        self.assertAlmostEqual(made[0][0], 11 / 255, places=3)


class PropertyView:
    """A view shaped the way rubicon-objc really hands UIKit over: **properties**.

    ``ios-30`` shipped ``view.superview()`` / ``view.window()`` / ``view.frame()``.
    On the device those are properties, so every call raised
    ``'UIView' object is not callable``; ``pin_panel`` threw, the app caught it and
    silently kept the Toga layout sheet - the panel stayed *below* the game.  The
    method-shaped :class:`FakeView` above never caught that, so this class exists.
    """

    def __init__(self, name, parent=None, children=None, size=(390, 844),
                 origin=(0, 0)) -> None:
        self.name = name
        self.parent = parent
        self.children = list(children or [])
        self.constraints = []
        self.front = None
        self.translatesAutoresizingMaskIntoConstraints = True
        self.clipsToBounds = False
        self.hidden = False
        self.isHidden = False
        self.alpha = 1.0
        self.objc_class = types.SimpleNamespace(name=name)
        self.frame = types.SimpleNamespace(
            size=types.SimpleNamespace(width=size[0], height=size[1]),
            origin=types.SimpleNamespace(x=origin[0], y=origin[1]),
        )
        self.bounds = self.frame
        self.window = None

    # -- the property side of UIKit ---------------------------------------
    @property
    def superview(self):
        return self.parent

    @property
    def subviews(self):
        return self.children

    @property
    def safeAreaLayoutGuide(self):
        return self

    # -- the method side ---------------------------------------------------
    def addConstraint(self, constraint) -> None:
        self.constraints.append(constraint)

    def removeConstraint(self, constraint) -> None:
        if constraint in self.constraints:
            self.constraints.remove(constraint)

    def bringSubviewToFront_(self, view) -> None:
        self.front = view

    def layoutIfNeeded(self) -> None:
        self.laid_out = True


class PropertyShapedTests(unittest.TestCase):
    """rubicon-objc properties must be *read*, never called (the ios-30 bug)."""

    def test_top_ancestor_reads_a_property_superview(self):
        root = PropertyView("root")
        stage = PropertyView("stage", root)
        game = PropertyView("game", stage)
        self.assertIs(overlay.top_ancestor(game), root)

    def test_anchor_view_prefers_the_root_view_controller_view(self):
        controller_view = PropertyView("controller-view")
        window = types.SimpleNamespace(
            rootViewController=types.SimpleNamespace(view=controller_view))
        view = PropertyView("game")
        view.window = window                       # a property, like UIKit
        self.assertIs(overlay.anchor_view(view), controller_view)

    def test_host_view_reads_a_property_superview(self):
        root = PropertyView("root")
        sheet = PropertyView("sheet", root)
        impl = types.SimpleNamespace(native=sheet, constraints=None)
        self.assertIs(overlay.host_view(impl), root)

    def test_bring_to_front_uses_the_property(self):
        container = PropertyView("toga-container")
        sheet = PropertyView("sheet", container)
        self.assertTrue(overlay.bring_to_front(types.SimpleNamespace(native=sheet)))
        self.assertIs(container.front, sheet)

    def test_describe_overlay_reports_parent_and_z_order(self):
        container = PropertyView("toga-container")
        game = PropertyView("game", container)
        sheet = PropertyView("sheet", container, size=(390, 304), origin=(0, 540))
        container.children = [game, sheet]
        line = overlay.describe_overlay(sheet, "panel")
        self.assertIn("parent=toga-container", line)
        self.assertIn("z=1/2", line)                # on top of the game
        self.assertIn("hidden=False", line)
        self.assertIn("frame=390x304@0,540", line)

    def test_pin_panel_survives_a_property_shaped_device(self):
        """The exact ios-30 failure: ``pin_panel`` raised instead of pinning."""
        container = PropertyView("toga-container")
        sheet = PropertyView("sheet", container)
        impl = _impl(sheet, container)
        state = overlay.pin_panel(impl, height=44.0, layouts=LAYOUTS)
        self.assertNotIn("error", state)            # it used to blow up here
        self.assertEqual(set(state["constraints"]),
                         {"leading", "trailing", "bottom", "height"})
        self.assertEqual(len(container.constraints), 4)   # added to the anchor
        # Toga's four constraints are off -> the panel left the layout flow
        self.assertFalse(any(c.active for c in (
            impl.constraints.left_constraint, impl.constraints.top_constraint,
            impl.constraints.width_constraint, impl.constraints.height_constraint)))
        # ... and the height is ours to drive: 44 closed, 304 open. Never the game.
        self.assertTrue(overlay.set_panel_height(state, 304.0))
        self.assertEqual(state["constraints"]["height"].constant, 304.0)

    def test_toga_active_lists_the_constraints_that_came_back(self):
        container = PropertyView("toga-container")
        sheet = PropertyView("sheet", container)
        impl = _impl(sheet, container)
        for name in overlay.CONSTRAINT_NAMES:       # what pin_panel() leaves behind
            getattr(impl.constraints, name).setActive_(False)
        self.assertEqual(overlay.toga_active(impl), [])
        impl.constraints.top_constraint.setActive_(True)
        self.assertEqual(overlay.toga_active(impl), ["top_constraint"])

    def test_no_uikit_property_is_called_like_a_method(self):
        """Source guard: ``view.frame()`` compiles here and crashes on the device."""
        import ast

        properties = {"superview", "window", "rootViewController", "subviews",
                      "frame", "bounds", "safeAreaLayoutGuide", "isHidden", "alpha"}
        for name in ("overlay.py", "app.py"):
            path = ROOT / "src" / "checkersbot_ios" / name
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in properties:
                    self.fail(f"{name}:{node.lineno} calls {func.attr}() like a method "
                              "- UIKit exposes it as a property")


class PanelVisibilityTests(unittest.TestCase):
    """The ⚙ bug: a panel can be pinned and still show nothing."""

    def _frames(self, height, y):
        return types.SimpleNamespace(
            size=types.SimpleNamespace(width=390, height=height),
            origin=types.SimpleNamespace(x=0, y=y),
        )

    def test_body_inside_the_panel_is_visible(self):
        body = types.SimpleNamespace(frame=self._frames(260, 44))
        panel = types.SimpleNamespace(frame=self._frames(304, 540))
        visible, reason = overlay.body_visible(body, panel)
        self.assertTrue(visible, reason)
        self.assertIn("260", reason)

    def test_body_outside_the_panel_is_not_visible(self):
        # exactly what a 44 pt sheet box did: the body was laid out at y=44
        body = types.SimpleNamespace(frame=self._frames(260, 44))
        panel = types.SimpleNamespace(frame=self._frames(44, 800))
        visible, reason = overlay.body_visible(body, panel)
        self.assertFalse(visible)
        self.assertIn("outside", reason)

    def test_collapsed_body_is_not_visible(self):
        body = types.SimpleNamespace(frame=self._frames(0, 44))
        panel = types.SimpleNamespace(frame=self._frames(304, 540))
        self.assertFalse(overlay.body_visible(body, panel)[0])

    def test_body_visible_reads_rubicon_properties(self):
        body = PropertyView("body", size=(390, 260), origin=(0, 44))
        panel = PropertyView("panel", size=(390, 304), origin=(0, 540))
        self.assertTrue(overlay.body_visible(body, panel)[0])


class AlertTests(unittest.TestCase):
    """Diagnostics must be readable even when the panel cannot open."""

    def test_root_controller_reads_the_window_property(self):
        controller = object()
        view = PropertyView("game")
        view.window = types.SimpleNamespace(rootViewController=controller)
        self.assertIs(overlay.root_controller(view), controller)

    def test_root_controller_without_a_window(self):
        self.assertIsNone(overlay.root_controller(PropertyView("game")))

    def test_show_alert_presents_on_the_controller(self):
        presented = {}

        class FakeAlert:
            @classmethod
            def alertControllerWithTitle(cls, title, message=None, preferredStyle=1):
                presented["title"] = title
                presented["message"] = message
                return cls()

            def addAction(self, action):
                presented.setdefault("actions", []).append(action)

        class FakeAction:
            @classmethod
            def actionWithTitle(cls, title, style=0, handler=None):
                return cls()

        class FakeController:
            def presentViewController(self, alert, animated=True, completion=None):
                presented["alert"] = alert
                presented["animated"] = animated

        fake_objc = types.ModuleType("rubicon.objc")
        fake_objc.ObjCClass = lambda name: {"UIAlertController": FakeAlert,
                                            "UIAlertAction": FakeAction}[name]
        fake_rubicon = types.ModuleType("rubicon")
        fake_rubicon.objc = fake_objc
        saved = {name: sys.modules.get(name) for name in ("rubicon", "rubicon.objc")}
        sys.modules["rubicon"] = fake_rubicon
        sys.modules["rubicon.objc"] = fake_objc
        try:
            ok = overlay.show_alert(FakeController(), "CheckersBot ⓘ", "log text")
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
        self.assertTrue(ok)
        self.assertEqual(presented["title"], "CheckersBot ⓘ")
        self.assertEqual(presented["message"], "log text")
        self.assertIn("alert", presented)

    def test_show_alert_without_a_controller_is_false(self):
        self.assertFalse(overlay.show_alert(None, "t", "m"))


class TwoPageSourceTests(unittest.TestCase):
    """Source-level checks for the two-page navigation (``app.py`` needs Toga)."""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "src" / "checkersbot_ios" / "app.py").read_text(
            encoding="utf-8")

    def test_one_webview_and_two_pages(self):
        self.assertEqual(self.source.count("toga.WebView("), 1,
                         "exactly one WebView instance may exist")
        for name in ("self.game_page", "self.settings_page", "self.tabbar",
                     "self.tab_game", "self.tab_settings"):
            self.assertIn(name, self.source)

    def test_exactly_one_page_is_mounted(self):
        # Toga 0.5.6 ignores ``display="none"`` in the layout, so the inactive page
        # must be *removed* from the container, not just hidden.
        self.assertIn("def _show_page(self, name: str)", self.source)
        self.assertIn("for child in list(self.pages.children):", self.source)
        self.assertIn("self.pages.remove(child)", self.source)
        self.assertIn("self.pages.add(page)", self.source)
        # ... and the pages are no longer juggled with display flags
        self.assertNotIn('self.game_page.style.display = "pack" if game', self.source)
        self.assertIn("children=[self.game_page],", self.source)   # only one mounted
        # the handlers the tab buttons call
        self.assertIn('on_press=self.on_show_game', self.source)
        self.assertIn('on_press=self.on_show_settings', self.source)

    def test_nothing_reloads_the_page_on_a_switch(self):
        # ``load_url`` may only appear in the explicit ⟲ lobby handler
        self.assertEqual(self.source.count("load_url"), 1)
        self.assertIn("async def on_reload", self.source)

    def test_the_settings_page_has_everything(self):
        for name in ("self.settings_status", "self.settings_start",
                     "self.refresh_button", "self.reset_button",
                     "self.log_button", "self.info_button",
                     "self._scroll_container", "self.log_view"):
            self.assertIn(name, self.source)
        self.assertIn("horizontal=False", self.source)   # vertical scrolling only

    def test_diagnostics_stay_reachable(self):
        self.assertIn('"ⓘ", on_press=self.on_show_diagnostics', self.source)
        self.assertIn("overlay.show_alert(", self.source)
        self.assertIn("webview page loads:", self.source)

    def test_the_switch_is_logged(self):
        for marker in ("გვერდი: ", "overlay: page state (", "webview_loads="):
            self.assertIn(marker, self.source)


class FakeScrollView(PropertyView):
    """A ``UIScrollView`` stand-in: properties (rubicon shape) + the flags iOS uses."""

    def __init__(self, width=390, height=260, content_height=44.0, doc_height=44.0):
        super().__init__("UIScrollView", size=(width, height))
        self.contentSize = types.SimpleNamespace(width=width, height=content_height)
        self.contentOffset = types.SimpleNamespace(x=0, y=0)
        self.isScrollEnabled = True
        self.alwaysBounceVertical = False
        self.showsVerticalScrollIndicator = False
        self.delaysContentTouches = True
        self.canCancelContentTouches = True
        self.contentInsetAdjustmentBehavior = 0
        self.document_container = types.SimpleNamespace(
            native=PropertyView("document", size=(width, doc_height)))


class FakeScrollImpl:
    def __init__(self, view):
        self.native = view
        self.document_container = view.document_container


def _fake_cg():
    """``CGRectMake`` / ``NSMakeSize`` stand-ins (rubicon is device-only)."""
    def rect(x=0, y=0, width=0, height=0):
        return types.SimpleNamespace(
            origin=types.SimpleNamespace(x=x, y=y),
            size=types.SimpleNamespace(width=width, height=height),
        )

    def size(width=0, height=0):
        return types.SimpleNamespace(width=width, height=height)

    return rect, size


class ScrollTests(unittest.TestCase):
    """The settings list must really scroll - every control has to be reachable."""

    def test_content_size_is_raised_to_the_real_content_height(self):
        view = FakeScrollView()
        impl = FakeScrollImpl(view)
        content = types.SimpleNamespace(layout=types.SimpleNamespace(height=760.0))
        ok, report = overlay.ensure_vertical_scroll(impl, content, minimum=260, cg=_fake_cg())
        self.assertTrue(ok, report)
        self.assertGreaterEqual(view.contentSize.height, 760.0)
        self.assertEqual(view.contentSize.width, 390)
        self.assertIn("contentSize", report)
        # the document view must cover the whole content so taps below the fold land
        self.assertGreaterEqual(view.document_container.native.frame.size.height, 760.0)

    def test_touch_flags_let_the_controls_be_used(self):
        view = FakeScrollView()
        ok, _ = overlay.ensure_vertical_scroll(FakeScrollImpl(view), cg=_fake_cg())
        self.assertTrue(ok)
        # without this iOS swallows the first tap on a control inside a scroll view
        self.assertFalse(view.delaysContentTouches)
        self.assertTrue(view.alwaysBounceVertical)
        self.assertTrue(view.isScrollEnabled)
        self.assertTrue(view.showsVerticalScrollIndicator)
        self.assertEqual(view.contentInsetAdjustmentBehavior, 2)   # never

    def test_a_correct_scroll_view_is_left_alone(self):
        view = FakeScrollView(content_height=760.0, doc_height=760.0)
        impl = FakeScrollImpl(view)
        content = types.SimpleNamespace(layout=types.SimpleNamespace(height=760.0))
        first = overlay.ensure_vertical_scroll(impl, content, cg=_fake_cg())
        second = overlay.ensure_vertical_scroll(impl, content, cg=_fake_cg())
        self.assertTrue(first[0] and second[0])
        self.assertIn("already correct", second[1])
        self.assertEqual(view.contentSize.height, 760.0)

    def test_native_subviews_extend_the_measured_extent(self):
        view = FakeScrollView()
        view.document_container.native.children = [
            PropertyView("row", size=(390, 300), origin=(0, 0)),
            PropertyView("row", size=(390, 420), origin=(0, 300)),
        ]
        content = types.SimpleNamespace(layout=types.SimpleNamespace(height=100.0))
        self.assertAlmostEqual(overlay.scroll_extent(FakeScrollImpl(view), content),
                               720.0)

    def test_scroll_extent_prefers_the_layout_when_it_is_larger(self):
        view = FakeScrollView()
        content = types.SimpleNamespace(layout=types.SimpleNamespace(height=900.0))
        self.assertGreaterEqual(overlay.scroll_extent(FakeScrollImpl(view), content),
                                900.0)

    def test_ensure_vertical_scroll_never_raises(self):
        ok, report = overlay.ensure_vertical_scroll(types.SimpleNamespace(native=None))
        self.assertFalse(ok)
        self.assertIn("no native view", report)

    def test_a_zero_width_scroll_view_is_reported(self):
        view = FakeScrollView(width=0, height=0)
        ok, report = overlay.ensure_vertical_scroll(FakeScrollImpl(view), cg=_fake_cg())
        self.assertFalse(ok)
        self.assertIn("no width", report)

    def test_scroll_report_mentions_the_numbers(self):
        report = overlay.scroll_report(FakeScrollImpl(FakeScrollView()))
        for token in ("frame=390x260", "contentSize=390x44", "delays=True",
                      "inset=0", "doc=390x44"):
            self.assertIn(token, report)

    def test_scroll_report_without_a_view(self):
        self.assertIn("no native view", overlay.scroll_report(None))


class ScrollingSourceTests(unittest.TestCase):
    """The settings page must fix and log its scroll view when it appears."""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "src" / "checkersbot_ios" / "app.py").read_text(
            encoding="utf-8")

    def test_the_scroll_view_is_kept_and_fixed(self):
        self.assertIn("self._scroll_container = toga.ScrollContainer(", self.source)
        self.assertIn("self._settings_content = toga.Box(", self.source)
        self.assertIn("def _ensure_scroll(self)", self.source)
        self.assertIn("overlay.ensure_vertical_scroll(", self.source)
        # once when the ⚙ page is shown, once when the log is added
        self.assertEqual(self.source.count("self._ensure_scroll()"), 2)

    def test_the_scroll_numbers_are_logged(self):
        self.assertIn("overlay.scroll_report(", self.source)
        self.assertIn("def _log_scroll(self)", self.source)

    def test_the_settings_list_is_the_only_scroll_view(self):
        self.assertEqual(self.source.count("toga.ScrollContainer("), 1)


class TitleBarTests(unittest.TestCase):
    """Toga's main window is a UINavigationController - its bar is the white header."""

    class FakeNavigationBar:
        def __init__(self, height):
            self.frame = types.SimpleNamespace(
                size=types.SimpleNamespace(width=390, height=height),
                origin=types.SimpleNamespace(x=0, y=59))

    class FakeController:
        def __init__(self, bar_height=44.0):
            self.navigationBar = TitleBarTests.FakeNavigationBar(bar_height)
            self.view = types.SimpleNamespace(
                safeAreaInsets=types.SimpleNamespace(top=59.0, bottom=34.0,
                                                     left=0.0, right=0.0))
            self.hidden = None

        def setNavigationBarHidden_animated_(self, hidden, animated=True):
            self.hidden = (bool(hidden), bool(animated))

    class FakeContainer:
        def __init__(self, controller):
            self.controller = controller
            self.native = types.SimpleNamespace(
                safeAreaInsets=types.SimpleNamespace(top=0.0))
            self.top_inset = 0
            self.on_native_layout = None

    class FakeContent:
        def __init__(self):
            self.refreshes = 0

        def refresh(self):
            self.refreshes += 1

    def _window(self, bar_height=44.0):
        controller = self.FakeController(bar_height)
        container = self.FakeContainer(controller)
        content = self.FakeContent()
        window = types.SimpleNamespace(
            container=container,
            interface=types.SimpleNamespace(content=content),
        )
        return window, controller, container, content

    def test_the_navigation_bar_is_hidden(self):
        window, controller, _, _ = self._window()
        report = overlay.hide_title_bar(window)
        self.assertEqual(controller.hidden, (True, False))
        self.assertIn("hidden=True", report)
        self.assertIn("44", report)              # it says how tall the bar was

    def test_the_content_gets_the_freed_height_but_keeps_the_island(self):
        window, _, container, content = self._window()
        overlay.hide_title_bar(window)
        # the *safe area* top (59 pt), not zero: the Dynamic Island stays clear
        self.assertEqual(container.top_inset, 59.0)
        self.assertGreaterEqual(content.refreshes, 1)

    def test_toga_recomputing_the_inset_from_the_bar_is_corrected(self):
        window, controller, container, content = self._window()
        overlay.hide_title_bar(window)
        # Toga's own handler would set the inset from the (hidden) bar again
        container.top_inset = 0
        container.on_native_layout(container)
        self.assertEqual(container.top_inset, 59.0)

    def test_a_missing_container_is_reported_not_raised(self):
        self.assertIn("no container", overlay.hide_title_bar(types.SimpleNamespace()))

    def test_describe_toga_lists_the_mounted_children(self):
        page = types.SimpleNamespace(
            style=types.SimpleNamespace(display="pack", flex=1, height=None),
            _impl=types.SimpleNamespace(native=PropertyView("p", size=(402, 800))),
            children=[],
        )
        root = types.SimpleNamespace(
            style=types.SimpleNamespace(display="pack", flex=1, height=None),
            _impl=types.SimpleNamespace(native=PropertyView("r", size=(402, 874))),
            children=[page],
        )
        lines = overlay.describe_toga(root, "pages")
        self.assertIn("mounted tree", lines[0])
        self.assertEqual(len(lines), 3)          # root + one page
        self.assertIn("frame=402x800", lines[2])


if __name__ == "__main__":
    unittest.main(verbosity=2)

