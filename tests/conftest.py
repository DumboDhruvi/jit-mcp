"""Shared test fixtures and realistic FakeAccessibilityAdapter for jit-mcp tests.

Licensed under Apache-2.0.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from jit_mcp.adapters.base import (
    ActionExecutionError,
    AppNotFoundError,
    ControlDisabledError,
    NodeNotFoundError,
)
from jit_mcp.models import AppInfo, UiBounds, UiNode


class FakeAccessibilityAdapter:
    """In-memory accessibility adapter simulating realistic Linux desktop applications."""

    def __init__(self) -> None:
        self.click_log: list[str] = []
        self.coordinate_clicks: list[tuple[int, int]] = []
        self.hover_log: list[str] = []
        self.mouse_paths: list[tuple[list[tuple[int, int]], bool]] = []
        self.shortcuts_pressed: list[str] = []
        self.keys_logged: list[list[str]] = []

        # Configurable hooks for stress & timeout test cases
        self.child_delay_ms: int = 0
        self.simulate_label_drift: bool = False
        self.fail_node_actions: set[str] = set()

        # Simulated application state for "demo.app"
        self.demo_submit_count: int = 0
        self.demo_agreement_text: str = ""
        self.demo_auto_save: bool = False
        self.demo_theme: str = "Light"
        self.demo_scroll_offset: int = 0
        self.demo_file_menu_hovered: bool = False

        # Simulated state for "calculator"
        self.calc_display: str = "0"
        self.calc_buffer: list[str] = []

        # Simulated state for "org.gnome.TextEditor"
        self.editor_title: str = ""
        self.editor_body: str = ""
        self.editor_saved: bool = False

    def list_apps(self) -> list[AppInfo]:
        """Return running simulated desktop applications."""
        return [
            AppInfo(app_id="demo.app", name="Demo Application", pid=1001),
            AppInfo(app_id="calculator", name="Calculator", pid=1002),
            AppInfo(app_id="org.gnome.TextEditor", name="Text Editor", pid=1003),
            AppInfo(app_id="system", name="System Settings", pid=1004),
        ]

    def _is_proceed_enabled(self) -> bool:
        """Proceed button dynamically enables once agreement text is typed."""
        return bool(self.demo_agreement_text.strip())

    def get_root(self, app_id: str) -> UiNode:
        """Return root UI node for the requested application."""
        if self.child_delay_ms > 0:
            time.sleep(self.child_delay_ms / 1000.0)

        if app_id == "deep.tree":
            # Nested tree of depth 15 for TC-U-013
            return self._build_deep_tree(current_depth=1, max_depth=15)

        if app_id == "wide.tree":
            # Wide tree of 1000 nodes for TC-U-014
            children = [
                UiNode(
                    control_id=f"item:{i}",
                    node_ref=f"ref_wide_{i}",
                    role="list_item",
                    name=f"Item {i}",
                    enabled=True,
                )
                for i in range(1000)
            ]
            return UiNode(
                control_id="wide_root",
                node_ref="ref_wide_root",
                role="window",
                name="Wide Window",
                children=children,
            )

        if app_id == "layout.prune.app":
            # 10 layout wrappers + 2 visible buttons for TC-U-016
            layout_children = [
                UiNode(
                    control_id=f"filler:{i}",
                    node_ref=f"ref_filler_{i}",
                    role="filler",
                    name="",
                    bounds=UiBounds(x=0, y=0, width=0, height=0),
                )
                for i in range(10)
            ]
            buttons = [
                UiNode(
                    control_id="btn:enabled",
                    node_ref="ref_btn_enabled",
                    role="button",
                    name="Enabled Button",
                    enabled=True,
                    bounds=UiBounds(x=10, y=10, width=80, height=30),
                ),
                UiNode(
                    control_id="btn:disabled",
                    node_ref="ref_btn_disabled",
                    role="button",
                    name="Disabled Button",
                    enabled=False,
                    bounds=UiBounds(x=10, y=50, width=80, height=30),
                ),
            ]
            return UiNode(
                control_id="prune_root",
                node_ref="ref_prune_root",
                role="window",
                name="Layout Prune Window",
                children=layout_children + buttons,
            )

        if app_id in ("demo.app", "test.app"):
            return self._build_demo_app_tree()

        if app_id == "calculator":
            return self._build_calculator_tree()

        if app_id == "org.gnome.TextEditor":
            return self._build_editor_tree()

        if app_id == "system":
            return UiNode(
                control_id="sys:win",
                node_ref="ref_sys_win",
                role="window",
                name="System Settings",
                children=[
                    UiNode(
                        control_id="btn:format",
                        node_ref="ref_format",
                        role="button",
                        name="Format Drive",
                        enabled=True,
                        bounds=UiBounds(x=100, y=100, width=120, height=35),
                    )
                ],
            )

        raise AppNotFoundError(app_id=app_id)

    def _build_deep_tree(self, current_depth: int, max_depth: int) -> UiNode:
        child = []
        if current_depth < max_depth:
            child = [self._build_deep_tree(current_depth + 1, max_depth)]
        return UiNode(
            control_id=f"node:d{current_depth}",
            node_ref=f"ref_d{current_depth}",
            role="container" if current_depth < max_depth else "button",
            name=f"Level {current_depth}",
            children=child,
        )

    def _build_demo_app_tree(self) -> UiNode:
        proceed_enabled = self._is_proceed_enabled()
        submit_name = "Sending..." if self.simulate_label_drift else "Submit"

        file_menu_children = []
        if self.demo_file_menu_hovered:
            file_menu_children = [
                UiNode(
                    control_id="menu:save",
                    node_ref="ref_menu_save",
                    role="menu_item",
                    name="Save",
                    menu_path=["File Menu", "Save"],
                    shortcut="Ctrl+S",
                    enabled=True,
                ),
                UiNode(
                    control_id="menu:export",
                    node_ref="ref_menu_export",
                    role="menu_item",
                    name="Export as PDF",
                    menu_path=["File Menu", "Export As", "Export as PDF"],
                    shortcut="Ctrl+Shift+E",
                    enabled=True,
                ),
            ]

        children = [
            UiNode(
                control_id="txt:entry",
                node_ref="ref_txt_entry",
                role="text_input",
                name="Agreement",
                value=self.demo_agreement_text,
                enabled=True,
                bounds=UiBounds(x=50, y=50, width=200, height=30),
            ),
            UiNode(
                control_id="btn:submit",
                node_ref="ref_btn_submit",
                role="button",
                name=submit_name,
                enabled=True,
                bounds=UiBounds(x=50, y=100, width=80, height=30),
            ),
            UiNode(
                control_id="btn:proceed",
                node_ref="ref_btn_proceed",
                role="button",
                name="Proceed",
                enabled=proceed_enabled,
                bounds=UiBounds(x=150, y=100, width=80, height=30),
            ),
            UiNode(
                control_id="menu:file",
                node_ref="ref_menu_file",
                role="menu",
                name="File Menu",
                menu_path=["File Menu"],
                expanded=self.demo_file_menu_hovered,
                children=file_menu_children,
                bounds=UiBounds(x=10, y=10, width=60, height=25),
            ),
            UiNode(
                control_id="chk:autosave",
                node_ref="ref_chk_autosave",
                role="checkbox",
                name="Auto-Save",
                checked=self.demo_auto_save,
                enabled=True,
                bounds=UiBounds(x=50, y=150, width=100, height=25),
            ),
            UiNode(
                control_id="combo:theme",
                node_ref="ref_combo_theme",
                role="dropdown",
                name="Theme",
                value=self.demo_theme,
                enabled=True,
                bounds=UiBounds(x=50, y=190, width=120, height=30),
            ),
            UiNode(
                control_id="scroll:doc",
                node_ref="ref_scroll_doc",
                role="scrollbar",
                name="Document View",
                value=str(self.demo_scroll_offset),
                enabled=True,
                bounds=UiBounds(x=200, y=150, width=150, height=100),
            ),
            UiNode(
                control_id="canvas:1",
                node_ref="ref_canvas_1",
                role="canvas",
                name="Canvas",
                enabled=True,
                bounds=UiBounds(x=100, y=100, width=200, height=200),
            ),
            UiNode(
                control_id="splitter:1",
                node_ref="ref_splitter_1",
                role="scrollbar",
                name="Splitter",
                enabled=True,
                bounds=UiBounds(x=95, y=100, width=10, height=200),
            ),
            UiNode(
                control_id="btn:delete",
                node_ref="ref_btn_delete",
                role="button",
                name="Delete File",
                enabled=True,
                bounds=UiBounds(x=50, y=320, width=100, height=30),
            ),
        ]

        return UiNode(
            control_id="demo_win",
            node_ref="ref_demo_win",
            role="window",
            name="Demo Application",
            children=children,
        )

    def _build_calculator_tree(self) -> UiNode:
        buttons = [
            UiNode(
                control_id="calc:display",
                node_ref="ref_calc_display",
                role="text_input",
                name="Display",
                value=self.calc_display,
                enabled=True,
                bounds=UiBounds(x=20, y=20, width=180, height=40),
            ),
            UiNode(
                control_id="calc:add",
                node_ref="ref_calc_add",
                role="button",
                name="+",
                enabled=True,
                bounds=UiBounds(x=160, y=70, width=40, height=40),
            ),
            UiNode(
                control_id="calc:equals",
                node_ref="ref_calc_equals",
                role="button",
                name="=",
                enabled=True,
                bounds=UiBounds(x=160, y=120, width=40, height=40),
            ),
        ]
        return UiNode(
            control_id="calc_win",
            node_ref="ref_calc_win",
            role="window",
            name="Calculator",
            children=buttons,
        )

    def _build_editor_tree(self) -> UiNode:
        saved_label = [
            UiNode(
                control_id="lbl:saved",
                node_ref="ref_lbl_saved",
                role="label",
                name="Saved",
                value="Document saved",
                enabled=True,
            )
        ] if self.editor_saved else []

        children = [
            UiNode(
                control_id="ed:title",
                node_ref="ref_ed_title",
                role="text_input",
                name="Title",
                value=self.editor_title,
                enabled=True,
                bounds=UiBounds(x=20, y=20, width=200, height=30),
            ),
            UiNode(
                control_id="ed:body",
                node_ref="ref_ed_body",
                role="text_input",
                name="Body",
                value=self.editor_body,
                enabled=True,
                bounds=UiBounds(x=20, y=60, width=200, height=100),
            ),
            UiNode(
                control_id="btn:save",
                node_ref="ref_btn_save",
                role="button",
                name="Save",
                enabled=True,
                bounds=UiBounds(x=20, y=170, width=80, height=30),
            ),
            UiNode(
                control_id="btn:delall",
                node_ref="ref_btn_delall",
                role="button",
                name="Delete All",
                enabled=True,
                bounds=UiBounds(x=110, y=170, width=80, height=30),
            ),
        ] + saved_label

        return UiNode(
            control_id="ed_win",
            node_ref="ref_ed_win",
            role="window",
            name="Text Editor",
            children=children,
        )

    def get_children(self, node_ref: str) -> list[UiNode]:
        """Fetch children of a UI node."""
        if self.child_delay_ms > 0:
            time.sleep(self.child_delay_ms / 1000.0)

        for app_info in self.list_apps():
            try:
                root = self.get_root(app_info.app_id)
                target = self._search_node_by_ref(root, node_ref)
                if target:
                    return target.children
            except (KeyError, AttributeError, ValueError, NodeNotFoundError):
                continue
        return []

    def _search_node_by_ref(self, current: UiNode, node_ref: str) -> UiNode | None:
        if current.node_ref == node_ref or current.control_id == node_ref:
            return current
        for c in current.children:
            match = self._search_node_by_ref(c, node_ref)
            if match:
                return match
        return None

    def read_node(self, node_ref: str) -> UiNode:
        """Read fresh node state."""
        for app_info in self.list_apps():
            try:
                root = self.get_root(app_info.app_id)
                target = self._search_node_by_ref(root, node_ref)
                if target:
                    return target
            except (KeyError, AttributeError, ValueError, NodeNotFoundError):
                continue
        raise NodeNotFoundError(node_ref=node_ref)

    def perform_action(
        self,
        node_ref: str,
        action: str,
        value: str | None = None,
        **kwargs: Any,
    ) -> str | None:
        """Perform semantic action on simulated UI node."""
        if node_ref in self.fail_node_actions:
            raise ActionExecutionError(
                action=action,
                control_id=node_ref,
                reason="Simulated action failure.",
            )

        self.click_log.append(f"{action}:{node_ref}")

        # Demo app operations
        if node_ref in ("ref_btn_submit", "btn:submit", "Submit"):
            self.demo_submit_count += 1
            return "ok"

        if node_ref in ("ref_btn_proceed", "btn:proceed", "Proceed"):
            if not self._is_proceed_enabled():
                raise ControlDisabledError(
                    app_id="demo.app",
                    control_id="Proceed",
                    reason="control is disabled/greyed out.",
                )
            return "proceed clicked"

        if action == "set_text":
            if node_ref in ("ref_txt_entry", "txt:entry", "Agreement"):
                self.demo_agreement_text = value or ""
                return "text set"
            if node_ref in ("ref_ed_title", "ed:title", "Title"):
                self.editor_title = value or ""
                return "title set"
            if node_ref in ("ref_ed_body", "ed:body", "Body"):
                self.editor_body = value or ""
                return "body set"
            if node_ref in ("ref_calc_display", "calc:display", "Display"):
                self.calc_display = value or ""
                self.calc_buffer.append(self.calc_display)
                return "display set"

        if action == "toggle" and node_ref in ("ref_chk_autosave", "chk:autosave", "Auto-Save"):
            self.demo_auto_save = not self.demo_auto_save
            return "toggled"

        if action == "select" and node_ref in ("ref_combo_theme", "combo:theme", "Theme"):
            self.demo_theme = value or "Light"
            return "selected"

        if action == "scroll":
            amount = int(kwargs.get("amount", 3))
            self.demo_scroll_offset += amount
            return "scrolled"

        if action == "click":
            if node_ref in ("ref_btn_save", "btn:save", "Save"):
                self.editor_saved = True
                return "saved"
            if node_ref in ("ref_calc_add", "calc:add", "+"):
                self.calc_buffer.append("+")
                return "+"
            if node_ref in ("ref_calc_equals", "calc:equals", "="):
                # Evaluate simple expression in buffer if present
                if "+" in self.calc_buffer and len(self.calc_buffer) >= 3:
                    try:
                        n1 = int(self.calc_buffer[0])
                        n2 = int(self.calc_buffer[2])
                        self.calc_display = str(n1 + n2)
                    except (ValueError, IndexError):
                        self.calc_display = "42"
                else:
                    self.calc_display = "30"
                return "="

        return "action performed"

    def hover(self, node_ref: str) -> None:
        """Hover over node, triggering menu expansion."""
        self.hover_log.append(node_ref)
        if node_ref in ("ref_menu_file", "menu:file", "File Menu"):
            self.demo_file_menu_hovered = True

    def click_coordinates(self, cx: int, cy: int) -> None:
        """Log absolute screen mouse click."""
        self.coordinate_clicks.append((cx, cy))

    def mouse_path(
        self,
        points: list[tuple[int, int]],
        button_down: bool = True,
    ) -> None:
        """Record continuous pointer motion path."""
        self.mouse_paths.append((points, button_down))

    def press_shortcut(self, shortcut: str) -> None:
        """Log keyboard shortcut dispatch."""
        self.shortcuts_pressed.append(shortcut)

    def key_combination(self, keys: list[str]) -> None:
        """Log key combination events."""
        self.keys_logged.append(keys)


@pytest.fixture
def fake_adapter() -> FakeAccessibilityAdapter:
    """Provide a fresh instance of FakeAccessibilityAdapter."""
    return FakeAccessibilityAdapter()


@pytest.fixture
def temp_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide an isolated temporary cache directory wired to JIT_MCP_CACHE_DIR."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("JIT_MCP_CACHE_DIR", str(cache_dir))
    return cache_dir
