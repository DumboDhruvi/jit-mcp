"""Semantic Action Executor and Latency-Optimized Batching.

Licensed under Apache-2.0.
"""

from __future__ import annotations

import re
import time
from typing import Any

from jit_mcp.adapters.base import (
    AccessibilityAdapter,
    ActionExecutionError,
    ArgumentResolutionError,
    ConfirmationRequiredError,
    ControlDisabledError,
    NodeNotFoundError,
    PostconditionFailedError,
    SecurityBlockedError,
)
from jit_mcp.models import (
    ActionStep,
    BatchActionItem,
    BatchResult,
    BatchStepResult,
    Postcondition,
    SafetyTier,
    ScanResult,
    Selector,
    ToolSpec,
    UiNode,
)
from jit_mcp.safety import SafetyGuardrail


class ActionExecutor:
    """Executes semantic UI actions, latency batches, and compiled workflows safely."""

    def __init__(
        self,
        adapter: AccessibilityAdapter,
        safety: SafetyGuardrail | None = None,
        scanner: Any | None = None,
    ) -> None:
        self.adapter = adapter
        self.safety = safety or SafetyGuardrail()
        self.scanner = scanner
        # In-memory registry cache per application: app_id -> {id_or_name: UiNode}
        self._cached_registries: dict[str, dict[str, UiNode]] = {}

    def register_scan_result(self, scan_result: ScanResult) -> None:
        """Cache internal node registry from a ScanResult for fast resolution."""
        app_id = scan_result.app.app_id
        if app_id not in self._cached_registries:
            self._cached_registries[app_id] = {}
        for cid, node in scan_result.internal_registry.items():
            self._cached_registries[app_id][cid] = node
            if node.name:
                self._cached_registries[app_id][node.name] = node

    def resolve_control(
        self,
        app_id: str,
        control_id_or_name: str,
        scan_result: ScanResult | None = None,
    ) -> UiNode | None:
        """Resolve a UI node by control_id, accessible name, or menu path."""
        # 1. Check scan_result internal registry if supplied
        if scan_result and scan_result.internal_registry:
            self.register_scan_result(scan_result)
            reg = scan_result.internal_registry
            if control_id_or_name in reg:
                return reg[control_id_or_name]
            for node in reg.values():
                if node.control_id == control_id_or_name or node.name == control_id_or_name:
                    return node
                if node.name.lower() == control_id_or_name.lower():
                    return node
                if node.menu_path and " > ".join(node.menu_path) == control_id_or_name:
                    return node

        # 2. Check cached application registry
        cached = self._cached_registries.get(app_id, {})
        if control_id_or_name in cached:
            return cached[control_id_or_name]
        for node in cached.values():
            if node.control_id == control_id_or_name or node.name == control_id_or_name:
                return node
            if node.name.lower() == control_id_or_name.lower():
                return node
            if node.menu_path and " > ".join(node.menu_path) == control_id_or_name:
                return node

        # 3. Check scanner dynamically if available
        if self.scanner and hasattr(self.scanner, "scan"):
            try:
                res = self.scanner.scan(app_id)
                if isinstance(res, ScanResult):
                    self.register_scan_result(res)
                    return self.resolve_control(app_id, control_id_or_name, scan_result=res)
            except Exception:  # noqa: BLE001, S110
                pass

        # 4. Search live accessibility tree starting from root
        try:
            root = self.adapter.get_root(app_id)
            found = self._find_node_in_tree(root, control_id_or_name)
            if found:
                if app_id not in self._cached_registries:
                    self._cached_registries[app_id] = {}
                self._cached_registries[app_id][found.control_id] = found
                if found.name:
                    self._cached_registries[app_id][found.name] = found
                return found
        except Exception:  # noqa: BLE001, S110
            pass

        return None

    def _find_node_in_tree(self, current: UiNode, target: str) -> UiNode | None:
        """Recursive breadth/depth search for a control in a UiNode tree."""
        if current.control_id == target or current.name == target or current.node_ref == target:
            return current
        if current.name.lower() == target.lower():
            return current
        if current.menu_path and " > ".join(current.menu_path) == target:
            return current

        # Search existing children
        for child in current.children:
            match = self._find_node_in_tree(child, target)
            if match:
                return match

        # Try fetching children dynamically via adapter
        try:
            children = self.adapter.get_children(current.node_ref)
            for child in children:
                match = self._find_node_in_tree(child, target)
                if match:
                    return match
        except Exception:  # noqa: BLE001, S110
            pass

        return None

    def _expand_menu_hierarchy(self, app_id: str, menu_path: list[str]) -> None:
        """Expand all parent menus along a menu path so leaf items become accessible."""
        if len(menu_path) <= 1:
            return

        parents = menu_path[:-1]
        for parent_name in parents:
            parent_node = self.resolve_control(app_id, parent_name)
            if parent_node:
                try:
                    self.adapter.perform_action(parent_node.node_ref, "click")
                except Exception:  # noqa: BLE001
                    try:
                        self.adapter.hover(parent_node.node_ref)
                    except Exception:  # noqa: BLE001, S110
                        pass

    def _guard_and_enforce(
        self,
        action: str,
        app_id: str,
        control_id_or_name: str,
        node: UiNode | None,
        confirmed: bool,
        value: str | None = None,
    ) -> None:
        """Evaluate safety guardrails and verify the control is not greyed-out."""
        # 1. Safety Guardrail verification
        info: Any = node or control_id_or_name
        if value and isinstance(info, str):
            info = {"name": control_id_or_name, "intent": value}
        elif value and node is not None:
            info = {"name": node.name or node.control_id, "intent": value}

        decision = self.safety.check_action(
            action_name=action,
            control_info=info,
            confirmed=confirmed,
        )
        if decision.risk_tier == SafetyTier.BLOCKED:
            raise SecurityBlockedError(action=action, reason=decision.reason)
        if decision.risk_tier == SafetyTier.CONFIRM and not confirmed:
            raise ConfirmationRequiredError(action=action, reason=decision.reason)

        # 2. Disabled / Greyed-out check
        if node is not None:
            # Refresh live state if adapter supports read_node
            try:
                live_node = self.adapter.read_node(node.node_ref)
                if live_node:
                    node = live_node
            except Exception:  # noqa: BLE001, S110
                pass

            if node.enabled is False:
                raise ControlDisabledError(
                    app_id=app_id,
                    control_id=control_id_or_name,
                    reason="control is disabled/greyed out.",
                )

    def click(
        self,
        app_id: str,
        control_id_or_name: str,
        scan_result: ScanResult | None = None,
        confirmed: bool = False,
    ) -> str | None:
        """Click a control semantically, falling back to screen coordinates if needed."""
        # 1. Resolve target metadata / node first to guard before expanding menus
        node = self.resolve_control(app_id, control_id_or_name, scan_result=scan_result)

        self._guard_and_enforce(
            action="click",
            app_id=app_id,
            control_id_or_name=control_id_or_name,
            node=node,
            confirmed=confirmed,
        )

        # 2. Expand menu hierarchy if needed
        if " > " in control_id_or_name:
            parts = [p.strip() for p in control_id_or_name.split(">")]
            self._expand_menu_hierarchy(app_id, parts)
        elif node and node.menu_path:
            self._expand_menu_hierarchy(app_id, node.menu_path)

        # 3. If node is resolved, perform action
        if node is not None:
            try:
                return self.adapter.perform_action(node.node_ref, "click")
            except (NodeNotFoundError, ActionExecutionError):
                if node.bounds and node.bounds.cx is not None and node.bounds.cy is not None:
                    self.adapter.click_coordinates(node.bounds.cx, node.bounds.cy)
                    return None
                raise

        # 4. Node was not resolved semantically; check if cached bounds exist for fallback
        cached = self._cached_registries.get(app_id, {})
        cached_node = cached.get(control_id_or_name)
        if cached_node and cached_node.bounds and cached_node.bounds.cx is not None:
            if cached_node.enabled is False:
                raise ControlDisabledError(
                    app_id=app_id,
                    control_id=control_id_or_name,
                    reason="control is disabled/greyed out.",
                )
            self._guard_and_enforce(
                action="click",
                app_id=app_id,
                control_id_or_name=cached_node.name or control_id_or_name,
                node=cached_node,
                confirmed=confirmed,
            )
            self.adapter.click_coordinates(cached_node.bounds.cx, cached_node.bounds.cy)
            return None

        raise NodeNotFoundError(node_ref=control_id_or_name, app_id=app_id)

    def hover(
        self,
        app_id: str,
        control_id_or_name: str,
        scan_result: ScanResult | None = None,
        confirmed: bool = False,
    ) -> None:
        """Hover cursor over a UI control."""
        node = self.resolve_control(app_id, control_id_or_name, scan_result=scan_result)
        self._guard_and_enforce(
            action="hover",
            app_id=app_id,
            control_id_or_name=control_id_or_name,
            node=node,
            confirmed=confirmed,
        )

        if node is not None:
            self.adapter.hover(node.node_ref)
            return

        raise NodeNotFoundError(node_ref=control_id_or_name, app_id=app_id)

    def set_text(
        self,
        app_id: str,
        control_id_or_name: str,
        text: str,
        scan_result: ScanResult | None = None,
        confirmed: bool = False,
    ) -> str | None:
        """Set text on an editable input control."""
        node = self.resolve_control(app_id, control_id_or_name, scan_result=scan_result)
        self._guard_and_enforce(
            action="set_text",
            app_id=app_id,
            control_id_or_name=control_id_or_name,
            node=node,
            confirmed=confirmed,
            value=text,
        )

        if node is not None:
            return self.adapter.perform_action(node.node_ref, "set_text", value=text)

        raise NodeNotFoundError(node_ref=control_id_or_name, app_id=app_id)

    def toggle_checkbox(
        self,
        app_id: str,
        control_id_or_name: str,
        scan_result: ScanResult | None = None,
        confirmed: bool = False,
    ) -> str | None:
        """Toggle a checkbox control state."""
        node = self.resolve_control(app_id, control_id_or_name, scan_result=scan_result)
        self._guard_and_enforce(
            action="toggle",
            app_id=app_id,
            control_id_or_name=control_id_or_name,
            node=node,
            confirmed=confirmed,
        )

        if node is not None:
            return self.adapter.perform_action(node.node_ref, "toggle")

        raise NodeNotFoundError(node_ref=control_id_or_name, app_id=app_id)

    def select_dropdown(
        self,
        app_id: str,
        control_id_or_name: str,
        option: str,
        scan_result: ScanResult | None = None,
        confirmed: bool = False,
    ) -> str | None:
        """Select an option from a dropdown combo box."""
        node = self.resolve_control(app_id, control_id_or_name, scan_result=scan_result)
        self._guard_and_enforce(
            action="select",
            app_id=app_id,
            control_id_or_name=control_id_or_name,
            node=node,
            confirmed=confirmed,
            value=option,
        )

        if node is not None:
            return self.adapter.perform_action(node.node_ref, "select", value=option)

        raise NodeNotFoundError(node_ref=control_id_or_name, app_id=app_id)

    def scroll_control(
        self,
        app_id: str,
        control_id_or_name: str,
        direction: str = "down",
        amount: int = 3,
        scan_result: ScanResult | None = None,
        confirmed: bool = False,
    ) -> str | None:
        """Scroll a scrollable container or viewport."""
        node = self.resolve_control(app_id, control_id_or_name, scan_result=scan_result)
        self._guard_and_enforce(
            action="scroll",
            app_id=app_id,
            control_id_or_name=control_id_or_name,
            node=node,
            confirmed=confirmed,
        )

        if node is not None:
            return self.adapter.perform_action(
                node.node_ref, "scroll", direction=direction, amount=amount
            )

        raise NodeNotFoundError(node_ref=control_id_or_name, app_id=app_id)

    def draw_or_drag_path(
        self,
        app_id: str,
        control_id_or_name: str,
        points: list[tuple[float, float]] | list[tuple[int, int]],
        normalized: bool = True,
        button_down: bool = True,
        scan_result: ScanResult | None = None,
        confirmed: bool = False,
    ) -> None:
        """Trace continuous pointer waypoints inside a canvas or divider."""
        node = self.resolve_control(app_id, control_id_or_name, scan_result=scan_result)
        self._guard_and_enforce(
            action="draw_path",
            app_id=app_id,
            control_id_or_name=control_id_or_name,
            node=node,
            confirmed=confirmed,
        )

        if node is None or node.bounds is None:
            raise NodeNotFoundError(
                node_ref=control_id_or_name,
                app_id=app_id,
                message=f"Cannot draw/drag path: control '{control_id_or_name}' has no bounds.",
            )

        bounds = node.bounds
        pixel_points: list[tuple[int, int]] = []

        if normalized:
            for nx, ny in points:
                if not (0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0):
                    raise ActionExecutionError(
                        action="draw_path",
                        control_id=control_id_or_name,
                        reason=f"Normalized waypoint ({nx}, {ny}) is outside [0.0, 1.0] range.",
                    )
                px = round(bounds.x + nx * bounds.width)
                py = round(bounds.y + ny * bounds.height)
                pixel_points.append((px, py))
        else:
            tol = 5  # 5px tolerance
            for x, y in points:
                ix, iy = int(x), int(y)
                if not (
                    (bounds.x - tol) <= ix <= (bounds.x + bounds.width + tol)
                    and (bounds.y - tol) <= iy <= (bounds.y + bounds.height + tol)
                ):
                    raise ActionExecutionError(
                        action="draw_path",
                        control_id=control_id_or_name,
                        reason=f"Waypoint ({ix}, {iy}) lies outside control bounds {bounds}",
                    )
                pixel_points.append((ix, iy))

        self.adapter.mouse_path(pixel_points, button_down=button_down)

    def press_shortcut(
        self, app_id: str, shortcut: str, confirmed: bool = False
    ) -> None:
        """Synthesize key events for a keyboard shortcut string (e.g. 'Ctrl+S')."""
        self._guard_and_enforce(
            action="shortcut",
            app_id=app_id,
            control_id_or_name=shortcut,
            node=None,
            confirmed=confirmed,
            value=shortcut,
        )

        if hasattr(self.adapter, "press_shortcut"):
            self.adapter.press_shortcut(shortcut)
            return

        if hasattr(self.adapter, "key_combination"):
            # Map common shortcut tokens to X11/AT-SPI key names
            tokens = [t.strip() for t in shortcut.split("+")]
            key_map = {
                "ctrl": "Control_L",
                "control": "Control_L",
                "shift": "Shift_L",
                "alt": "Alt_L",
                "super": "Super_L",
                "meta": "Super_L",
            }
            mapped = [key_map.get(t.lower(), t) for t in tokens]
            self.adapter.key_combination(mapped)
            return

        raise ActionExecutionError(
            action="press_shortcut",
            reason="Adapter does not support shortcut key dispatch.",
        )

    def execute_action_batch(
        self,
        app_id: str,
        actions: list[BatchActionItem] | list[dict[str, Any]],
        stop_on_error: bool = True,
        scan_result: ScanResult | None = None,
    ) -> BatchResult:
        """Execute an ordered sequence of actions in a single turn with early stopping."""
        step_results: list[BatchStepResult] = []
        total_steps = len(actions)

        for idx, raw_action in enumerate(actions):
            if isinstance(raw_action, dict):
                item = BatchActionItem.model_validate(raw_action)
            else:
                item = raw_action

            target = item.control_id_or_name or ""
            act = item.action

            try:
                if act == "click":
                    self.click(app_id, target, scan_result=scan_result, confirmed=item.confirmed)
                elif act == "hover":
                    self.hover(app_id, target, scan_result=scan_result, confirmed=item.confirmed)
                elif act == "set_text":
                    self.set_text(
                        app_id,
                        target,
                        text=item.value or "",
                        scan_result=scan_result,
                        confirmed=item.confirmed,
                    )
                elif act == "toggle":
                    self.toggle_checkbox(
                        app_id, target, scan_result=scan_result, confirmed=item.confirmed
                    )
                elif act == "select":
                    self.select_dropdown(
                        app_id,
                        target,
                        option=item.option or item.value or "",
                        scan_result=scan_result,
                        confirmed=item.confirmed,
                    )
                elif act == "scroll":
                    self.scroll_control(
                        app_id,
                        target,
                        direction=item.direction or "down",
                        amount=item.amount,
                        scan_result=scan_result,
                        confirmed=item.confirmed,
                    )
                elif act == "draw_path":
                    self.draw_or_drag_path(
                        app_id,
                        target,
                        points=item.points or [],
                        normalized=item.normalized,
                        button_down=item.button_down,
                        scan_result=scan_result,
                        confirmed=item.confirmed,
                    )
                elif act == "shortcut":
                    self.press_shortcut(
                        app_id, item.shortcut or item.value or "", confirmed=item.confirmed
                    )
                else:
                    raise ActionExecutionError(action=act, control_id=target, reason=f"Unsupported action '{act}'")

                step_results.append(
                    BatchStepResult(
                        step_index=idx,
                        action=act,
                        control_id_or_name=target,
                        success=True,
                    )
                )

            except Exception as exc:  # noqa: BLE001
                step_results.append(
                    BatchStepResult(
                        step_index=idx,
                        action=act,
                        control_id_or_name=target,
                        success=False,
                        error=str(exc),
                    )
                )
                # DEF-007: Always stop immediately on SecurityBlockedError or ConfirmationRequiredError even if stop_on_error=False
                is_safety_error = isinstance(
                    exc, (SecurityBlockedError, ConfirmationRequiredError)
                )
                if stop_on_error or is_safety_error:
                    return BatchResult(
                        success=False,
                        total_steps=total_steps,
                        executed_steps=len(step_results),
                        results=step_results,
                        error=str(exc),
                        stopped_early=True,
                    )

        all_success = all(r.success for r in step_results)
        return BatchResult(
            success=all_success,
            total_steps=total_steps,
            executed_steps=len(step_results),
            results=step_results,
            stopped_early=False,
        )

    def _interpolate_template(
        self,
        template: str | None,
        arguments: dict[str, Any],
    ) -> str | None:
        """Safely interpolate parameters into a template string without recursive expansion."""
        if template is None:
            return None

        def _replace_match(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in arguments:
                raise ArgumentResolutionError(
                    arg_name=key,
                    reason=f"Missing required parameter '{key}' for template '{template}'.",
                )
            return str(arguments[key])

        # Single-pass substitution strictly prevents format injection and nested expansion
        return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", _replace_match, template)

    def execute_step(
        self,
        app_id: str,
        step: ActionStep,
        arguments: dict[str, Any] | None = None,
        live_tree: UiNode | None = None,
        confirmed: bool = False,
    ) -> Any:
        """Execute a single workflow ActionStep with semantic lookup and coordinate fallback."""
        args = arguments or {}
        interpolated_value = self._interpolate_template(step.value_template, args)

        target_name = step.target.name or ""
        if step.target.name_pattern and not target_name:
            target_name = step.target.name_pattern

        # DEF-004: Always evaluate _guard_and_enforce BEFORE resolving node or touching coordinate fallback
        self._guard_and_enforce(
            action=step.action,
            app_id=app_id,
            control_id_or_name=target_name or step.target.role,
            node=None,
            confirmed=confirmed,
            value=interpolated_value,
        )

        # 1. Attempt semantic resolution of target
        node: UiNode | None = None
        if live_tree:
            node = self._match_selector_in_tree(live_tree, step.target)

        if node is None and target_name:
            node = self.resolve_control(app_id, target_name)

        # 2. Try fallbacks if target was not resolved
        if node is None:
            for fallback_sel in step.fallbacks:
                if live_tree:
                    node = self._match_selector_in_tree(live_tree, fallback_sel)
                if node is None and fallback_sel.name:
                    node = self.resolve_control(app_id, fallback_sel.name)
                if node:
                    break

        # 3. If semantic resolution succeeded, verify node enabled and perform action
        if node is not None:
            self._guard_and_enforce(
                action=step.action,
                app_id=app_id,
                control_id_or_name=node.name or node.control_id,
                node=node,
                confirmed=confirmed,
                value=interpolated_value,
            )
            try:
                return self.adapter.perform_action(
                    node.node_ref,
                    step.action,
                    value=interpolated_value,
                )
            except (NodeNotFoundError, ActionExecutionError):
                pass  # Fall through to coordinate fallback

        # 4. Coordinate fallback engagement
        fallback_bounds = step.coordinate_fallback or (node.bounds if node else None)
        if fallback_bounds and fallback_bounds.cx is not None and fallback_bounds.cy is not None:
            self.adapter.click_coordinates(fallback_bounds.cx, fallback_bounds.cy)
            return None

        raise NodeNotFoundError(
            node_ref=target_name or step.target.role,
            app_id=app_id,
            message=f"Step '{step.step_id}' failed: target could not be resolved and no coordinate fallback succeeded.",
        )

    def _match_selector_in_tree(self, current: UiNode, selector: Selector) -> UiNode | None:
        """Match a Selector against a UiNode and its children."""
        matches = True
        if selector.role and current.role != selector.role:
            matches = False
        if selector.name and current.name != selector.name:
            matches = False
        if selector.name_pattern:
            if len(selector.name_pattern) > 128:
                raise ActionExecutionError(
                    action="match_selector",
                    reason=f"Selector name_pattern exceeds 128 characters: {selector.name_pattern[:32]}...",
                )
            try:
                if not re.search(selector.name_pattern, current.name):
                    matches = False
            except re.error as err:
                raise ActionExecutionError(
                    action="match_selector",
                    reason=f"Invalid regex in selector name_pattern: {err}",
                ) from err
        if selector.automation_id and current.automation_id != selector.automation_id:
            matches = False

        if matches:
            return current

        for child in current.children:
            match = self._match_selector_in_tree(child, selector)
            if match:
                return match

        return None

    def verify_postconditions(
        self,
        postconditions: list[Postcondition],
        adapter: AccessibilityAdapter | None = None,
        app_id: str | None = None,
    ) -> None:
        """Verify workflow postconditions within their allotted timeouts."""
        adp = adapter or self.adapter
        for idx, pc in enumerate(postconditions):
            deadline = time.monotonic() + (pc.timeout_ms / 1000.0)
            verified = False
            last_err = ""

            while time.monotonic() <= deadline:
                try:
                    root = adp.get_root(app_id) if app_id else None
                    target_node = self._match_selector_in_tree(root, pc.target) if root else None

                    if pc.type == "element_exists":
                        if target_node is not None:
                            verified = True
                            break
                        last_err = f"Target element with role '{pc.target.role}' does not exist."

                    elif pc.type == "element_absent":
                        if target_node is None:
                            verified = True
                            break
                        last_err = f"Target element with role '{pc.target.role}' is still present."

                    elif pc.type == "text_contains":
                        if target_node and pc.expected and str(pc.expected) in (target_node.value or target_node.name):
                            verified = True
                            break
                        last_err = f"Expected text '{pc.expected}' not found in target."

                    elif pc.type == "state_equals":
                        if target_node and pc.expected is not None:
                            if isinstance(pc.expected, dict):
                                match_dict = all(
                                    getattr(target_node, k, None) == v
                                    for k, v in pc.expected.items()
                                )
                                if match_dict:
                                    verified = True
                                    break
                            elif target_node.checked == pc.expected or target_node.expanded == pc.expected:
                                verified = True
                                break
                        last_err = f"Target state did not match expected '{pc.expected}'."

                    elif pc.type == "value_equals":
                        if target_node and target_node.value == str(pc.expected):
                            verified = True
                            break
                        last_err = f"Target value '{getattr(target_node, 'value', None)}' != '{pc.expected}'."

                except Exception as exc:  # noqa: BLE001
                    last_err = str(exc)

                time.sleep(0.05)

            if not verified:
                raise PostconditionFailedError(
                    condition=pc.type,
                    step_index=idx,
                    reason=last_err,
                )

    def execute_workflow(
        self,
        tool_spec: ToolSpec,
        arguments: dict[str, Any],
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Execute a full compiled ToolSpec workflow deterministically."""
        app_id = tool_spec.app.app_id

        # Safety enforcement on the whole workflow tool
        decision = self.safety.classify_action(
            action=tool_spec.tool.name,
            control_name="",
            intent=tool_spec.provenance.intent,
        )
        if decision.risk_tier == SafetyTier.BLOCKED:
            raise SecurityBlockedError(action=tool_spec.tool.name, reason=decision.reason)
        if decision.risk_tier == SafetyTier.CONFIRM and not confirmed:
            raise ConfirmationRequiredError(action=tool_spec.tool.name, reason=decision.reason)

        live_tree: UiNode | None = None
        try:
            live_tree = self.adapter.get_root(app_id)
        except Exception:  # noqa: BLE001, S110
            pass

        for step in tool_spec.steps:
            self.execute_step(
                app_id=app_id,
                step=step,
                arguments=arguments,
                live_tree=live_tree,
                confirmed=confirmed,
            )

        self.verify_postconditions(
            tool_spec.postconditions,
            adapter=self.adapter,
            app_id=app_id,
        )

        return {
            "status": "success",
            "tool": tool_spec.tool.name,
            "executed_steps": len(tool_spec.steps),
        }


__all__ = ["ActionExecutor"]
