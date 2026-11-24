"""JIT-MCP: Just-In-Time Model Context Protocol Server for Desktop Automation.

Provides public exports for fast MCP integration with OS accessibility APIs.
"""

from typing import Any

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "FastMcpServer",
    "ToolSpec",
    "AtSpi2Adapter",
    "UiBounds",
    "UiNode",
]

_LAZY_EXPORTS: dict[str, list[tuple[str, str]]] = {
    "FastMcpServer": [("jit_mcp.server", "FastMcpServer")],
    "ToolSpec": [("jit_mcp.models", "ToolSpec"), ("jit_mcp.server", "ToolSpec")],
    "AtSpi2Adapter": [
        ("jit_mcp.adapters.atspi2", "AtSpi2Adapter"),
        ("jit_mcp.adapters", "AtSpi2Adapter"),
    ],
    "UiBounds": [("jit_mcp.models", "UiBounds")],
    "UiNode": [("jit_mcp.models", "UiNode")],
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        candidates = _LAZY_EXPORTS[name]
        last_exc: Exception | None = None
        for module_name, attr_name in candidates:
            try:
                import importlib

                module = importlib.import_module(module_name)
                val = getattr(module, attr_name)
                globals()[name] = val
                return val
            except (ImportError, AttributeError) as exc:
                last_exc = exc
                continue
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r} "
            f"(lazy export could not be loaded: {last_exc})"
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_LAZY_EXPORTS.keys()))
