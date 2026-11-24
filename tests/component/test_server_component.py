"""Level 2 Component Tests: FastMCP Meta-Server & Dynamic Tool Registration.

Tests TC-C-005 through TC-C-012.
Licensed under Apache-2.0.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jit_mcp.adapters.base import (
    ArgumentResolutionError,
    SecurityBlockedError,
)
from jit_mcp.cache import save_tool_spec
from jit_mcp.models import (
    ActionStep,
    ScannerBudget,
    Selector,
    ToolAppInfo,
    ToolDefinition,
    ToolProvenance,
    ToolSafety,
    ToolSpec,
    UiBounds,
)
from jit_mcp.server import FastMcpServer

try:
    from tests.conftest import FakeAccessibilityAdapter
except ImportError:
    from conftest import FakeAccessibilityAdapter  # type: ignore[no-redef]


class TestFastMcpServerComponent:
    """Component tests covering FastMcpServer tools, session registry, dynamic registration, and safety."""

    def test_tc_c_005_server_list_tools_registers_complete_meta_tool_suite(
        self, fake_adapter: FakeAccessibilityAdapter, temp_cache_dir: Path
    ) -> None:
        """TC-C-005: FastMcpServer initializes with the complete core meta-tool suite registered."""
        server = FastMcpServer(adapter=fake_adapter, cache_dir=temp_cache_dir)

        tools = [t.name for t in server.list_tools()]
        expected_core_tools = [
            "list_desktop_apps",
            "scan_app_ui",
            "click_control",
            "hover_control",
            "set_text",
            "toggle_checkbox",
            "select_dropdown",
            "scroll_control",
            "draw_or_drag_path",
            "press_shortcut",
            "execute_action_batch",
            "save_workflow_tool",
            "call_compiled_tool",
        ]
        for tool_name in expected_core_tools:
            assert tool_name in tools, f"Core tool '{tool_name}' not found in list_tools()"

        # Underling FastMCP instance exists
        assert server.mcp is not None

    def test_tc_c_006_scan_app_ui_compact_view_and_session_registry(
        self, fake_adapter: FakeAccessibilityAdapter, temp_cache_dir: Path
    ) -> None:
        """TC-C-006: scan_app_ui returns compact view with ZERO coordinates, while registry holds UiBounds."""
        server = FastMcpServer(adapter=fake_adapter, cache_dir=temp_cache_dir)

        compact_view = server.scan_app_ui("calculator")

        assert "controls" in compact_view
        assert len(compact_view["controls"]) > 0

        # Compact controls must not leak coordinate numbers
        controls_json = json.dumps(compact_view["controls"])
        for coord_key in ('"x":', '"y":', '"width":', '"height":', '"cx":', '"cy":', '"bounds":'):
            assert coord_key not in controls_json, f"Leaked coordinate key in compact view: {coord_key}"

        # Server session registry must retain raw UiBounds
        assert "calculator" in server._scan_cache
        registry = server._scan_cache["calculator"].internal_registry
        assert any(node.bounds is not None for node in registry.values())

    def test_tc_c_006_server_preloads_cached_workflow_tools_on_startup(
        self, fake_adapter: FakeAccessibilityAdapter, temp_cache_dir: Path
    ) -> None:
        """TC-C-006 startup: Server preloads valid cached workflow tools from disk upon initialization."""
        # Pre-seed a valid ToolSpec into the cache directory
        spec = ToolSpec(
            app=ToolAppInfo(name="Text Editor", app_id="org.gnome.TextEditor", platform="linux-atspi2"),
            tool=ToolDefinition(name="preloaded_tool", description="Preloaded workflow tool", parameters={"type": "object", "properties": {}}),
            steps=[
                ActionStep(
                    step_id="step_1",
                    action="click",
                    target=Selector(role="button", name="Save"),
                    coordinate_fallback=UiBounds(x=10, y=10, width=50, height=30),
                )
            ],
            safety=ToolSafety(risk_tier="safe", requires_confirmation=False, reason="Safe"),
            provenance=ToolProvenance(
                compiled_at="2026-11-24T00:00:00Z",
                intent="preloaded tool",
                scanner_budget=ScannerBudget(max_depth=6, max_nodes=250, timeout_ms=3000),
                snapshot_hash="1122334455667788",
                compiler_version="0.1.0",
            ),
        )
        save_tool_spec(spec, cache_dir=temp_cache_dir)

        # Boot fresh server pointing to the same cache directory
        server = FastMcpServer(adapter=fake_adapter, cache_dir=temp_cache_dir)

        tools = [t.name for t in server.list_tools()]
        assert "texteditor_preloaded_tool" in tools

    def test_tc_c_007_save_workflow_tool_persists_registers_and_emits_notification(
        self, fake_adapter: FakeAccessibilityAdapter, temp_cache_dir: Path
    ) -> None:
        """TC-C-007: Compiling and saving a workflow tool persists it, registers it, and emits notification."""
        server = FastMcpServer(adapter=fake_adapter, cache_dir=temp_cache_dir)

        notifications: list[str] = []
        server.set_notification_handler(lambda n: notifications.append(n))

        steps = [
            {"action": "set_text", "control_id_or_name": "Title", "value": "Sample Document"},
            {"action": "click", "control_id_or_name": "Save"},
        ]

        result = server.save_workflow_tool(
            app_id="org.gnome.TextEditor",
            tool_name="create_note",
            steps=steps,
            intent="create a note",
        )

        assert result["status"] == "saved"
        assert result["tool_name"] == "texteditor_create_note"

        # 1. Saved to disk cache
        saved_file = temp_cache_dir / "org.gnome.TextEditor" / "create_note.json"
        assert saved_file.is_file()

        # 2. Registered on server
        tool_names = [t.name for t in server.list_tools()]
        assert "texteditor_create_note" in tool_names

        # 3. Notification emitted
        assert "notifications/tools/list_changed" in notifications

    def test_tc_c_008_call_compiled_tool_execution_and_missing_tool_error(
        self, fake_adapter: FakeAccessibilityAdapter, temp_cache_dir: Path
    ) -> None:
        """TC-C-008: Invoking a compiled tool executes workflow, or returns helpful error if missing."""
        server = FastMcpServer(adapter=fake_adapter, cache_dir=temp_cache_dir)

        # Missing tool raises ArgumentResolutionError suggesting save_workflow_tool
        with pytest.raises(ArgumentResolutionError) as exc_info:
            server.call_compiled_tool(app_id="calculator", tool_name="nonexistent_flow")
        assert "save_workflow_tool" in str(exc_info.value)

        # Save and execute a valid workflow
        steps = [{"action": "click", "control_id_or_name": "Save"}]
        server.save_workflow_tool(
            app_id="org.gnome.TextEditor",
            tool_name="quick_save",
            steps=steps,
            intent="save text editor document",
        )

        exec_res = server.call_compiled_tool(
            app_id="org.gnome.TextEditor",
            tool_name="quick_save",
        )
        assert exec_res is not None
        assert fake_adapter.editor_saved is True

    def test_tc_c_009_execute_action_batch_confirmation_challenge_halts_early(
        self, fake_adapter: FakeAccessibilityAdapter, temp_cache_dir: Path
    ) -> None:
        """TC-C-009: execute_action_batch halts immediately on unconfirmed destructive action."""
        server = FastMcpServer(adapter=fake_adapter, cache_dir=temp_cache_dir)

        batch = [
            {"action": "set_text", "control_id_or_name": "Agreement", "value": "Initial Draft"},
            {"action": "click", "control_id_or_name": "Delete File", "confirmed": False},
            {"action": "click", "control_id_or_name": "Submit"},
        ]

        res = server.execute_action_batch(app_id="demo.app", actions=batch, stop_on_error=True)

        assert res["success"] is False
        assert res["stopped_early"] is True
        assert res["total_steps"] == 3
        assert res["executed_steps"] == 2
        assert "requires confirmation" in str(res["error"])
        # Step 1 succeeded
        assert res["results"][0]["success"] is True
        # Step 2 failed on confirmation
        assert res["results"][1]["success"] is False
        # Step 3 was never executed
        assert len(res["results"]) == 2

    def test_tc_c_010_click_control_menu_path_expansion(
        self, fake_adapter: FakeAccessibilityAdapter, temp_cache_dir: Path
    ) -> None:
        """TC-C-010: click_control targeting leaf item with breadcrumbs expands menus."""
        server = FastMcpServer(adapter=fake_adapter, cache_dir=temp_cache_dir)

        # 1. Hover reveals menu hierarchy during scan stage
        server.hover_control("demo.app", "File Menu")
        assert fake_adapter.demo_file_menu_hovered is True
        server.scan_app_ui("demo.app")

        # 2. Simulate menu closing before action execution
        fake_adapter.demo_file_menu_hovered = False
        fake_adapter.click_log.clear()
        if "demo.app" in server._scan_cache:
            file_menu = server._scan_cache["demo.app"].internal_registry.get("File Menu")
            if file_menu:
                file_menu.expanded = False
        if server.executor and "demo.app" in server.executor._cached_registries:
            file_menu_exec = server.executor._cached_registries["demo.app"].get("File Menu")
            if file_menu_exec:
                file_menu_exec.expanded = False

        # 3. Clicking leaf item via breadcrumb auto-expands parent menu hierarchy
        server.click_control("demo.app", "File Menu > Export As > Export as PDF")

        assert "click:ref_menu_file" in fake_adapter.click_log
        assert "click:ref_menu_export" in fake_adapter.click_log

    def test_tc_c_011_click_control_confirmation_gating(
        self, fake_adapter: FakeAccessibilityAdapter, temp_cache_dir: Path
    ) -> None:
        """TC-C-011: Destructive click returns confirmation prompt without executing until confirmed."""
        server = FastMcpServer(adapter=fake_adapter, cache_dir=temp_cache_dir)

        # 1. Unconfirmed call pauses and returns challenge
        prompt = server.click_control(app_id="demo.app", control_id_or_name="Delete File", confirmed=False)
        assert isinstance(prompt, str)
        assert "requires confirmation" in prompt
        assert "click:ref_btn_delete" not in fake_adapter.click_log

        # 2. Confirmed call executes
        server.click_control(app_id="demo.app", control_id_or_name="Delete File", confirmed=True)
        assert any("Delete" in log or "delete" in log for log in fake_adapter.click_log)

    def test_tc_c_012_security_blocked_tier_rejection_on_click_and_save(
        self, fake_adapter: FakeAccessibilityAdapter, temp_cache_dir: Path
    ) -> None:
        """TC-C-012: Blocked tier actions are rejected unconditionally, even with confirmed=True."""
        server = FastMcpServer(adapter=fake_adapter, cache_dir=temp_cache_dir)

        # Blocked click
        with pytest.raises(SecurityBlockedError) as exc_info:
            server.click_control(app_id="system", control_id_or_name="Format Drive", confirmed=True)
        assert "blocked" in str(exc_info.value).lower()

        # Blocked workflow save
        with pytest.raises(SecurityBlockedError):
            server.save_workflow_tool(
                app_id="system",
                tool_name="format_disk",
                steps=[{"action": "click", "control_id_or_name": "Format Drive"}],
                intent="format drive and erase partition",
            )
