"""Unit tests for Compiler and ActionExecutor (TC-U-026..TC-U-033).

Licensed under Apache-2.0.
"""

from __future__ import annotations

import pytest

from jit_mcp.adapters.base import (
    ArgumentResolutionError,
    ControlDisabledError,
    NodeNotFoundError,
)
from jit_mcp.compiler import HeuristicCompilerBackend
from jit_mcp.executor import ActionExecutor
from jit_mcp.models import (
    ActionStep,
    AppInfo,
    BatchActionItem,
    ScanResult,
    Selector,
    ToolSpec,
    UiBounds,
    UiNode,
)
from tests.conftest import FakeAccessibilityAdapter


def test_tc_u_026_heuristic_compiler_workflow_generation() -> None:
    """TC-U-026: Compiles recorded sequence into ToolSpec with coordinate_fallback when bounds are present."""
    backend = HeuristicCompilerBackend()
    app = AppInfo(app_id="calculator", name="Calculator")

    recorded_steps = [
        {"action": "set_text", "name": "Display", "value": "10", "bounds": {"x": 10, "y": 20, "width": 100, "height": 30}},
        {"action": "click", "name": "Add", "role": "button", "bounds": {"x": 10, "y": 60, "width": 50, "height": 30}},
        {"action": "set_text", "name": "Display", "value": "20", "bounds": {"x": 10, "y": 20, "width": 100, "height": 30}},
        {"action": "click", "name": "Equals", "role": "button", "bounds": {"x": 70, "y": 60, "width": 50, "height": 30}},
    ]

    spec = backend.compile_workflow(app, recorded_steps, intent="add two numbers")
    assert isinstance(spec, ToolSpec)
    assert len(spec.steps) == 4

    for step in spec.steps:
        assert step.target.role
        assert step.coordinate_fallback is not None
        assert step.coordinate_fallback.cx is not None
        assert step.coordinate_fallback.cy is not None

    # Verify DEF-004: steps without bounds or scan_result yield coordinate_fallback=None
    steps_without_bounds = [{"action": "click", "name": "Clear", "role": "button"}]
    spec_no_bounds = backend.compile_workflow(app, steps_without_bounds, intent="clear calculator")
    assert spec_no_bounds.steps[0].coordinate_fallback is None


def test_tc_u_027_compiler_checkbox_postconditions() -> None:
    """TC-U-027: Compiler generates state_equals postcondition for checkbox toggle."""
    backend = HeuristicCompilerBackend()
    app = AppInfo(app_id="demo.app", name="Demo App")

    recorded_steps = [
        {"action": "toggle", "name": "Auto-Save", "role": "checkbox"}
    ]

    spec = backend.compile_workflow(app, recorded_steps, intent="toggle autosave")
    assert any(
        pc.type == "state_equals" and pc.expected == {"checked": True}
        for pc in spec.postconditions
    )


def test_tc_u_028_executor_refuses_disabled_control(fake_adapter: FakeAccessibilityAdapter) -> None:
    """TC-U-028: Refuses to click a disabled control and raises ControlDisabledError."""
    executor = ActionExecutor(adapter=fake_adapter)
    app = AppInfo(app_id="test.app", name="TestApp")

    disabled_node = UiNode(
        control_id="btn:submit",
        node_ref="ref_disabled_submit",
        role="button",
        name="Submit",
        enabled=False,
    )
    scan_result = ScanResult(
        app=app,
        controls=[],
        internal_registry={"btn:submit": disabled_node},
        snapshot_hash="12345678",
    )

    with pytest.raises(ControlDisabledError) as exc_info:
        executor.click(
            app_id="test.app",
            control_id_or_name="btn:submit",
            scan_result=scan_result,
        )

    err = exc_info.value
    assert err.control_id == "btn:submit"
    assert "Cannot click control 'btn:submit': control is disabled/greyed out." in str(err)


