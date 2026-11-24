"""Domain models and schema types for jit-mcp.

Licensed under Apache-2.0.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class UiBounds(BaseModel):
    """Screen coordinates and bounding box of a UI control."""

    model_config = ConfigDict(extra="ignore")

    x: int
    y: int
    width: int
    height: int
    cx: int | None = None
    cy: int | None = None

    @model_validator(mode="after")
    def _validate_and_compute_center(self) -> "UiBounds":
        if self.width < 0:
            raise ValueError(f"width must be non-negative, got {self.width}")
        if self.height < 0:
            raise ValueError(f"height must be non-negative, got {self.height}")
        if self.cx is None:
            self.cx = self.x + self.width // 2
        if self.cy is None:
            self.cy = self.y + self.height // 2
        return self


class AppInfo(BaseModel):
    """Metadata describing a running desktop application."""

    model_config = ConfigDict(extra="ignore")

    app_id: str
    name: str
    pid: int | None = None
    window_title: str = ""
    platform: Literal["linux-atspi2", "windows-uia", "macos-ax"] = "linux-atspi2"
    toolkit: str = ""
    version: str = ""


class UiNode(BaseModel):
    """Internal representation of a scanned UI control in the accessibility tree."""

    model_config = ConfigDict(extra="ignore")

    control_id: str = ""
    node_ref: str = ""
    role: str
    name: str = ""
    enabled: bool = True
    states: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    checked: bool | None = None
    expanded: bool | None = None
    value: str | None = None
    shortcut: str | None = None
    menu_path: list[str] | str = Field(default_factory=list)
    bounds: UiBounds | None = None
    path_hint: str | None = None
    automation_id: str | None = None
    children: list["UiNode"] = Field(default_factory=list)


class CompactControl(BaseModel):
    """Token-efficient, LLM-facing control representation with ZERO coordinates."""

    model_config = ConfigDict(extra="ignore")

    control_id: str = ""
    name: str = ""
    role: str
    enabled: bool
    checked: bool | None = None
    expanded: bool | None = None
    value: str | None = None
    shortcut: str | None = None
    menu_path: list[str] | str = Field(default_factory=list)


class ScanResult(BaseModel):
    """Dual-view scan outcome: compact view for LLMs and hidden internal registry."""

    model_config = ConfigDict(extra="ignore")

    app: AppInfo
    controls: list[CompactControl]
    internal_registry: dict[str, UiNode]
    snapshot_hash: str
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)

    def to_compact_llm_view(self) -> dict[str, Any]:
        """Return a token-efficient JSON view strictly omitting coordinate keys."""
        compact_controls: list[dict[str, Any]] = []
        for c in self.controls:
            cdict = c.model_dump()
            # Explicit defense: strip any accidental coordinate keys
            for coord_key in ("x", "y", "width", "height", "cx", "cy", "bounds"):
                cdict.pop(coord_key, None)
            compact_controls.append(cdict)

        return {
            "app": self.app.model_dump(),
            "snapshot_hash": self.snapshot_hash,
            "truncated": self.truncated,
            "warnings": list(self.warnings),
            "controls": compact_controls,
        }


class SafetyTier(str, Enum):
    """Risk tier classifications for actions and workflow tools."""

    SAFE = "safe"
    CONFIRM = "confirm"
    BLOCKED = "blocked"


class SafetyDecision(BaseModel):
    """Result of a safety guardrail check."""

    model_config = ConfigDict(extra="ignore")

    risk_tier: SafetyTier
    requires_confirmation: bool
    reason: str


class Selector(BaseModel):
    """Semantic target selector for workflow steps and postconditions."""

    model_config = ConfigDict(extra="ignore")

    role: str = Field(min_length=1)
    name: str | None = None
    name_pattern: str | None = None
    automation_id: str | None = None
    path_hint: str | None = None


class ActionStep(BaseModel):
    """Single execution step within a compiled ToolSpec."""

    model_config = ConfigDict(extra="ignore")

    step_id: str = Field(min_length=1)
    action: Literal[
        "click",
        "hover",
        "set_text",
        "select",
        "toggle",
        "scroll",
        "draw_path",
        "key_press",
        "read_text",
        "wait",
    ]
    target: Selector
    fallbacks: list[Selector] = Field(default_factory=list)
    coordinate_fallback: UiBounds | None = None
    click_path: list[str] = Field(default_factory=list)
    value_template: str | None = None
    timeout_ms: int = Field(default=3000, ge=100, le=30000)


class Postcondition(BaseModel):
    """Verification check evaluated after workflow execution."""

    model_config = ConfigDict(extra="ignore")

    type: Literal[
        "element_exists",
        "element_absent",
        "text_contains",
        "state_equals",
        "value_equals",
    ]
    target: Selector
    expected: Any = None
    timeout_ms: int = Field(default=2000, ge=100, le=15000)


class ScannerBudget(BaseModel):
    """Resource constraints for live UI tree inspection."""

    model_config = ConfigDict(extra="ignore")

    max_depth: int = Field(default=6, ge=1)
    max_nodes: int = Field(default=250, ge=1)
    timeout_ms: int = Field(default=3000, ge=100)


class ToolProvenance(BaseModel):
    """Audit trail and compilation metadata for a ToolSpec."""

    model_config = ConfigDict(extra="ignore")

    compiled_at: str
    intent: str
    scanner_budget: ScannerBudget
    snapshot_hash: str = Field(min_length=8)
    compiler_version: str


class ToolAppInfo(BaseModel):
    """Application metadata embedded in a ToolSpec."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    app_id: str = Field(min_length=1)
    version: str = ""
    platform: Literal["linux-atspi2", "windows-uia", "macos-ax"] = "linux-atspi2"
    toolkit: str = ""


