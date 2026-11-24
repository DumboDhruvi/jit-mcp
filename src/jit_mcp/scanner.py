"""Dynamic accessibility tree scanner enforcing budgets and generating dual-view scan results.

Licensed under Apache-2.0.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import time
from collections import deque
from typing import Any

from jit_mcp.adapters.base import AccessibilityAdapter
from jit_mcp.models import AppInfo, CompactControl, ScannerBudget, ScanResult, UiNode
from jit_mcp.safety import sanitize_ui_label

LAYOUT_CONTAINER_ROLES = {
    "filler",
    "panel",
    "frame",
    "scroll_pane",
    "root_pane",
    "layered_pane",
    "viewport",
    "split_pane",
    "box",
    "layout",
    "container",
    "grouping",
    "window",
    "application",
    "separator",
    "empty",
    "unknown",
    "section",
    "form",
}

INTERACTIVE_ROLES = {
    "button",
    "push_button",
    "toggle_button",
    "radio_button",
    "checkbox",
    "check_box",
    "check_menu_item",
    "radio_menu_item",
    "dropdown",
    "combo_box",
    "menu",
    "menu_item",
    "menu_bar",
    "text",
    "text_input",
    "entry",
    "password_text",
    "spin_button",
    "scrollbar",
    "scroll_bar",
    "slider",
    "tab",
    "page_tab",
    "tab_page",
    "canvas",
    "drawing_area",
    "link",
    "hyperlink",
    "tree_item",
    "list_item",
    "cell",
    "table_cell",
    "color_chooser",
    "file_chooser",
    "progress_bar",
    "status_bar",
    "tool_bar",
    "tooltip",
    "dialog",
    "alert",
}

MENU_CONTAINER_ROLES = {"menu", "menu_bar", "menubar", "popup_menu"}
MENU_ITEM_ROLES = {"menu_item", "check_menu_item", "radio_menu_item"}


def is_compact_control(node: UiNode) -> bool:
    """Determine whether a node should be included in the LLM-facing CompactControl view."""
    # Zero-size bounds indicate invisible, layout-only or offscreen elements
    if node.bounds is not None and (node.bounds.width <= 0 or node.bounds.height <= 0):
        return False

    role = node.role.strip().lower()

    # Layout/structural container roles are pruned unless they have explicit interactive actions
    if role in LAYOUT_CONTAINER_ROLES:
        return bool(node.actions)

    # Interactive controls are preserved
    if role in INTERACTIVE_ROLES:
        return True

    # Any node with explicit semantic actions is preserved
    if node.actions:
        return True

    # Non-empty named controls (e.g. labels, headings, static text) are preserved
    return bool(node.name and node.name.strip())


def extract_shortcut_and_name(node: UiNode) -> tuple[str | None, str]:
    """Extract keyboard shortcut hints and clean display name from a node."""
    shortcut = node.shortcut
    name = node.name or ""
    clean_name = name

    # Check for tab-delimited shortcut (common in GTK/AT-SPI: e.g. "Save\tCtrl+S")
    if "\t" in name:
        parts = name.split("\t", 1)
        clean_name = parts[0].strip()
        if not shortcut:
            shortcut = parts[1].strip()
    elif not shortcut and name:
        # Check for trailing shortcut in parentheses: e.g. "Save (Ctrl+S)" or "Export (Ctrl+Shift+E)"
        m = re.search(
            r"\(((?:Ctrl|Alt|Shift|Super|Cmd)(?:\+(?:Ctrl|Alt|Shift|Super|Cmd|[a-zA-Z0-9_]+))+)\)\s*$",
            name,
            re.IGNORECASE,
        )
        if m:
            shortcut = m.group(1).strip()
            clean_name = name[: m.start()].strip()

    return shortcut, clean_name


def compute_menu_path(
    node: UiNode, parent_menu_path: list[str], clean_name: str
) -> list[str]:
    """Calculate the flattened hierarchical menu breadcrumbs for nested menus and menu items."""
    if node.menu_path:
        return list(node.menu_path)

    role = node.role.strip().lower()
    name_to_use = clean_name.strip() if clean_name else (node.name.strip() if node.name else "")

    if role in MENU_ITEM_ROLES:
        if name_to_use:
            return parent_menu_path + [name_to_use]
        return list(parent_menu_path)

    if role in MENU_CONTAINER_ROLES:
        # Top-level menubars themselves do not form part of the item breadcrumb
        if role in {"menu_bar", "menubar"}:
            return list(parent_menu_path)
        if name_to_use:
            return parent_menu_path + [name_to_use]
        return list(parent_menu_path)

    return []


def compute_snapshot_hash(app: AppInfo, controls: list[CompactControl]) -> str:
    """Compute a deterministic SHA-256 snapshot hash over normalized scanned controls."""
    payload: dict[str, Any] = {
        "app_id": app.app_id,
        "controls": [
            {
                "control_id": c.control_id,
                "name": c.name,
                "role": c.role,
                "enabled": c.enabled,
                "checked": c.checked,
                "expanded": c.expanded,
                "value": c.value,
                "shortcut": c.shortcut,
                "menu_path": c.menu_path,
            }
            for c in controls
        ],
    }
    canonical_bytes = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def _make_unique_control_id(raw_id: str, seen: dict[str, int]) -> str:
    count = seen.get(raw_id, 0)
    seen[raw_id] = count + 1
    if count == 0:
        return raw_id
    return f"{raw_id}#{count + 1}"


class UiScanner:
    """Dynamic accessibility tree scanner enforcing budgets and generating dual-view scan results."""

    def __init__(
        self,
        adapter: AccessibilityAdapter,
        budget: ScannerBudget | None = None,
    ) -> None:
        self.adapter = adapter
        self.budget = budget or ScannerBudget(
            max_depth=6,
            max_nodes=250,
            timeout_ms=3000,
        )

    def _get_app_info(self, app_id: str) -> AppInfo:
        with contextlib.suppress(Exception):
            apps = self.adapter.list_apps()
            for app in apps:
                if app.app_id == app_id:
                    return app
        return AppInfo(
            app_id=app_id,
            name=app_id,
            window_title="",
            platform="linux-atspi2",
        )

    def _fetch_children(self, node: UiNode) -> list[UiNode]:
        """Fetch children from adapter protocol with fallback to node.children."""
        with contextlib.suppress(Exception):
            children = self.adapter.get_children(node.node_ref)
            if isinstance(children, (list, tuple)) and len(children) > 0:
                return list(children)
        if node.children:
            return list(node.children)
        return []

    def _to_compact(self, node: UiNode, clean_name: str) -> CompactControl:
        raw_name = clean_name if clean_name else node.name
        sanitized_name = sanitize_ui_label(raw_name) if raw_name else ""
        sanitized_val = (
            sanitize_ui_label(node.value) if node.value is not None else None
        )
        sanitized_shortcut = (
            sanitize_ui_label(node.shortcut) if node.shortcut is not None else None
        )
        sanitized_menu_path = [sanitize_ui_label(p) for p in node.menu_path]
        return CompactControl(
            control_id=node.control_id,
            name=sanitized_name,
            role=node.role,
            enabled=node.enabled,
            checked=node.checked,
            expanded=node.expanded,
            value=sanitized_val,
            shortcut=sanitized_shortcut,
            menu_path=sanitized_menu_path,
        )

    def _register_node(
        self,
        registry: dict[str, UiNode],
        node: UiNode,
        clean_name: str,
    ) -> None:
        """Register a node in internal_registry by control_id, name, and menu_path."""
        # 1. Primary unique ID lookup
        registry[node.control_id] = node

        # 2. Node reference lookup
        if node.node_ref and node.node_ref not in registry:
            registry[node.node_ref] = node

        # 3. Exact and normalized clean name lookups (prioritize interactive controls if collision)
        for name_candidate in (node.name, clean_name):
            if name_candidate:
                stripped = name_candidate.strip()
                if stripped:
                    if stripped not in registry or (
                        not is_compact_control(registry[stripped])
                        and is_compact_control(node)
                    ):
                        registry[stripped] = node
                    lower = stripped.lower()
                    if lower not in registry or (
                        not is_compact_control(registry[lower])
                        and is_compact_control(node)
                    ):
                        registry[lower] = node

        # 4. Breadcrumb menu path lookups
        if node.menu_path:
            path_str = " > ".join(node.menu_path)
            if path_str not in registry or is_compact_control(node):
                registry[path_str] = node
            lower_path = path_str.lower()
            if lower_path not in registry or is_compact_control(node):
                registry[lower_path] = node

        # 5. Automation ID lookup
        if node.automation_id and node.automation_id not in registry:
            registry[node.automation_id] = node

    def scan(
        self,
        app_id: str,
        budget: ScannerBudget | None = None,
    ) -> ScanResult:
        """Scan the application accessibility tree within budget constraints."""
        active_budget = budget or self.budget
        start_time = time.monotonic()
        timeout_sec = active_budget.timeout_ms / 1000.0
        deadline = start_time + timeout_sec

        app_info = self._get_app_info(app_id)

        root = self.adapter.get_root(app_id)
        if root is None:
            snapshot_hash = compute_snapshot_hash(app_info, [])
            return ScanResult(
                app=app_info,
                controls=[],
                internal_registry={},
                snapshot_hash=snapshot_hash,
                truncated=False,
                warnings=["Root accessibility node is None"],
            )

        controls: list[CompactControl] = []
        internal_registry: dict[str, UiNode] = {}
        warnings: list[str] = []
        truncated = False
        seen_cids: dict[str, int] = {}

        unique_root_id = _make_unique_control_id(root.control_id, seen_cids)
        shortcut, clean_name = extract_shortcut_and_name(root)
        root_menu_path = compute_menu_path(root, [], clean_name)
        root_copy = root.model_copy(
            update={
                "control_id": unique_root_id,
                "children": [],
                "shortcut": shortcut,
                "menu_path": root_menu_path,
            }
        )

        node_count = 1
        self._register_node(internal_registry, root_copy, clean_name)
        if unique_root_id != root.control_id and root.control_id not in internal_registry:
            internal_registry[root.control_id] = root_copy

        if is_compact_control(root):
            controls.append(self._to_compact(root_copy, clean_name))

        if node_count >= active_budget.max_nodes:
            # Reached max_nodes at root
            truncated = True
            warnings.append(
                f"Scan reached max_nodes budget of {active_budget.max_nodes}."
            )
        else:
            queue: deque[tuple[UiNode, UiNode, int, list[str]]] = deque(
                [(root, root_copy, 1, root_menu_path)]
            )

            while queue:
                if time.monotonic() >= deadline:
                    truncated = True
                    warnings.append(
                        f"Scan traversal timed out after {active_budget.timeout_ms}ms."
                    )
                    break

                orig_parent, copy_parent, depth, parent_menu_path = queue.popleft()

                # Strictly stop traversal at max_depth
                if depth >= active_budget.max_depth:
                    continue

                children = self._fetch_children(orig_parent)
                if not children:
                    continue

                for child in children:
                    if time.monotonic() >= deadline:
                        truncated = True
                        warnings.append(
                            f"Scan traversal timed out after {active_budget.timeout_ms}ms."
                        )
                        break

                    if node_count >= active_budget.max_nodes:
                        truncated = True
                        warnings.append(
                            f"Scan reached max_nodes budget of {active_budget.max_nodes}."
                        )
                        break

                    unique_child_id = _make_unique_control_id(
                        child.control_id, seen_cids
                    )
                    child_shortcut, child_clean_name = extract_shortcut_and_name(child)
                    child_menu_path = compute_menu_path(
                        child, parent_menu_path, child_clean_name
                    )

                    child_copy = child.model_copy(
                        update={
                            "control_id": unique_child_id,
                            "children": [],
                            "shortcut": child_shortcut,
                            "menu_path": child_menu_path,
                        }
                    )
                    copy_parent.children.append(child_copy)
                    node_count += 1

                    self._register_node(
                        internal_registry, child_copy, child_clean_name
                    )
                    if (
                        unique_child_id != child.control_id
                        and child.control_id not in internal_registry
                    ):
                        internal_registry[child.control_id] = child_copy

                    if is_compact_control(child):
                        controls.append(self._to_compact(child_copy, child_clean_name))

                    queue.append(
                        (child, child_copy, depth + 1, child_menu_path)
                    )

                if truncated:
                    break

        snapshot_hash = compute_snapshot_hash(app_info, controls)
        return ScanResult(
            app=app_info,
            controls=controls,
            internal_registry=internal_registry,
            snapshot_hash=snapshot_hash,
            truncated=truncated,
            warnings=warnings,
        )


__all__ = [
    "UiScanner",
    "compute_menu_path",
    "compute_snapshot_hash",
    "extract_shortcut_and_name",
    "is_compact_control",
]
