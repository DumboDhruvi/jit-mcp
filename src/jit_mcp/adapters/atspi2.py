"""Linux AT-SPI2 accessibility adapter implementation.

Licensed under Apache-2.0.
"""

from __future__ import annotations

import re
import subprocess
import warnings
from typing import Any

from jit_mcp.adapters.base import (
    AccessibilityAdapter,
    AccessibilityDisabledWarning,
    ActionExecutionError,
    AdapterUnavailableError,
    AppNotFoundError,
    ControlDisabledError,
    NodeNotFoundError,
)
from jit_mcp.models import AppInfo, UiBounds, UiNode

# Optional top-level import with graceful fallback
try:
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi as _AtspiModule
except (ImportError, ValueError, AttributeError):
    _AtspiModule = None

try:
    import gi

    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk as _GdkModule
except (ImportError, ValueError, AttributeError):
    _GdkModule = None

_COMMON_KEYSYMS: dict[str, int] = {
    "control_l": 65507,
    "control_r": 65508,
    "shift_l": 65505,
    "shift_r": 65506,
    "alt_l": 65513,
    "alt_r": 65514,
    "super_l": 65515,
    "super_r": 65516,
    "return": 65293,
    "enter": 65293,
    "escape": 65307,
    "tab": 65289,
    "backspace": 65288,
    "delete": 65535,
    "insert": 65379,
    "home": 65360,
    "end": 65367,
    "page_up": 65365,
    "page_down": 65366,
    "up": 65362,
    "down": 65364,
    "left": 65361,
    "right": 65363,
    "space": 32,
}
for _i in range(1, 13):
    _COMMON_KEYSYMS[f"f{_i}"] = 65469 + _i


