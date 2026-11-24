"""Platform adapters package for jit-mcp.

Licensed under Apache-2.0.
"""

from typing import Any
from jit_mcp.adapters.base import (
    AccessibilityAdapter,
    AccessibilityDisabledWarning,
    ActionExecutionError,
    AdapterUnavailableError,
    AppNotFoundError,
    ArgumentResolutionError,
    ConfirmationRequiredError,
    ControlDisabledError,
    JitMcpError,
    NodeNotFoundError,
    PostconditionFailedError,
    SecurityBlockedError,
    ToolSpecSchemaError,
)

__all__ = [
    "AccessibilityAdapter",
    "AccessibilityDisabledWarning",
    "JitMcpError",
    "AdapterUnavailableError",
    "AppNotFoundError",
    "NodeNotFoundError",
    "ActionExecutionError",
    "ControlDisabledError",
    "SecurityBlockedError",
    "ConfirmationRequiredError",
    "PostconditionFailedError",
    "ArgumentResolutionError",
    "ToolSpecSchemaError",
    "AtSpi2Adapter",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "AtSpi2Adapter": ("jit_mcp.adapters.atspi2", "AtSpi2Adapter"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        module_name, attr_name = _LAZY_EXPORTS[name]
        try:
            import importlib

            module = importlib.import_module(module_name)
            val = getattr(module, attr_name)
            globals()[name] = val
            return val
        except (ImportError, AttributeError) as exc:
            raise AttributeError(
                f"module {__name__!r} has no attribute {name!r} "
                f"(lazy export could not be loaded: {exc})"
            ) from exc
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_LAZY_EXPORTS.keys()))
