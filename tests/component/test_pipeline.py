"""Level 2 Component Integration Tests: Pipeline & Multi-Module Flows.

Tests integration across Scanner, Registry, Safety Guardrail, Executor,
Heuristic Compiler, and Disk Cache without display server requirements.

Licensed under Apache-2.0.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from jit_mcp.adapters.base import (
    ControlDisabledError,
)
from jit_mcp.cache import ToolSpecCache
from jit_mcp.compiler import HeuristicCompilerBackend
from jit_mcp.executor import ActionExecutor
from jit_mcp.models import (
    ActionStep,
    AppInfo,
    BatchActionItem,
    Postcondition,
    Selector,
    UiBounds,
)
from jit_mcp.scanner import UiScanner


def test_two_stage_workflow_tc_c_001(fake_adapter: Any) -> None:
    """TC-C-001: Two-Stage scan -> compact LLM view -> semantic click -> state change."""
    scanner = UiScanner(fake_adapter)
    executor = ActionExecutor(fake_adapter, scanner=scanner)

    # 1. First stage: live scan
    scan_result = scanner.scan("demo.app")

    # Verify compact LLM view strictly hides pixel coordinates
    compact_view = scan_result.to_compact_llm_view()
    compact_json = json.dumps(compact_view)
    coordinate_matches = re.findall(
        r'\"(x|y|width|height|cx|cy|bounds)\"', compact_json
    )
    assert not coordinate_matches, f"Found coordinates in compact view: {coordinate_matches}"

    # Verify internal registry contains live UiBounds
    assert "btn:submit" in scan_result.internal_registry
    submit_node = scan_result.internal_registry["btn:submit"]
    assert submit_node.bounds is not None
    assert submit_node.bounds.cx is not None

    # 2. Second stage: semantic execution using resolved control
    assert fake_adapter.demo_submit_count == 0
    executor.click("demo.app", "btn:submit", scan_result=scan_result)
    assert fake_adapter.demo_submit_count == 1


def test_greyed_out_enforcement_and_rescan_flow_tc_c_002(
    fake_adapter: Any,
) -> None:
    """TC-C-002: Click on disabled control is refused, typing enables it, and re-scan permits click."""
    scanner = UiScanner(fake_adapter)
    executor = ActionExecutor(fake_adapter, scanner=scanner)

    # 1. Initial scan: Proceed button is disabled because agreement text is empty
    scan1 = scanner.scan("demo.app")
    proceed_ctrl1 = next(c for c in scan1.controls if c.control_id == "btn:proceed")
    assert proceed_ctrl1.enabled is False

    # Attempting to click disabled control raises ControlDisabledError
    with pytest.raises(ControlDisabledError) as exc_info:
        executor.click("demo.app", "Proceed", scan_result=scan1)
    assert "disabled" in str(exc_info.value).lower() or "greyed out" in str(exc_info.value).lower()

    # 2. Set agreement text to enable Proceed button
    executor.set_text("demo.app", "Agreement", "I AGREE", scan_result=scan1)
    assert fake_adapter.demo_agreement_text == "I AGREE"

    # 3. Dynamic re-scan reflects updated enabled state
    scan2 = scanner.scan("demo.app")
    proceed_ctrl2 = next(c for c in scan2.controls if c.control_id == "btn:proceed")
    assert proceed_ctrl2.enabled is True

    # 4. Click on newly enabled control succeeds
    click_res = executor.click("demo.app", "Proceed", scan_result=scan2)
    assert click_res == "proceed clicked"


def test_rich_controls_interaction_tc_c_003(
    fake_adapter: Any,
) -> None:
    """TC-C-003: Rich controls: hover, checkbox toggle, dropdown selection, and scrolling."""
    scanner = UiScanner(fake_adapter)
    executor = ActionExecutor(fake_adapter, scanner=scanner)
    scan_res = scanner.scan("demo.app")

    # 1. Hover reveals menu children
    assert fake_adapter.demo_file_menu_hovered is False
    executor.hover("demo.app", "File Menu", scan_result=scan_res)
    assert fake_adapter.demo_file_menu_hovered is True

    # 2. Toggle checkbox
    assert fake_adapter.demo_auto_save is False
    executor.toggle_checkbox("demo.app", "Auto-Save", scan_result=scan_res)
    assert fake_adapter.demo_auto_save is True

    # 3. Select dropdown combo box
    assert fake_adapter.demo_theme == "Light"
    executor.select_dropdown("demo.app", "Theme", "Dark", scan_result=scan_res)
    assert fake_adapter.demo_theme == "Dark"

    # 4. Scroll container
    assert fake_adapter.demo_scroll_offset == 0
    executor.scroll_control(
        "demo.app", "Document View", direction="down", amount=3, scan_result=scan_res
    )
    assert fake_adapter.demo_scroll_offset == 3


def test_draw_or_drag_path_canvas_and_divider_tc_c_004(
    fake_adapter: Any,
) -> None:
    """TC-C-004: Normalized waypoint canvas drawing and pixel split-pane dragging."""
    scanner = UiScanner(fake_adapter)
    executor = ActionExecutor(fake_adapter, scanner=scanner)
    scan_res = scanner.scan("demo.app")

    # 1. Canvas drawing with normalized coordinates: [0.0..1.0] inside UiBounds(x=100, y=100, w=200, h=200)
    # (0.0, 0.0) -> (100, 100), (1.0, 1.0) -> (300, 300)
    executor.draw_or_drag_path(
        "demo.app",
        "Canvas",
        points=[(0.0, 0.0), (1.0, 1.0)],
        normalized=True,
        button_down=True,
        scan_result=scan_res,
    )
    assert len(fake_adapter.mouse_paths) >= 1
    last_path, button_down = fake_adapter.mouse_paths[-1]
    assert button_down is True
    assert last_path == [(100, 100), (300, 300)]

    # 2. Splitter divider dragging with absolute pixel coordinates
    executor.draw_or_drag_path(
        "demo.app",
        "Splitter",
        points=[(100, 150), (100, 250)],
        normalized=False,
        button_down=True,
        scan_result=scan_res,
    )
    last_pixel_path, button_down2 = fake_adapter.mouse_paths[-1]
    assert button_down2 is True
    assert last_pixel_path == [(100, 150), (100, 250)]


def test_flattened_menu_path_auto_expansion_tc_c_010(
    fake_adapter: Any,
) -> None:
    """TC-C-010: Clicking leaf menu item with menu_path auto-expands parent menus."""
    scanner = UiScanner(fake_adapter)
    executor = ActionExecutor(fake_adapter, scanner=scanner)

    # 1. Hover reveals menu hierarchy during scan stage
    executor.hover("demo.app", "File Menu")
    assert fake_adapter.demo_file_menu_hovered is True
    scan_res = scanner.scan("demo.app")

    # 2. Simulate menu closing before action execution
    fake_adapter.demo_file_menu_hovered = False
    fake_adapter.click_log.clear()

    # 3. Clicking leaf item via breadcrumb or control_id auto-expands parent menu hierarchy
    executor.click(
        "demo.app",
        "File Menu > Export As > Export as PDF",
        scan_result=scan_res,
    )

    # Verify parent menu was expanded ('click:ref_menu_file') before leaf click ('click:ref_menu_export')
    assert "click:ref_menu_file" in fake_adapter.click_log
    assert "click:ref_menu_export" in fake_adapter.click_log


def test_execute_action_batch_with_early_safety_stop_tc_c_009(
    fake_adapter: Any,
) -> None:
    """TC-C-009: Action batching executes steps sequentially and halts early on confirmation challenge."""
    scanner = UiScanner(fake_adapter)
    executor = ActionExecutor(fake_adapter, scanner=scanner)

    batch: list[BatchActionItem] = [
        BatchActionItem(
            action="set_text",
            control_id_or_name="Agreement",
            value="I AGREE",
        ),
        BatchActionItem(
            action="click",
            control_id_or_name="Delete File",
            confirmed=False,  # Unconfirmed destructive action triggers CONFIRM risk tier
        ),
        BatchActionItem(
            action="click",
            control_id_or_name="Proceed",
            confirmed=True,
        ),
    ]

    result = executor.execute_action_batch(
        "demo.app", actions=batch, stop_on_error=True
    )

    assert result.success is False
    assert result.stopped_early is True
    # Step 0 succeeded, Step 1 failed on confirmation challenge, Step 2 was skipped
    assert result.executed_steps == 2
    assert result.results[0].success is True
    assert result.results[1].success is False
    assert "confirmation" in (result.error or "").lower()
    # Verify Step 0 state change took effect
    assert fake_adapter.demo_agreement_text == "I AGREE"


def test_coordinate_fallback_on_simulated_label_drift_tc_c_013(
    fake_adapter: Any,
) -> None:
    """TC-C-013: When control name drifts or semantic action fails, executor falls back to cached coordinates."""
    scanner = UiScanner(fake_adapter)
    executor = ActionExecutor(fake_adapter, scanner=scanner)

    # 1. Initial scan registers "btn:submit" with bounds cx=90, cy=115
    scan_res = scanner.scan("demo.app")
    submit_node = scan_res.internal_registry["btn:submit"]
    assert submit_node.bounds is not None
    cx, cy = submit_node.bounds.cx, submit_node.bounds.cy

    # 2. Simulate label drift and action execution failure
    fake_adapter.simulate_label_drift = True
    fake_adapter.fail_node_actions.add("ref_btn_submit")

    # 3. Executing click falls back to click_coordinates(cx, cy)
    executor.click("demo.app", "btn:submit", scan_result=scan_res)

    assert (cx, cy) in fake_adapter.coordinate_clicks


def test_heuristic_compiler_cache_and_workflow_roundtrip(
    fake_adapter: Any,
    temp_cache_dir: Path,
) -> None:
    """Verify CompilerBackend -> ToolSpecCache disk round-trip -> ActionExecutor.execute_workflow."""
    compiler = HeuristicCompilerBackend()
    cache = ToolSpecCache(cache_dir=temp_cache_dir)
    executor = ActionExecutor(fake_adapter)

    # 1. Define interactive action steps with template parameter
    recorded_steps: list[ActionStep] = [
        ActionStep(
            step_id="step_agree",
            action="set_text",
            target=Selector(role="text_input", name="Agreement"),
            value_template="{agreement_text}",
        ),
        ActionStep(
            step_id="step_proceed",
            action="click",
            target=Selector(role="button", name="Proceed"),
            coordinate_fallback=UiBounds(
                x=150, y=100, width=80, height=30, cx=190, cy=115
            ),
        ),
    ]

    postconditions = [
        Postcondition(
            type="element_exists",
            target=Selector(role="button", name="Proceed"),
        )
    ]

    app_info = AppInfo(app_id="demo.app", name="Demo Application")

    # 2. Compile to ToolSpec
    spec = compiler.compile_workflow(
        app_info=app_info,
        recorded_steps=recorded_steps,
        intent="agree to terms and proceed",
        tool_name="agree_and_proceed",
    )
    spec.postconditions = postconditions

    # 3. Persist to cache
    saved_path = cache.save(spec)
    assert saved_path.is_file()
    assert saved_path.name == "agree_and_proceed.json"
    assert "demo.app" in str(saved_path)

    # 4. Read back and validate
    loaded_spec = cache.get("demo.app", "agree_and_proceed")
    assert loaded_spec is not None
    assert loaded_spec.tool.name == "agree_and_proceed"
    assert len(loaded_spec.steps) == 2

    # 5. Execute compiled workflow with arguments
    exec_result = executor.execute_workflow(
        tool_spec=loaded_spec,
        arguments={"agreement_text": "I AGREE"},
        confirmed=True,
    )

    assert exec_result["status"] == "success"
    assert exec_result["executed_steps"] == 2
    assert fake_adapter.demo_agreement_text == "I AGREE"
