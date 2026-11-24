"""FastMCP meta-server for live desktop accessibility automation and dynamic workflows.

Licensed under Apache-2.0.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from jit_mcp.adapters.atspi2 import AtSpi2Adapter
from jit_mcp.adapters.base import (
    AccessibilityAdapter,
    AdapterUnavailableError,
    ArgumentResolutionError,
    ConfirmationRequiredError,
    SecurityBlockedError,
)
from jit_mcp.cache import ToolSpecCache
from jit_mcp.compiler import CompilerBackend, HeuristicCompilerBackend
from jit_mcp.executor import ActionExecutor
from jit_mcp.models import AppInfo, SafetyTier, ScanResult, ToolSpec
from jit_mcp.safety import SafetyGuardrail
from jit_mcp.scanner import UiScanner

# Import official FastMCP / MCPServer (supporting both v2.x MCPServer and v1.x FastMCP)
try:
    from mcp.server.mcpserver import MCPServer as FastMCPBase
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP as FastMCPBase
    except ImportError:
        FastMCPBase = None


class ToolInfo(str):
    """String representation of tool name with attribute access for compatibility."""

    def __new__(cls, name: str, description: str = ""):
        instance = super().__new__(cls, name)
        instance._description = description
        return instance

    @property
    def name(self) -> str:
        return str(self)

    @property
    def description(self) -> str:
        return self._description


def _derive_registered_tool_name(app_id: str, tool_name: str) -> str:
    """Derive standard registered tool name like 'texteditor_create_note'."""
    app_short = app_id.split(".")[-1].lower()
    clean_tool = re.sub(r"[^\w]", "_", tool_name.lower())
    if clean_tool.startswith(f"{app_short}_"):
        return clean_tool
    return f"{app_short}_{clean_tool}"


logger = logging.getLogger("jit_mcp.server")


class FastMcpServer:
    """FastMCP meta-server providing live accessibility inspection and automation tools."""

    CORE_TOOLS: ClassVar[list[str]] = [
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

    def __init__(
        self,
        adapter: AccessibilityAdapter | None = None,
        cache_dir: Path | str | None = None,
        compiler: CompilerBackend | None = None,
        safety: SafetyGuardrail | None = None,
        notification_callback: Callable[[str], None] | None = None,
    ):
        # 1. Platform Adapter setup (lazy fallback to AtSpi2Adapter if none provided)
        if adapter is not None:
            self._adapter = adapter
        else:
            try:
                self._adapter = AtSpi2Adapter(check_a11y=False)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not initialize default AtSpi2Adapter: %s", exc)
                self._adapter = None

        # 2. Components
        resolved_cache_dir = cache_dir or os.environ.get("JIT_MCP_CACHE_DIR")
        self.cache = ToolSpecCache(resolved_cache_dir)
        self.compiler = compiler or HeuristicCompilerBackend()
        self.safety = safety or SafetyGuardrail()
        self.scanner = UiScanner(adapter=self._adapter) if self._adapter else None
        self.executor = (
            ActionExecutor(adapter=self._adapter, safety=self.safety, scanner=self.scanner)
            if self._adapter
            else None
        )

        # 3. State & Registry
        self._scan_cache: dict[str, ScanResult] = {}
        self._workflow_tools: dict[str, ToolSpec] = {}
        self._notification_handlers: list[Callable[[str], None]] = []
        if notification_callback is not None:
            self._notification_handlers.append(notification_callback)

        # 4. Underlying FastMCP instance
        self.mcp = FastMCPBase("jit-mcp") if FastMCPBase else None

        # 5. Register core tools and preload cached tools
        self._register_core_tools()
        self._preload_cached_tools()

    @property
    def adapter(self) -> AccessibilityAdapter:
        """Access the underlying accessibility adapter or raise clear error if unavailable."""
        if self._adapter is None:
            try:
                self._adapter = AtSpi2Adapter(check_a11y=False)
                self.scanner = UiScanner(adapter=self._adapter)
                self.executor = ActionExecutor(
                    adapter=self._adapter, safety=self.safety, scanner=self.scanner
                )
            except Exception as exc:
                raise AdapterUnavailableError(
                    f"Accessibility adapter is unavailable on this environment: {exc}"
                ) from exc
        return self._adapter

    def set_notification_handler(self, handler: Callable[[str], None]) -> None:
        """Register a notification callback for tool changes."""
        self._notification_handlers.append(handler)

    def _notify_tools_changed(self) -> None:
        """Emit notifications/tools/list_changed to attached listeners."""
        for handler in self._notification_handlers:
            try:
                handler("notifications/tools/list_changed")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Notification handler failed: %s", exc)

    def _register_core_tools(self) -> None:
        """Register core meta-tools on the underlying FastMCP server."""
        if not self.mcp:
            return

        # Register methods on mcp instance
        self.mcp.add_tool(self.list_desktop_apps, name="list_desktop_apps", description="List open desktop applications")
        self.mcp.add_tool(self.scan_app_ui, name="scan_app_ui", description="Scan UI controls of a desktop application")
        self.mcp.add_tool(self.click_control, name="click_control", description="Click a UI control by name or control ID")
        self.mcp.add_tool(self.hover_control, name="hover_control", description="Hover over a UI control")
        self.mcp.add_tool(self.set_text, name="set_text", description="Enter text into an input field")
        self.mcp.add_tool(self.toggle_checkbox, name="toggle_checkbox", description="Toggle a checkbox control")
        self.mcp.add_tool(self.select_dropdown, name="select_dropdown", description="Select an option in a dropdown")
        self.mcp.add_tool(self.scroll_control, name="scroll_control", description="Scroll a container up or down")
        self.mcp.add_tool(self.draw_or_drag_path, name="draw_or_drag_path", description="Draw or drag a pointer trajectory")
        self.mcp.add_tool(self.press_shortcut, name="press_shortcut", description="Press a keyboard shortcut (e.g. Ctrl+S)")
        self.mcp.add_tool(self.execute_action_batch, name="execute_action_batch", description="Execute a batch of UI actions in sequence")
        self.mcp.add_tool(self.save_workflow_tool, name="save_workflow_tool", description="Save a sequence of actions as a reusable workflow tool")
        self.mcp.add_tool(self.call_compiled_tool, name="call_compiled_tool", description="Execute a cached workflow tool")

    def _preload_cached_tools(self) -> None:
        """Load and register previously cached workflow tools."""
        try:
            cached_specs = self.cache.list()
            for spec in cached_specs:
                self._register_spec_as_tool(spec, emit_notification=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Preloading cached tools failed: %s", exc)

    def _register_spec_as_tool(self, spec: ToolSpec, emit_notification: bool = True) -> str:
        """Dynamically register a ToolSpec as an MCP tool."""
        reg_name = _derive_registered_tool_name(spec.app.app_id, spec.tool.name)
        self._workflow_tools[reg_name] = spec
        if spec.tool.name != reg_name:
            self._workflow_tools[spec.tool.name] = spec

        if self.mcp:
            def make_handler(target_app: str, target_tool: str):
                def handler(**kwargs: Any) -> Any:
                    confirmed = kwargs.pop("confirmed", False)
                    return self.call_compiled_tool(
                        app_id=target_app,
                        tool_name=target_tool,
                        arguments=kwargs,
                        confirmed=confirmed,
                    )
                return handler

            handler_fn = make_handler(spec.app.app_id, spec.tool.name)
            self.mcp.add_tool(handler_fn, name=reg_name, description=spec.tool.description or f"Workflow for {reg_name}")

        if emit_notification:
            self._notify_tools_changed()

        return reg_name

    def list_tools(self) -> list[ToolInfo]:
        """Return registered meta-tools and dynamic workflow tools."""
        tool_names = list(self.CORE_TOOLS)
        for w_name in self._workflow_tools:
            if w_name not in tool_names:
                tool_names.append(w_name)
        return [ToolInfo(name) for name in tool_names]

    # --- Core Tool Implementations ---

    def list_desktop_apps(self) -> list[dict[str, Any]]:
        """List running desktop applications."""
        apps = self.adapter.list_apps()
        return [
            {
                "app_id": a.app_id,
                "name": a.name,
                "window_title": a.window_title,
                "pid": a.pid,
                "toolkit": a.toolkit,
            }
            for a in apps
        ]

    def scan_app_ui(self, app_id: str) -> dict[str, Any]:
        """Scan UI controls of an application and cache internal registry."""
        if not self.scanner:
            self.scanner = UiScanner(adapter=self.adapter)
        scan_result = self.scanner.scan(app_id)
        self._scan_cache[app_id] = scan_result
        if self.executor:
            self.executor.register_scan_result(scan_result)
        return scan_result.to_compact_llm_view()

    def click_control(
        self, app_id: str, control_id_or_name: str, confirmed: bool = False
    ) -> Any:
        """Click a control semantically, verifying safety and greyed-out state."""
        if not self.executor:
            self.executor = ActionExecutor(
                adapter=self.adapter, safety=self.safety, scanner=self.scanner
            )
        cached_scan = self._scan_cache.get(app_id)
        try:
            return self.executor.click(
                app_id=app_id,
                control_id_or_name=control_id_or_name,
                scan_result=cached_scan,
                confirmed=confirmed,
            )
        except ConfirmationRequiredError as exc:
            return f"Action '{control_id_or_name}' requires confirmation: {exc.reason}"

    def hover_control(
        self, app_id: str, control_id_or_name: str, confirmed: bool = False
    ) -> Any:
        """Hover over a UI control."""
        if not self.executor:
            self.executor = ActionExecutor(
                adapter=self.adapter, safety=self.safety, scanner=self.scanner
            )
        cached_scan = self._scan_cache.get(app_id)
        try:
            try:
                return self.executor.hover(
                    app_id=app_id,
                    control_id_or_name=control_id_or_name,
                    scan_result=cached_scan,
                    confirmed=confirmed,
                )
            except TypeError:
                return self.executor.hover(
                    app_id=app_id,
                    control_id_or_name=control_id_or_name,
                    scan_result=cached_scan,
                )
        except ConfirmationRequiredError as exc:
            return f"Action '{control_id_or_name}' requires confirmation: {exc.reason}"

    def set_text(
        self, app_id: str, control_id_or_name: str, text: str, confirmed: bool = False
    ) -> Any:
        """Enter text into an input field."""
        if not self.executor:
            self.executor = ActionExecutor(
                adapter=self.adapter, safety=self.safety, scanner=self.scanner
            )
        cached_scan = self._scan_cache.get(app_id)
        try:
            try:
                return self.executor.set_text(
                    app_id=app_id,
                    control_id_or_name=control_id_or_name,
                    text=text,
                    scan_result=cached_scan,
                    confirmed=confirmed,
                )
            except TypeError:
                return self.executor.set_text(
                    app_id=app_id,
                    control_id_or_name=control_id_or_name,
                    text=text,
                    scan_result=cached_scan,
                )
        except ConfirmationRequiredError as exc:
            return f"Action '{control_id_or_name}' requires confirmation: {exc.reason}"

    def toggle_checkbox(
        self, app_id: str, control_id_or_name: str, confirmed: bool = False
    ) -> Any:
        """Toggle a checkbox control."""
        if not self.executor:
            self.executor = ActionExecutor(
                adapter=self.adapter, safety=self.safety, scanner=self.scanner
            )
        cached_scan = self._scan_cache.get(app_id)
        try:
            try:
                return self.executor.toggle_checkbox(
                    app_id=app_id,
                    control_id_or_name=control_id_or_name,
                    scan_result=cached_scan,
                    confirmed=confirmed,
                )
            except TypeError:
                return self.executor.toggle_checkbox(
                    app_id=app_id,
                    control_id_or_name=control_id_or_name,
                    scan_result=cached_scan,
                )
        except ConfirmationRequiredError as exc:
            return f"Action '{control_id_or_name}' requires confirmation: {exc.reason}"

    def select_dropdown(
        self,
        app_id: str,
        control_id_or_name: str,
        option: str,
        confirmed: bool = False,
    ) -> Any:
        """Select an option in a dropdown combo box."""
        if not self.executor:
            self.executor = ActionExecutor(
                adapter=self.adapter, safety=self.safety, scanner=self.scanner
            )
        cached_scan = self._scan_cache.get(app_id)
        try:
            try:
                return self.executor.select_dropdown(
                    app_id=app_id,
                    control_id_or_name=control_id_or_name,
                    option=option,
                    scan_result=cached_scan,
                    confirmed=confirmed,
                )
            except TypeError:
                return self.executor.select_dropdown(
                    app_id=app_id,
                    control_id_or_name=control_id_or_name,
                    option=option,
                    scan_result=cached_scan,
                )
        except ConfirmationRequiredError as exc:
            return f"Action '{control_id_or_name}' requires confirmation: {exc.reason}"

    def scroll_control(
        self,
        app_id: str,
        control_id_or_name: str,
        direction: str = "down",
        amount: int = 3,
        confirmed: bool = False,
    ) -> Any:
        """Scroll a control up or down."""
        if not self.executor:
            self.executor = ActionExecutor(
                adapter=self.adapter, safety=self.safety, scanner=self.scanner
            )
        cached_scan = self._scan_cache.get(app_id)
        try:
            try:
                return self.executor.scroll_control(
                    app_id=app_id,
                    control_id_or_name=control_id_or_name,
                    direction=direction,
                    amount=amount,
                    scan_result=cached_scan,
                    confirmed=confirmed,
                )
            except TypeError:
                return self.executor.scroll_control(
                    app_id=app_id,
                    control_id_or_name=control_id_or_name,
                    direction=direction,
                    amount=amount,
                    scan_result=cached_scan,
                )
        except ConfirmationRequiredError as exc:
            return f"Action '{control_id_or_name}' requires confirmation: {exc.reason}"

    def draw_or_drag_path(
        self,
        app_id: str,
        control_id_or_name: str,
        points: list[list[float]] | list[tuple[float, float]],
        normalized: bool = True,
        button_down: bool = True,
        confirmed: bool = False,
    ) -> Any:
        """Draw or drag a pointer trajectory across screen coordinates."""
        if not self.executor:
            self.executor = ActionExecutor(
                adapter=self.adapter, safety=self.safety, scanner=self.scanner
            )
        cached_scan = self._scan_cache.get(app_id)
        converted_points = [
            (float(p[0]), float(p[1])) if normalized else (int(p[0]), int(p[1]))
            for p in points
        ]
        try:
            try:
                return self.executor.draw_or_drag_path(
                    app_id=app_id,
                    control_id_or_name=control_id_or_name,
                    points=converted_points,  # type: ignore[arg-type]
                    normalized=normalized,
                    button_down=button_down,
                    scan_result=cached_scan,
                    confirmed=confirmed,
                )
            except TypeError:
                return self.executor.draw_or_drag_path(
                    app_id=app_id,
                    control_id_or_name=control_id_or_name,
                    points=converted_points,  # type: ignore[arg-type]
                    normalized=normalized,
                    button_down=button_down,
                    scan_result=cached_scan,
                )
        except ConfirmationRequiredError as exc:
            return f"Action '{control_id_or_name}' requires confirmation: {exc.reason}"

    def press_shortcut(
        self, app_id: str, shortcut: str, confirmed: bool = False
    ) -> Any:
        """Execute a keyboard shortcut (e.g. 'Ctrl+S')."""
        if not self.executor:
            self.executor = ActionExecutor(
                adapter=self.adapter, safety=self.safety, scanner=self.scanner
            )
        try:
            try:
                return self.executor.press_shortcut(
                    app_id=app_id, shortcut=shortcut, confirmed=confirmed
                )
            except TypeError:
                return self.executor.press_shortcut(app_id=app_id, shortcut=shortcut)
        except ConfirmationRequiredError as exc:
            return f"Action '{shortcut}' requires confirmation: {exc.reason}"

    def execute_action_batch(
        self,
        app_id: str,
        actions: list[dict[str, Any]],
        stop_on_error: bool = True,
    ) -> Any:
        """Execute a batch of actions sequentially in one MCP call."""
        if not self.executor:
            self.executor = ActionExecutor(
                adapter=self.adapter, safety=self.safety, scanner=self.scanner
            )
        cached_scan = self._scan_cache.get(app_id)
        result = self.executor.execute_action_batch(
            app_id=app_id,
            actions=actions,
            stop_on_error=stop_on_error,
            scan_result=cached_scan,
        )
        return result.model_dump()

    def save_workflow_tool(
        self,
        app_id: str,
        tool_name: str,
        steps: list[dict[str, Any]],
        intent: str = "",
    ) -> dict[str, Any]:
        """Compile, validate, persist, and dynamically expose a multi-step workflow tool."""
        # 1. Check safety of intent and tool name
        intent_str = intent or f"Workflow {tool_name} on {app_id}"
        decision = self.safety.classify_intent_safety(intent_str)
        if decision.risk_tier == SafetyTier.BLOCKED:
            raise SecurityBlockedError(
                action=tool_name,
                reason=f"Workflow intent '{intent_str}' is blocked: {decision.reason}",
            )

        # Check safety of workflow steps
        if hasattr(self.safety, "classify_workflow_steps"):
            steps_decision = self.safety.classify_workflow_steps(steps)
            if steps_decision.risk_tier == SafetyTier.BLOCKED:
                raise SecurityBlockedError(
                    action=tool_name,
                    reason=f"Workflow step is blocked: {steps_decision.reason}",
                )

        # 2. Compile workflow into ToolSpec
        app_info = AppInfo(app_id=app_id, name=app_id)
        cached_scan = self._scan_cache.get(app_id)
        tool_spec = self.compiler.compile_workflow(
            app_info=app_info,
            recorded_steps=steps,
            intent=intent_str,
            tool_name=tool_name,
            scan_result=cached_scan,
        )

        # Blocked safety validation
        if tool_spec.safety.risk_tier == SafetyTier.BLOCKED:
            raise SecurityBlockedError(
                action=tool_name,
                reason=f"Compiled workflow safety is blocked: {tool_spec.safety.reason}",
            )

        # 3. Persist to cache
        saved_path = self.cache.save(tool_spec)

        # 4. Register dynamically
        reg_name = self._register_spec_as_tool(tool_spec, emit_notification=True)

        return {
            "status": "saved",
            "app_id": app_id,
            "tool_name": reg_name,
            "path": str(saved_path),
            "safety_tier": tool_spec.safety.risk_tier.value,
        }

    def call_compiled_tool(
        self,
        app_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        confirmed: bool = False,
    ) -> Any:
        """Execute a compiled workflow tool from disk cache."""
        # 1. Locate spec
        spec = self.cache.get(app_id, tool_name)
        if spec is None:
            # Try looking up in memory
            reg_name = _derive_registered_tool_name(app_id, tool_name)
            spec = self._workflow_tools.get(reg_name) or self._workflow_tools.get(tool_name)

        if spec is None:
            raise ArgumentResolutionError(
                arg_name="tool_name",
                reason=(
                    f"Workflow tool '{tool_name}' for app '{app_id}' was not found in cache. "
                    "Save it first using 'save_workflow_tool'."
                ),
            )

        # 2. Safety check
        if spec.safety.risk_tier == SafetyTier.BLOCKED:
            raise SecurityBlockedError(
                action=tool_name,
                reason=f"Workflow tool '{tool_name}' is blocked by safety guardrail.",
            )

        if spec.safety.risk_tier == SafetyTier.CONFIRM and not confirmed:
            return (
                f"Action '{tool_name}' requires confirmation: "
                f"{spec.safety.reason or 'Destructive workflow actions.'}"
            )

        # 3. Execution
        if not self.executor:
            self.executor = ActionExecutor(
                adapter=self.adapter, safety=self.safety, scanner=self.scanner
            )

        # Resolve parameter placeholders in postconditions if present
        def _interpolate_pc(template: str, args: dict[str, Any]) -> str:
            def _sub(m: re.Match[str]) -> str:
                k = m.group(1)
                return str(args.get(k, m.group(0)))

            return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", _sub, template)

        resolved_pcs = []
        for pc in spec.postconditions:
            if isinstance(pc.expected, str) and "{" in pc.expected and arguments:
                exp = _interpolate_pc(pc.expected, arguments)
                resolved_pcs.append(pc.model_copy(update={"expected": exp}))
            else:
                resolved_pcs.append(pc)
        spec_to_run = spec.model_copy(update={"postconditions": resolved_pcs})

        return self.executor.execute_workflow(
            tool_spec=spec_to_run,
            arguments=arguments or {},
            confirmed=confirmed,
        )

    def run(self, transport: str = "stdio", **kwargs: Any) -> None:
        """Run the server using the specified MCP transport."""
        if not self.mcp:
            raise RuntimeError("MCPServer library is not available.")
        self.mcp.run(transport=transport, **kwargs)


def main() -> None:
    """CLI entrypoint for running jit-mcp over stdio."""
    parser = argparse.ArgumentParser(description="JIT-MCP FastMCP Desktop Automation Server")
    parser.add_argument(
        "--cache-dir",
        type=str,
        default=None,
        help="Custom directory path for caching compiled workflow tools",
    )
    args = parser.parse_args()

    server = FastMcpServer(cache_dir=args.cache_dir)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