def test_tc_u_029_template_interpolation_safety() -> None:
    """TC-U-029: Interpolates {param} placeholders and raises on missing parameters."""
    executor = ActionExecutor(adapter=FakeAccessibilityAdapter())

    template = "Note: {title} - {body}"
    arguments = {"title": "Groceries", "body": "Milk & Eggs"}
    interpolated = executor._interpolate_template(template, arguments)
    assert interpolated == "Note: Groceries - Milk & Eggs"

    # Missing parameters raise ArgumentResolutionError
    with pytest.raises(ArgumentResolutionError):
        executor._interpolate_template("Note: {title} - {body}", {"title": "Groceries"})


def test_tc_u_030_semantic_miss_coordinate_fallback() -> None:
    """TC-U-030: Falls back to coordinate_fallback when semantic node lookup fails."""

    class MissingNodeAdapter(FakeAccessibilityAdapter):
        def perform_action(self, node_ref: str, action: str, value: str | None = None, **kwargs) -> str | None:
            raise NodeNotFoundError(node_ref=node_ref, app_id="test.app")

    adapter = MissingNodeAdapter()
    executor = ActionExecutor(adapter=adapter)

    step = ActionStep(
        step_id="step_1",
        action="click",
        target=Selector(role="button", name="IconBtn"),
        coordinate_fallback=UiBounds(x=100, y=300, width=40, height=80, cx=120, cy=340),
    )

    executor.execute_step(app_id="test.app", step=step)
    assert (120, 340) in adapter.coordinate_clicks


def test_tc_u_031_draw_or_drag_path_normalized_mapping() -> None:
    """TC-U-031: Maps normalized 0.0-1.0 waypoints inside UiBounds to concrete screen pixels."""
    adapter = FakeAccessibilityAdapter()
    executor = ActionExecutor(adapter=adapter)

    canvas_bounds = UiBounds(x=100, y=200, width=400, height=300)
    canvas_node = UiNode(
        control_id="canvas:1",
        node_ref="ref_canvas_1",
        role="canvas",
        name="Canvas",
        bounds=canvas_bounds,
    )
    scan_result = ScanResult(
        app=AppInfo(app_id="test.app", name="TestApp"),
        controls=[],
        internal_registry={"canvas:1": canvas_node},
        snapshot_hash="12345678",
    )

    normalized_points = [(0.1, 0.1), (0.5, 0.5), (0.9, 0.1)]
    executor.draw_or_drag_path(
        app_id="test.app",
        control_id_or_name="canvas:1",
        points=normalized_points,
        normalized=True,
        button_down=True,
        scan_result=scan_result,
    )

    assert len(adapter.mouse_paths) == 1
    points, button_down = adapter.mouse_paths[0]
    assert button_down is True
    # (100 + 40, 200 + 30) = (140, 230)
    # (100 + 200, 200 + 150) = (300, 350)
    # (100 + 360, 200 + 30) = (460, 230)
    assert points == [(140, 230), (300, 350), (460, 230)]


def test_tc_u_032_press_shortcut_dispatch() -> None:
    """TC-U-032: press_shortcut dispatches key events through the adapter."""
    adapter = FakeAccessibilityAdapter()
    executor = ActionExecutor(adapter=adapter)

    executor.press_shortcut(app_id="test.app", shortcut="Ctrl+S")
    assert "Ctrl+S" in adapter.shortcuts_pressed


def test_tc_u_033_execute_action_batch_early_stop() -> None:
    """TC-U-033: execute_action_batch halts immediately on error with partial result."""
    adapter = FakeAccessibilityAdapter()
    executor = ActionExecutor(adapter=adapter)

    actions = [
        BatchActionItem(action="set_text", control_id_or_name="txt:entry", value="Title"),
        BatchActionItem(action="click", control_id_or_name="NonExistentButton"),
        BatchActionItem(action="click", control_id_or_name="btn:submit"),
    ]

    result = executor.execute_action_batch("demo.app", actions, stop_on_error=True)

    assert result.success is False
    assert result.total_steps == 3
    assert result.executed_steps == 2
    assert result.stopped_early is True
    assert result.results[0].success is True
    assert result.results[1].success is False
    assert len(result.results) == 2