class ToolDefinition(BaseModel):
    """Tool schema and parameters embedded in a ToolSpec."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    description: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )


class ToolSafety(BaseModel):
    """Safety classification embedded in a ToolSpec."""

    model_config = ConfigDict(extra="ignore")

    risk_tier: SafetyTier | Literal["safe", "confirm", "blocked"]
    requires_confirmation: bool
    reason: str


class ToolSpec(BaseModel):
    """Compiled, cached multi-step workflow MCP tool schema (v1.0.0)."""

    model_config = ConfigDict(extra="ignore")

    schema_version: Literal["1.0.0"] = "1.0.0"
    app: ToolAppInfo
    tool: ToolDefinition
    steps: list[ActionStep] = Field(..., min_length=1)
    postconditions: list[Postcondition] = Field(default_factory=list)
    safety: ToolSafety
    provenance: ToolProvenance


class BatchActionItem(BaseModel):
    """Single action item in a latency-optimized action batch."""

    model_config = ConfigDict(extra="ignore")

    action: Literal[
        "click",
        "hover",
        "set_text",
        "toggle",
        "select",
        "scroll",
        "draw_path",
        "shortcut",
    ]
    control_id_or_name: str | None = None
    value: str | None = None
    option: str | None = None
    direction: str | None = None
    amount: int = 1
    points: list[tuple[float, float]] | None = None
    normalized: bool = True
    button_down: bool = True
    shortcut: str | None = None
    confirmed: bool = False


class BatchStepResult(BaseModel):
    """Execution result of an individual batch step."""

    model_config = ConfigDict(extra="ignore")

    step_index: int
    action: str
    control_id_or_name: str | None = None
    success: bool = True
    error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class BatchResult(BaseModel):
    """Overall result of execute_action_batch."""

    model_config = ConfigDict(extra="ignore")

    success: bool
    total_steps: int
    executed_steps: int
    results: list[BatchStepResult] = Field(default_factory=list)
    error: str | None = None
    stopped_early: bool = False


# Rebuild recursive models
UiNode.model_rebuild()

__all__ = [
    "UiBounds",
    "AppInfo",
    "UiNode",
    "CompactControl",
    "ScanResult",
    "SafetyTier",
    "SafetyDecision",
    "Selector",
    "ActionStep",
    "Postcondition",
    "ScannerBudget",
    "ToolProvenance",
    "ToolAppInfo",
    "ToolDefinition",
    "ToolSafety",
    "ToolSpec",
    "BatchActionItem",
    "BatchStepResult",
    "BatchResult",
]