class AtSpi2Adapter:
    """Linux accessibility adapter backed by gi.repository.Atspi."""

    def __init__(self, check_a11y: bool = False, atspi_module: Any = None):
        """Initialize the AT-SPI2 adapter."""
        if atspi_module is not None:
            self._atspi = atspi_module
        else:
            try:
                import gi

                gi.require_version("Atspi", "2.0")
                from gi.repository import Atspi as loaded_atspi

                self._atspi = loaded_atspi
            except (ImportError, ValueError, AttributeError) as exc:
                raise AdapterUnavailableError(
                    "AT-SPI2 library is not available. Please ensure python3-gi and "
                    "gir1.2-atspi-2.0 are installed or recreate the virtual environment "
                    f"with '--system-site-packages'. Details: {exc}"
                ) from exc

        self._node_cache: dict[str, Any] = {}
        if check_a11y:
            self.check_accessibility_enabled()

    @classmethod
    def check_accessibility_enabled(cls) -> bool:
        """Check if system accessibility is enabled and warn if disabled."""
        enabled = True
        try:
            res = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "toolkit-accessibility"],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                output = res.stdout.strip().lower()
                if output == "false":
                    enabled = False
        except Exception:
            pass

        if not enabled:
            warnings.warn(
                "System accessibility (toolkit-accessibility) is currently disabled. "
                "Enable it with: gsettings set org.gnome.desktop.interface toolkit-accessibility true",
                AccessibilityDisabledWarning,
                stacklevel=2,
            )
            return False
        return True

    def _normalize_role(self, role_raw: Any, role_name: str = "") -> str:
        """Convert AT-SPI2 role enums and strings to standard role strings."""
        if self._atspi and hasattr(self._atspi, "Role"):
            role_enum = getattr(self._atspi, "Role")
            if role_raw == getattr(role_enum, "PUSH_BUTTON", None):
                return "button"
            if role_raw == getattr(role_enum, "COMBO_BOX", None):
                return "dropdown"
            if role_raw == getattr(role_enum, "CHECK_BOX", None):
                return "checkbox"
            if role_raw == getattr(role_enum, "RADIO_BUTTON", None):
                return "radio_button"
            if role_raw == getattr(role_enum, "SCROLL_BAR", None):
                return "scrollbar"
            if role_raw in (
                getattr(role_enum, "MENU_ITEM", None),
                getattr(role_enum, "CHECK_MENU_ITEM", None),
                getattr(role_enum, "RADIO_MENU_ITEM", None),
            ):
                return "menu_item"
            if role_raw == getattr(role_enum, "MENU_BAR", None):
                return "menu_bar"
            if role_raw == getattr(role_enum, "MENU", None):
                return "menu"
            if role_raw in (
                getattr(role_enum, "TEXT", None),
                getattr(role_enum, "ENTRY", None),
                getattr(role_enum, "PASSWORD_TEXT", None),
            ):
                return "text_input"
            if role_raw in (
                getattr(role_enum, "DRAWING_AREA", None),
                getattr(role_enum, "CANVAS", None),
            ):
                return "canvas"
            if role_raw in (
                getattr(role_enum, "PAGE_TAB", None),
                getattr(role_enum, "PAGE_TAB_LIST", None),
            ):
                return "tab"
            if role_raw == getattr(role_enum, "SLIDER", None):
                return "slider"
            if role_raw == getattr(role_enum, "LINK", None):
                return "link"
            if role_raw in (
                getattr(role_enum, "WINDOW", None),
                getattr(role_enum, "FRAME", None),
                getattr(role_enum, "DIALOG", None),
            ):
                return "window"
            if role_raw == getattr(role_enum, "APPLICATION", None):
                return "application"

        name = (role_name or str(role_raw)).lower().replace("-", "_").replace(" ", "_")
        if "push_button" in name or "button" in name or "toggle_button" in name:
            return "button"
        if "combo_box" in name or "combo" in name or "dropdown" in name:
            return "dropdown"
        if "check_box" in name or "checkbox" in name:
            return "checkbox"
        if "radio_button" in name or "radio" in name:
            return "radio_button"
        if "scroll_bar" in name or "scrollbar" in name:
            return "scrollbar"
        if "check_menu_item" in name or "radio_menu_item" in name or "menu_item" in name:
            return "menu_item"
        if "menu_bar" in name:
            return "menu_bar"
        if "menu" in name:
            return "menu"
        if any(k in name for k in ("text", "entry", "password_text", "document_text")):
            return "text_input"
        if "drawing_area" in name or "canvas" in name:
            return "canvas"
        if "page_tab" in name or "tab" in name:
            return "tab"
        if "slider" in name or "spin_button" in name:
            return "slider"
        if "link" in name:
            return "link"
        if "window" in name or "frame" in name or "dialog" in name:
            return "window"
        if "application" in name:
            return "application"
        if "filler" in name or "panel" in name or "separator" in name:
            return "panel"
        return name or "unknown"

    def _extract_bounds(self, accessible: Any) -> UiBounds | None:
        """Extract UiBounds from an accessible object."""
        raw_rect = None

        if hasattr(accessible, "get_extents"):
            try:
                coord_type = (
                    getattr(self._atspi.CoordType, "SCREEN", 0)
                    if hasattr(self._atspi, "CoordType")
                    else 0
                )
                raw_rect = accessible.get_extents(coord_type)
            except TypeError:
                try:
                    raw_rect = accessible.get_extents()
                except Exception:
                    pass
            except Exception:
                pass

        if raw_rect is None and hasattr(accessible, "get_component"):
            try:
                comp = accessible.get_component()
                if comp and hasattr(comp, "get_extents"):
                    coord_type = (
                        getattr(self._atspi.CoordType, "SCREEN", 0)
                        if hasattr(self._atspi, "CoordType")
                        else 0
                    )
                    raw_rect = comp.get_extents(coord_type)
            except Exception:
                pass

        if raw_rect is None:
            for attr in ("bounding_box", "bounds", "extents", "rect"):
                if hasattr(accessible, attr):
                    val = getattr(accessible, attr)
                    if callable(val):
                        try:
                            val = val()
                        except Exception:
                            continue
                    if val is not None:
                        raw_rect = val
                        break

        if raw_rect is None:
            return None

        if isinstance(raw_rect, (tuple, list)) and len(raw_rect) >= 4:
            try:
                x = int(raw_rect[0])
                y = int(raw_rect[1])
                width = int(raw_rect[2])
                height = int(raw_rect[3])
                if width < 0 or height < 0:
                    return None
                return UiBounds(
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                )
            except (ValueError, TypeError):
                return None

        if (
            hasattr(raw_rect, "x")
            and hasattr(raw_rect, "y")
            and hasattr(raw_rect, "width")
            and hasattr(raw_rect, "height")
        ):
            try:
                x = int(raw_rect.x)
                y = int(raw_rect.y)
                width = int(raw_rect.width)
                height = int(raw_rect.height)
                if width < 0 or height < 0:
                    return None
                return UiBounds(
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                )
            except (ValueError, TypeError):
                return None

        return None

    def _extract_states_and_enabled(
        self, accessible: Any
    ) -> tuple[list[str], bool, bool | None, bool | None]:
        """Extract state strings and enabled flag from accessible."""
        state_set = None
        if hasattr(accessible, "get_state_set"):
            try:
                state_set = accessible.get_state_set()
            except Exception:
                pass
        elif hasattr(accessible, "state_set"):
            state_set = accessible.state_set
        elif hasattr(accessible, "states"):
            state_set = accessible.states

        states: list[str] = []
        has_sensitive: bool = False
        checked: bool | None = None
        expanded: bool | None = None

        if state_set is not None:
            if hasattr(state_set, "contains"):
                sensitive_type = (
                    getattr(self._atspi.StateType, "SENSITIVE", None)
                    if hasattr(self._atspi, "StateType")
                    else None
                )
                checked_type = (
                    getattr(self._atspi.StateType, "CHECKED", None)
                    if hasattr(self._atspi, "StateType")
                    else None
                )
                expanded_type = (
                    getattr(self._atspi.StateType, "EXPANDED", None)
                    if hasattr(self._atspi, "StateType")
                    else None
                )

                if sensitive_type is not None:
                    try:
                        has_sensitive = bool(state_set.contains(sensitive_type))
                    except Exception:
                        pass
                if checked_type is not None:
                    try:
                        checked = bool(state_set.contains(checked_type))
                    except Exception:
                        pass
                if expanded_type is not None:
                    try:
                        expanded = bool(state_set.contains(expanded_type))
                    except Exception:
                        pass

            state_list = []
            if hasattr(state_set, "get_states"):
                try:
                    state_list = state_set.get_states()
                except Exception:
                    pass
            elif isinstance(state_set, (list, set, tuple)):
                state_list = state_set

            for s in state_list:
                s_name = str(s).lower()
                if hasattr(s, "value_name"):
                    s_name = s.value_name.lower()
                elif hasattr(s, "name"):
                    s_name = s.name.lower()
                states.append(s_name)
                if "sensitive" in s_name:
                    has_sensitive = True
                if "checked" in s_name:
                    checked = True
                if "expanded" in s_name:
                    expanded = True

        enabled = has_sensitive if state_set is not None else True
        return states, enabled, checked, expanded

    def _convert_accessible_to_ui_node(
        self, accessible: Any, app_id: str = "app", path: str = "0"
    ) -> UiNode:
        """Convert a native or mocked AT-SPI2 accessible into a UiNode."""
        name = ""
        if hasattr(accessible, "get_name"):
            try:
                name = accessible.get_name() or ""
            except Exception:
                pass
        elif hasattr(accessible, "name"):
            name = str(accessible.name or "")

        role_raw = None
        role_name = ""
        if hasattr(accessible, "get_role"):
            try:
                role_raw = accessible.get_role()
            except Exception:
                pass
        elif hasattr(accessible, "role"):
            role_raw = accessible.role

        if hasattr(accessible, "get_role_name"):
            try:
                role_name = accessible.get_role_name() or ""
            except Exception:
                pass
        elif hasattr(accessible, "role_name"):
            role_name = str(accessible.role_name or "")

        role = self._normalize_role(role_raw, role_name)
        bounds = self._extract_bounds(accessible)
        states, enabled, checked, expanded = self._extract_states_and_enabled(accessible)

        actions: list[str] = []
        if hasattr(accessible, "get_n_actions"):
            try:
                n = accessible.get_n_actions()
                if isinstance(n, int):
                    for i in range(n):
                        act_name = accessible.get_action_name(i)
                        if isinstance(act_name, str) and act_name:
                            actions.append(act_name)
            except Exception:
                pass
        elif hasattr(accessible, "actions") and isinstance(accessible.actions, (list, tuple)):
            actions = [str(a) for a in accessible.actions if a is not None]

        shortcut: str | None = None
        if hasattr(accessible, "get_key_binding"):
            try:
                kb = accessible.get_key_binding(0)
                if isinstance(kb, str) and kb.strip():
                    shortcut = kb.strip()
            except Exception:
                pass
        elif hasattr(accessible, "shortcut") and isinstance(accessible.shortcut, str):
            shortcut = accessible.shortcut.strip()

        clean_name = re.sub(r"[^\w\-_]", "_", name.strip()) if name else path
        control_id = f"{role}:{clean_name}"
        node_ref = f"atspi:{app_id}:{path}"
        self._node_cache[node_ref] = accessible

        return UiNode(
            control_id=control_id,
            node_ref=node_ref,
            role=role,
            name=name,
            enabled=enabled,
            states=states,
            actions=actions,
            checked=checked,
            expanded=expanded,
            shortcut=shortcut,
            bounds=bounds,
        )

    def list_apps(self) -> list[AppInfo]:
        """List running desktop applications exposed by AT-SPI2."""
        apps: list[AppInfo] = []
        try:
            desktop_count = self._atspi.get_desktop_count()
        except Exception:
            return apps

        for d_idx in range(desktop_count):
            try:
                desktop = self._atspi.get_desktop(d_idx)
                if not desktop:
                    continue
                n_children = desktop.get_child_count()
                for c_idx in range(n_children):
                    app_node = desktop.get_child_at_index(c_idx)
                    if not app_node:
                        continue
                    app_name = app_node.get_name() or f"app-{c_idx}"
                    pid = None
                    if hasattr(app_node, "get_process_id"):
                        try:
                            pid = app_node.get_process_id()
                        except Exception:
                            pass
                    toolkit = ""
                    if hasattr(app_node, "get_toolkit_name"):
                        try:
                            toolkit = app_node.get_toolkit_name() or ""
                        except Exception:
                            pass
                    version = ""
                    if hasattr(app_node, "get_version"):
                        try:
                            version = app_node.get_version() or ""
                        except Exception:
                            pass
                    apps.append(
                        AppInfo(
                            app_id=app_name,
                            name=app_name,
                            pid=pid,
                            toolkit=toolkit,
                            version=version,
                            platform="linux-atspi2",
                        )
                    )
            except Exception:
                continue

        return apps

    def _find_app_accessible(self, app_id: str) -> Any:
        """Locate the root accessible object for an application ID."""
        desktop_count = self._atspi.get_desktop_count()
        app_id_lower = app_id.lower()
        for d_idx in range(desktop_count):
            desktop = self._atspi.get_desktop(d_idx)
            if not desktop:
                continue
            n_children = desktop.get_child_count()
            for c_idx in range(n_children):
                app_node = desktop.get_child_at_index(c_idx)
                if not app_node:
                    continue
                name = (app_node.get_name() or "").lower()
                if app_id_lower == name or app_id_lower in name:
                    return app_node
        return None

    def get_root(self, app_id: str) -> UiNode:
        """Get the root UI accessibility node for the application."""
        accessible = self._find_app_accessible(app_id)
        if accessible is None:
            raise AppNotFoundError(app_id=app_id)
        return self._convert_accessible_to_ui_node(accessible, app_id=app_id, path="root")

    def get_children(self, node_ref: str) -> list[UiNode]:
        """Fetch immediate child UI nodes of the referenced node."""
        accessible = self._node_cache.get(node_ref)
        if accessible is None:
            raise NodeNotFoundError(node_ref=node_ref)

        children: list[UiNode] = []
        if not hasattr(accessible, "get_child_count"):
            return children

        try:
            n_children = accessible.get_child_count()
            parts = node_ref.split(":")
            app_id = parts[1] if len(parts) > 1 else "app"
            parent_path = parts[2] if len(parts) > 2 else "0"

            for i in range(n_children):
                child_acc = accessible.get_child_at_index(i)
                if child_acc is not None:
                    child_path = f"{parent_path}.{i}"
                    node = self._convert_accessible_to_ui_node(
                        child_acc, app_id=app_id, path=child_path
                    )
                    children.append(node)
        except Exception as exc:
            raise ActionExecutionError(
                action="get_children",
                control_id=node_ref,
                reason=str(exc),
            ) from exc

        return children

    def read_node(self, node_ref: str) -> UiNode:
        """Read fresh properties and states of the specified UI node."""
        accessible = self._node_cache.get(node_ref)
        if accessible is None:
            raise NodeNotFoundError(node_ref=node_ref)

        parts = node_ref.split(":")
        app_id = parts[1] if len(parts) > 1 else "app"
        path = parts[2] if len(parts) > 2 else "0"
        return self._convert_accessible_to_ui_node(accessible, app_id=app_id, path=path)

    def perform_action(
        self,
        node_ref: str,
        action: str,
        value: str | None = None,
        **kwargs: Any,
    ) -> str | None:
        """Perform a semantic action on a UI node."""
        accessible = self._node_cache.get(node_ref)
        if accessible is None:
            raise NodeNotFoundError(node_ref=node_ref)

        # Check enabled state
        _, enabled, _, _ = self._extract_states_and_enabled(accessible)
        if not enabled:
            parts = node_ref.split(":")
            app_id = parts[1] if len(parts) > 1 else ""
            raise ControlDisabledError(app_id=app_id, control_id=node_ref)

        action_lower = action.lower()
        try:
            if action_lower in ("click", "press", "activate"):
                success = False
                if hasattr(accessible, "do_action"):
                    for i in range(getattr(accessible, "get_n_actions", lambda: 1)()):
                        act_name = (
                            accessible.get_action_name(i).lower()
                            if hasattr(accessible, "get_action_name")
                            else ""
                        )
                        if act_name in ("click", "press", "activate", ""):
                            success = accessible.do_action(i)
                            if success:
                                break
                if not success:
                    # Fallback to coordinate click
                    bounds = self._extract_bounds(accessible)
                    if bounds and bounds.cx is not None and bounds.cy is not None:
                        self.click_coordinates(bounds.cx, bounds.cy)
                        return "clicked_coordinates"
                return "clicked"

            if action_lower == "hover":
                self.hover(node_ref)
                return "hovered"

            if action_lower == "set_text":
                if hasattr(accessible, "get_editable_text"):
                    editable = accessible.get_editable_text()
                    if editable and hasattr(editable, "set_text_contents"):
                        editable.set_text_contents(value or "")
                        return f"text_set:{value}"
                # Fallback: click to focus, then send keystrokes
                bounds = self._extract_bounds(accessible)
                if bounds and bounds.cx is not None and bounds.cy is not None:
                    self.click_coordinates(bounds.cx, bounds.cy)
                if value:
                    for char in value:
                        self.key_combination([char])
                return f"text_set:{value}"

            if action_lower == "toggle":
                if hasattr(accessible, "do_action"):
                    accessible.do_action(0)
                else:
                    bounds = self._extract_bounds(accessible)
                    if bounds and bounds.cx is not None and bounds.cy is not None:
                        self.click_coordinates(bounds.cx, bounds.cy)
                return "toggled"

            if action_lower == "select":
                # Option selection
                target_option = (value or kwargs.get("option", "")).strip()
                target_lower = target_option.lower()

                def _find_and_select_in_acc(acc: Any) -> bool:
                    if not hasattr(acc, "get_child_count"):
                        return False
                    n_children = acc.get_child_count()
                    for idx in range(n_children):
                        child = acc.get_child_at_index(idx)
                        if child is None:
                            continue
                        name = (child.get_name() or "").strip()
                        if name.lower() == target_lower:
                            if hasattr(child, "do_action"):
                                try:
                                    child.do_action(0)
                                    return True
                                except Exception:
                                    pass
                            if hasattr(acc, "get_selection"):
                                sel = acc.get_selection()
                                if sel and hasattr(sel, "select_child"):
                                    try:
                                        sel.select_child(idx)
                                        return True
                                    except Exception:
                                        pass
                            bounds = self._extract_bounds(child)
                            if bounds and bounds.cx is not None and bounds.cy is not None:
                                self.click_coordinates(bounds.cx, bounds.cy)
                                return True
                        # If child is menu / popup / container, search deeper
                        role_name = ""
                        if hasattr(child, "get_role_name"):
                            role_name = (child.get_role_name() or "").lower()
                        if "menu" in role_name or role_name in ("menu", "popup_menu", "panel", "combo_box"):
                            if _find_and_select_in_acc(child):
                                return True
                    return False

                if _find_and_select_in_acc(accessible):
                    return f"selected:{target_option}"

                if hasattr(accessible, "get_selection"):
                    sel = accessible.get_selection()
                    if sel and hasattr(sel, "select_child"):
                        if target_option.isdigit():
                            sel.select_child(int(target_option))
                            return f"selected:{target_option}"

                # Fallback: expand and click matching child via node refs
                children = self.get_children(node_ref)
                for c in children:
                    if c.name.strip().lower() == target_lower:
                        self.perform_action(c.node_ref, "click")
                        return f"selected:{target_option}"
                    try:
                        subchildren = self.get_children(c.node_ref)
                        for sc in subchildren:
                            if sc.name.strip().lower() == target_lower:
                                self.perform_action(sc.node_ref, "click")
                                return f"selected:{target_option}"
                    except Exception:
                        pass
                return f"selected:{target_option}"

            if action_lower == "scroll":
                direction = kwargs.get("direction", "down")
                amount = int(kwargs.get("amount", 1))
                # Mouse scroll or keys
                key = "Page_Down" if direction == "down" else "Page_Up"
                for _ in range(amount):
                    self.press_shortcut(key)
                return f"scrolled:{direction}:{amount}"

            raise ActionExecutionError(
                action=action,
                control_id=node_ref,
                reason=f"Unsupported action '{action}'",
            )
        except Exception as exc:
            if isinstance(exc, (ControlDisabledError, NodeNotFoundError, ActionExecutionError)):
                raise
            raise ActionExecutionError(
                action=action, control_id=node_ref, reason=str(exc)
            ) from exc

    def hover(self, node_ref: str) -> None:
        """Move cursor over the specified UI node."""
        accessible = self._node_cache.get(node_ref)
        if accessible is None:
            raise NodeNotFoundError(node_ref=node_ref)
        bounds = self._extract_bounds(accessible)
        if bounds and bounds.cx is not None and bounds.cy is not None:
            self._atspi.generate_mouse_event(bounds.cx, bounds.cy, "abs")

    def click_coordinates(self, cx: int, cy: int) -> None:
        """Synthesize a hardware mouse click at absolute screen coordinates."""
        self._atspi.generate_mouse_event(cx, cy, "abs")
        self._atspi.generate_mouse_event(cx, cy, "b1p")
        self._atspi.generate_mouse_event(cx, cy, "b1r")

    def mouse_path(self, points: list[tuple[int, int]], button_down: bool = True) -> None:
        """Trace a continuous mouse trajectory through absolute screen coordinates."""
        if not points:
            return

        x0, y0 = points[0]
        self._atspi.generate_mouse_event(x0, y0, "abs")
        if button_down:
            self._atspi.generate_mouse_event(x0, y0, "b1p")

        for x, y in points[1:]:
            self._atspi.generate_mouse_event(x, y, "abs")

        if button_down and points:
            xn, yn = points[-1]
            self._atspi.generate_mouse_event(xn, yn, "b1r")

    def press_shortcut(self, shortcut: str) -> None:
        """Synthesize keypresses for a human shortcut string (e.g. 'Ctrl+S')."""
        if not shortcut or not shortcut.strip():
            return

        raw_parts = [p.strip() for p in shortcut.split("+") if p.strip()]
        keys: list[str] = []
        for p in raw_parts:
            low = p.lower()
            if low in ("ctrl", "control"):
                keys.append("Control_L")
            elif low == "shift":
                keys.append("Shift_L")
            elif low == "alt":
                keys.append("Alt_L")
            elif low in ("super", "meta", "win"):
                keys.append("Super_L")
            elif len(p) == 1:
                keys.append(p.lower())
            else:
                keys.append(p)

        self.key_combination(keys)

    def key_combination(self, keys: list[str]) -> None:
        """Synthesize low-level key combination events for a list of key symbols."""
        if not keys:
            return

        resolved_keys: list[tuple[int, str]] = []
        for k in keys:
            keyval = 0
            if _GdkModule and hasattr(_GdkModule, "keyval_from_name"):
                try:
                    keyval = _GdkModule.keyval_from_name(k)
                except Exception:
                    keyval = 0
            if not keyval:
                keyval = _COMMON_KEYSYMS.get(k.lower(), 0)
            if not keyval and len(k) == 1:
                keyval = ord(k)
            resolved_keys.append((keyval, k))

        press_type = (
            getattr(self._atspi.KeySynthType, "PRESS", None)
            if hasattr(self._atspi, "KeySynthType")
            else None
        )
        release_type = (
            getattr(self._atspi.KeySynthType, "RELEASE", None)
            if hasattr(self._atspi, "KeySynthType")
            else None
        )

        for keyval, name in resolved_keys:
            if press_type is not None:
                self._atspi.generate_keyboard_event(keyval, name if not keyval else None, press_type)

        for keyval, name in reversed(resolved_keys):
            if release_type is not None:
                self._atspi.generate_keyboard_event(
                    keyval, name if not keyval else None, release_type
                )
