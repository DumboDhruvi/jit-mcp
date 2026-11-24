"""Base protocols, warnings, and domain exceptions for accessibility adapters.

Licensed under Apache-2.0.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from jit_mcp.models import AppInfo, UiNode


class AccessibilityDisabledWarning(UserWarning):
    """Warning emitted when system-level accessibility is disabled."""


class JitMcpError(Exception):
    """Base exception for all jit-mcp domain errors."""


class AdapterUnavailableError(JitMcpError):
    """Raised when the accessibility adapter cannot be initialized."""

    def __init__(self, message: str = "Accessibility adapter is unavailable."):
        super().__init__(message)


class AppNotFoundError(JitMcpError):
    """Raised when an application with the specified app_id cannot be found."""

    def __init__(self, app_id: str, message: str | None = None):
        self.app_id = app_id
        super().__init__(message or f"Application '{app_id}' was not found.")


class NodeNotFoundError(JitMcpError):
    """Raised when a UI node cannot be resolved in the accessibility tree."""

    def __init__(self, node_ref: str, app_id: str | None = None, message: str | None = None):
        self.node_ref = node_ref
        self.app_id = app_id
        details = f" in app '{app_id}'" if app_id else ""
        super().__init__(message or f"UI node '{node_ref}' not found{details}.")


class ActionExecutionError(JitMcpError):
    """Raised when executing a semantic action on a UI node fails."""

    def __init__(
        self,
        action: str,
        control_id: str | None = None,
        reason: str = "",
        message: str | None = None,
    ):
        self.action = action
        self.control_id = control_id
        self.reason = reason
        ctrl = f" on control '{control_id}'" if control_id else ""
        rsn = f": {reason}" if reason else ""
        super().__init__(message or f"Action '{action}' failed{ctrl}{rsn}.")


class ControlDisabledError(JitMcpError):
    """Raised when an action is attempted on a disabled/greyed-out control."""

    def __init__(
        self,
        app_id: str,
        control_id: str,
        reason: str = "control is disabled/greyed out.",
        message: str | None = None,
    ):
        self.app_id = app_id
        self.control_id = control_id
        self.reason = reason
        default_msg = f"Cannot click control '{control_id}': {reason}"
        super().__init__(message or default_msg)


class SecurityBlockedError(JitMcpError):
    """Raised when an action is blocked by the safety guardrail."""

    def __init__(
        self,
        action: str | None = None,
        reason: str = "Action is blocked by safety guardrails.",
        message: str | None = None,
    ):
        self.action = action
        self.reason = reason
        super().__init__(message or (f"Action '{action}' blocked: {reason}" if action else reason))


class ConfirmationRequiredError(JitMcpError):
    """Raised when an action requires explicit human confirmation."""

    def __init__(
        self,
        action: str,
        reason: str = "Action requires confirmation.",
        message: str | None = None,
    ):
        self.action = action
        self.reason = reason
        super().__init__(message or f"Action '{action}' requires confirmation: {reason}")


class PostconditionFailedError(JitMcpError):
    """Raised when a workflow postcondition verification check fails."""

    def __init__(
        self,
        condition: str = "",
        step_index: int | None = None,
        reason: str = "",
        message: str | None = None,
    ):
        self.condition = condition
        self.step_index = step_index
        self.reason = reason
        idx = f" at step {step_index}" if step_index is not None else ""
        rsn = f": {reason}" if reason else ""
        super().__init__(message or f"Postcondition '{condition}' failed{idx}{rsn}.")


class ArgumentResolutionError(JitMcpError):
    """Raised when dynamic parameter resolution fails in a workflow tool."""

    def __init__(
        self,
        arg_name: str | None = None,
        reason: str = "",
        message: str | None = None,
    ):
        self.arg_name = arg_name
        self.reason = reason
        arg = f" for argument '{arg_name}'" if arg_name else ""
        rsn = f": {reason}" if reason else ""
        super().__init__(message or f"Argument resolution failed{arg}{rsn}.")


class ToolSpecSchemaError(JitMcpError):
    """Raised when a tool specification fails JSON schema or semantic validation."""

    def __init__(
        self,
        field: str | None = None,
        reason: str = "",
        message: str | None = None,
    ):
        self.field = field
        self.reason = reason
        fld = f" at '{field}'" if field else ""
        rsn = f": {reason}" if reason else ""
        super().__init__(message or f"ToolSpec schema validation failed{fld}{rsn}.")


@runtime_checkable
class AccessibilityAdapter(Protocol):
    """Protocol defining the interface for platform accessibility adapters."""

    def list_apps(self) -> list[AppInfo]:
        """List running desktop applications accessible to the adapter."""
        ...

    def get_root(self, app_id: str) -> UiNode:
        """Get the root UI accessibility node for the application."""
        ...

    def get_children(self, node_ref: str) -> list[UiNode]:
        """Fetch immediate child UI nodes of the referenced node."""
        ...

    def read_node(self, node_ref: str) -> UiNode:
        """Read fresh properties and states of the specified UI node."""
        ...

    def perform_action(
        self,
        node_ref: str,
        action: str,
        value: str | None = None,
        **kwargs: Any,
    ) -> str | None:
        """Perform a semantic action (click, toggle, select, set_text) on a node."""
        ...

    def hover(self, node_ref: str) -> None:
        """Move cursor over the specified UI node."""
        ...

    def click_coordinates(self, cx: int, cy: int) -> None:
        """Synthesize a hardware/X11 mouse click at absolute screen coordinates."""
        ...

    def mouse_path(
        self,
        points: list[tuple[int, int]],
        button_down: bool = True,
    ) -> None:
        """Trace a continuous mouse trajectory through absolute screen coordinates."""
        ...

    def press_shortcut(self, shortcut: str) -> None:
        """Synthesize keypresses for a keyboard shortcut string (e.g. 'Ctrl+S')."""
        ...

    def key_combination(self, keys: list[str]) -> None:
        """Synthesize low-level key combination events for a list of key symbols."""
        ...
