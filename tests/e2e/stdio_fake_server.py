"""Stdio subprocess server helper running FastMcpServer with FakeAccessibilityAdapter.

Licensed under Apache-2.0.
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from typing import Any

# Ensure project root and src/ are in sys.path
repo_root = Path(__file__).resolve().parent.parent.parent
if str(repo_root / "src") not in sys.path:
    sys.path.insert(0, str(repo_root / "src"))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from mcp.server.lowlevel.server import NotificationOptions

from jit_mcp.models import ToolSpec
from jit_mcp.server import FastMcpServer, _derive_registered_tool_name
from tests.conftest import FakeAccessibilityAdapter


def _patch_server_for_stdio(server: FastMcpServer) -> None:
    """Configure capabilities and dynamic tool signatures for stdio client compatibility."""
    # 1. Advertise tools.list_changed = True capability in initialize response
    orig_create_init = server.mcp._lowlevel_server.create_initialization_options

    def custom_create_init(*args: Any, **kwargs: Any) -> Any:
        kwargs["notification_options"] = NotificationOptions(tools_changed=True)
        return orig_create_init(*args, **kwargs)

    server.mcp._lowlevel_server.create_initialization_options = custom_create_init

    # 2. Wrap _register_spec_as_tool so dynamic tools use valid argument signatures
    def custom_register(spec: ToolSpec, emit_notification: bool = True) -> str:
        reg_name = _derive_registered_tool_name(spec.app.app_id, spec.tool.name)
        server._workflow_tools[reg_name] = spec
        if spec.tool.name != reg_name:
            server._workflow_tools[spec.tool.name] = spec

        if server.mcp:
            props = (
                spec.tool.parameters.get("properties", {})
                if spec.tool and spec.tool.parameters
                else {}
            )
            reqs = (
                spec.tool.parameters.get("required", [])
                if spec.tool and spec.tool.parameters
                else []
            )
            params = []
            for p_name in props:
                default_val = (
                    inspect.Parameter.empty if p_name in reqs else None
                )
                params.append(
                    inspect.Parameter(
                        p_name,
                        inspect.Parameter.KEYWORD_ONLY,
                        default=default_val,
                        annotation=Any,
                    )
                )
            params.append(
                inspect.Parameter(
                    "confirmed",
                    inspect.Parameter.KEYWORD_ONLY,
                    default=False,
                    annotation=bool,
                )
            )

            def dynamic_handler(*args: Any, **kwargs: Any) -> Any:
                confirmed = kwargs.pop("confirmed", False)
                if "kwargs" in kwargs and isinstance(kwargs["kwargs"], dict):
                    kwargs.update(kwargs.pop("kwargs"))
                clean_args = {k: v for k, v in kwargs.items() if v is not None}
                return server.call_compiled_tool(
                    app_id=spec.app.app_id,
                    tool_name=spec.tool.name,
                    arguments=clean_args,
                    confirmed=confirmed,
                )

            dynamic_handler.__signature__ = inspect.Signature(params)

            if server.mcp._tool_manager.get_tool(reg_name):
                server.mcp._tool_manager.remove_tool(reg_name)
            server.mcp.add_tool(
                dynamic_handler,
                name=reg_name,
                description=spec.tool.description or f"Workflow for {reg_name}",
            )

        if emit_notification:
            server._notify_tools_changed()

        return reg_name

    server._register_spec_as_tool = custom_register

    # Re-register any preloaded cached tools with the fixed dynamic signature
    for _reg_name, spec in list(server._workflow_tools.items()):
        if server.mcp:
            custom_register(spec, emit_notification=False)


def main() -> None:
    cache_dir = os.environ.get("JIT_MCP_CACHE_DIR")
    adapter = FakeAccessibilityAdapter()
    server = FastMcpServer(adapter=adapter, cache_dir=cache_dir)
    _patch_server_for_stdio(server)
    server.mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
