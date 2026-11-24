"""Level 3 E2E test suite: Real MCP stdio client-to-server lifecycle (TC-E-001..TC-E-004).

Exercises the complete jit-mcp system across real stdio subprocess boundaries:
- TC-E-001: Initialization, capabilities, app listing, and compact UI scan with ZERO coordinates.
- TC-E-002: Multi-turn interaction over stdio (inspect -> greyed out -> set text -> enabled -> click).
- TC-E-003: Workflow persistence across server restart over stdio.
- TC-E-004: End-to-end confirmation gating for destructive actions across stdio.

Licensed under Apache-2.0.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# 15-second per-action timeout to prevent hung subprocesses
STDIO_TIMEOUT_SECONDS = 15.0


@asynccontextmanager
async def spawn_mcp_stdio_session(
    cache_dir: Path | str,
) -> AsyncIterator[ClientSession]:
    """Launch a real jit-mcp stdio subprocess server and yield an active ClientSession."""
    server_script = Path(__file__).parent / "stdio_fake_server.py"
    env = os.environ.copy()
    env["JIT_MCP_CACHE_DIR"] = str(cache_dir)
    # Ensure src and root are in PYTHONPATH for subprocess
    repo_root = Path(__file__).resolve().parent.parent.parent
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{repo_root / 'src'}:{repo_root}:{existing_pythonpath}"
        if existing_pythonpath
        else f"{repo_root / 'src'}:{repo_root}"
    )

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(server_script)],
        env=env,
    )

    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        yield session


def _parse_content_json(result: Any) -> Any:
    """Extract and parse JSON from an MCP CallToolResult or return structured content."""
    if hasattr(result, "structured_content") and result.structured_content:
        return result.structured_content
    if hasattr(result, "content") and result.content:
        first_item = result.content[0]
        text_val = getattr(first_item, "text", "")
        try:
            return json.loads(text_val)
        except (json.JSONDecodeError, TypeError):
            return text_val
    return None


@pytest.mark.anyio
async def test_tc_e_001_stdio_initialization_and_scan(tmp_path: Path) -> None:
    """TC-E-001: Subprocess initialization, tools.listChanged capability, app listing & scan without coordinates."""
    async with spawn_mcp_stdio_session(cache_dir=tmp_path) as session:
        # 1. Standard MCP initialize handshake
        init_res = await asyncio.wait_for(
            session.initialize(), timeout=STDIO_TIMEOUT_SECONDS
        )
        assert init_res.capabilities.tools is not None
        assert init_res.capabilities.tools.list_changed is True

        # 2. list_desktop_apps
        apps_call = await asyncio.wait_for(
            session.call_tool("list_desktop_apps", arguments={}),
            timeout=STDIO_TIMEOUT_SECONDS,
        )
        assert apps_call.is_error is False
        apps_data = _parse_content_json(apps_call)
        apps_list = (
            apps_data.get("result", apps_data)
            if isinstance(apps_data, dict)
            else apps_data
        )
        app_ids = [a["app_id"] for a in apps_list]
        assert "demo.app" in app_ids
        assert "calculator" in app_ids
        assert "org.gnome.TextEditor" in app_ids

        # 3. scan_app_ui("demo.app")
        scan_call = await asyncio.wait_for(
            session.call_tool("scan_app_ui", arguments={"app_id": "demo.app"}),
            timeout=STDIO_TIMEOUT_SECONDS,
        )
        assert scan_call.is_error is False
        scan_json = _parse_content_json(scan_call)

        assert "controls" in scan_json
        controls = scan_json["controls"]
        assert len(controls) > 0

        # Verify ZERO raw coordinates in LLM compact view
        forbidden_keys = {"x", "y", "width", "height", "cx", "cy", "bounds"}
        for ctrl in controls:
            for bad_key in forbidden_keys:
                assert (
                    bad_key not in ctrl
                ), f"Raw coordinate key '{bad_key}' leaked in compact control: {ctrl}"
            assert "control_id" in ctrl
            assert "role" in ctrl
            assert "enabled" in ctrl


@pytest.mark.anyio
async def test_tc_e_002_stdio_multiturn_interaction(tmp_path: Path) -> None:
    """TC-E-002: Multi-turn interaction over stdio: scan -> disabled click rejected -> set_text -> enabled click."""
    async with spawn_mcp_stdio_session(cache_dir=tmp_path) as session:
        await asyncio.wait_for(
            session.initialize(), timeout=STDIO_TIMEOUT_SECONDS
        )

        # 1. scan_app_ui -> observe Proceed button is disabled
        scan1 = await asyncio.wait_for(
            session.call_tool("scan_app_ui", arguments={"app_id": "demo.app"}),
            timeout=STDIO_TIMEOUT_SECONDS,
        )
        assert scan1.is_error is False
        scan1_data = _parse_content_json(scan1)
        proceed_ctrl = next(
            c for c in scan1_data["controls"] if c.get("name") == "Proceed"
        )
        assert proceed_ctrl["enabled"] is False

        # 2. click_control on disabled Proceed fails / is rejected
        click1 = await asyncio.wait_for(
            session.call_tool(
                "click_control",
                arguments={"app_id": "demo.app", "control_id_or_name": "Proceed"},
            ),
            timeout=STDIO_TIMEOUT_SECONDS,
        )
        assert click1.is_error is True or "disabled" in str(click1.content)

        # 3. set_text on Agreement input field
        set_res = await asyncio.wait_for(
            session.call_tool(
                "set_text",
                arguments={
                    "app_id": "demo.app",
                    "control_id_or_name": "Agreement",
                    "text": "I AGREE",
                },
            ),
            timeout=STDIO_TIMEOUT_SECONDS,
        )
        assert set_res.is_error is False

        # 4. re-scan shows Proceed button now enabled
        scan2 = await asyncio.wait_for(
            session.call_tool("scan_app_ui", arguments={"app_id": "demo.app"}),
            timeout=STDIO_TIMEOUT_SECONDS,
        )
        assert scan2.is_error is False
        scan2_data = _parse_content_json(scan2)
        proceed_ctrl_updated = next(
            c for c in scan2_data["controls"] if c.get("name") == "Proceed"
        )
        assert proceed_ctrl_updated["enabled"] is True

        # 5. click_control on Proceed button now succeeds
        click2 = await asyncio.wait_for(
            session.call_tool(
                "click_control",
                arguments={"app_id": "demo.app", "control_id_or_name": "Proceed"},
            ),
            timeout=STDIO_TIMEOUT_SECONDS,
        )
        assert click2.is_error is False
        assert (
            "proceed clicked" in str(click2.content).lower()
            or "ok" in str(click2.content).lower()
        )


@pytest.mark.anyio
async def test_tc_e_003_stdio_workflow_tool_persistence_across_restart(
    tmp_path: Path,
) -> None:
    """TC-E-003: Workflow tool saved in session 1 persists to disk and is immediately executable in session 2."""
    cache_dir = tmp_path / "jit_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # --- Session 1: Save workflow tool ---
    async with spawn_mcp_stdio_session(cache_dir=cache_dir) as session1:
        await asyncio.wait_for(
            session1.initialize(), timeout=STDIO_TIMEOUT_SECONDS
        )

        steps = [
            {"action": "click", "control_id_or_name": "+"},
            {"action": "click", "control_id_or_name": "="},
        ]
        save_res = await asyncio.wait_for(
            session1.call_tool(
                "save_workflow_tool",
                arguments={
                    "app_id": "calculator",
                    "tool_name": "add_numbers",
                    "steps": steps,
                    "intent": "add numbers on calculator",
                },
            ),
            timeout=STDIO_TIMEOUT_SECONDS,
        )
        assert save_res.is_error is False
        save_data = _parse_content_json(save_res)
        assert save_data["status"] == "saved"
        assert save_data["tool_name"] == "calculator_add_numbers"

        # Verify persisted on disk in JIT_MCP_CACHE_DIR
        persisted_file = cache_dir / "calculator" / "add_numbers.json"
        assert persisted_file.is_file()

        # Verify listed in tools/list in session 1
        tools1 = await asyncio.wait_for(
            session1.list_tools(), timeout=STDIO_TIMEOUT_SECONDS
        )
        tool_names1 = [t.name for t in tools1.tools]
        assert "calculator_add_numbers" in tool_names1

    # --- Session 2: Terminate session 1 and start fresh process ---
    async with spawn_mcp_stdio_session(cache_dir=cache_dir) as session2:
        await asyncio.wait_for(
            session2.initialize(), timeout=STDIO_TIMEOUT_SECONDS
        )

        # Tool is ALREADY available at startup without re-saving
        tools2 = await asyncio.wait_for(
            session2.list_tools(), timeout=STDIO_TIMEOUT_SECONDS
        )
        tool_names2 = [t.name for t in tools2.tools]
        assert "calculator_add_numbers" in tool_names2

        # Executing the preloaded workflow tool succeeds cleanly
        exec_res = await asyncio.wait_for(
            session2.call_tool("calculator_add_numbers", arguments={}),
            timeout=STDIO_TIMEOUT_SECONDS,
        )
        assert exec_res.is_error is False
        exec_text = str(exec_res.content)
        assert "success" in exec_text.lower() or "status" in exec_text.lower()


@pytest.mark.anyio
async def test_tc_e_004_stdio_destructive_action_confirmation_gating(
    tmp_path: Path,
) -> None:
    """TC-E-004: Destructive action over stdio enforces confirmation prompt/error and proceeds when confirmed=True."""
    async with spawn_mcp_stdio_session(cache_dir=tmp_path) as session:
        await asyncio.wait_for(
            session.initialize(), timeout=STDIO_TIMEOUT_SECONDS
        )

        # Pre-scan demo.app to populate cache
        await asyncio.wait_for(
            session.call_tool("scan_app_ui", arguments={"app_id": "demo.app"}),
            timeout=STDIO_TIMEOUT_SECONDS,
        )

        # 1. confirmed=False -> returns confirmation challenge / prompt
        unconfirmed_call = await asyncio.wait_for(
            session.call_tool(
                "click_control",
                arguments={
                    "app_id": "demo.app",
                    "control_id_or_name": "Delete File",
                    "confirmed": False,
                },
            ),
            timeout=STDIO_TIMEOUT_SECONDS,
        )
        unconfirmed_text = str(unconfirmed_call.content)
        assert (
            "requires confirmation" in unconfirmed_text.lower()
            or unconfirmed_call.is_error is True
        )
        assert "delete" in unconfirmed_text.lower()

        # 2. confirmed=True -> execution proceeds successfully
        confirmed_call = await asyncio.wait_for(
            session.call_tool(
                "click_control",
                arguments={
                    "app_id": "demo.app",
                    "control_id_or_name": "Delete File",
                    "confirmed": True,
                },
            ),
            timeout=STDIO_TIMEOUT_SECONDS,
        )
        assert confirmed_call.is_error is False
        confirmed_text = str(confirmed_call.content)
        assert (
            "action performed" in confirmed_text.lower()
            or "ok" in confirmed_text.lower()
        )
