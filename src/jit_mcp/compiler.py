"""Pluggable workflow compiler and heuristic backend for ToolSpec generation.

Licensed under Apache-2.0.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from jit_mcp.adapters.base import SecurityBlockedError
from jit_mcp.models import (
    ActionStep,
    AppInfo,
    Postcondition,
    SafetyTier,
    ScannerBudget,
    ScanResult,
    Selector,
    ToolAppInfo,
    ToolDefinition,
    ToolProvenance,
    ToolSafety,
    ToolSpec,
    UiBounds,
)
from jit_mcp.safety import SafetyGuardrail

logger = logging.getLogger(__name__)


@runtime_checkable
class CompilerBackend(Protocol):
    """Protocol for pluggable workflow tool compiler backends."""

    compiler_version: str

    def compile_workflow(
        self,
        app_info: AppInfo,
        recorded_steps: list[dict[str, Any]] | list[ActionStep],
        intent: str,
        tool_name: str | None = None,
        scan_result: ScanResult | None = None,
        scanner_budget: ScannerBudget | None = None,
    ) -> ToolSpec:
        """Compile recorded interactive actions into a validated ToolSpec."""
        ...


class HeuristicCompilerBackend:
    """Deterministic, zero-cost offline compiler backend for workflow ToolSpecs."""

    compiler_version: str = "heuristic-1.0.0"

    def _normalize_tool_name(self, name_or_intent: str) -> str:
        """Normalize a string into a schema-valid tool name: ^[a-z][a-z0-9_]{1,63}$."""
        cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", name_or_intent.strip().lower())
        cleaned = re.sub(r"^[^a-z]+", "", cleaned)
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")

        if not cleaned:
            cleaned = "workflow_tool"
        elif len(cleaned) < 2:
            cleaned = f"{cleaned}_tool"

        return cleaned[:64]

    def compile_workflow(
        self,
        app_info: AppInfo,
        recorded_steps: list[dict[str, Any]] | list[ActionStep],
        intent: str,
        tool_name: str | None = None,
        scan_result: ScanResult | None = None,
        scanner_budget: ScannerBudget | None = None,
    ) -> ToolSpec:
        """Compile recorded steps or scan result into a verified ToolSpec."""
        # 1. Normalize tool name
        norm_name = self._normalize_tool_name(tool_name or intent)

        # 2. Safety classification of intent / tool_name
        intent_decision = SafetyGuardrail.classify_action(
            action=norm_name,
            control_name="",
            intent=intent,
        )
        if intent_decision.risk_tier == SafetyTier.BLOCKED:
            raise SecurityBlockedError(action=norm_name, reason=intent_decision.reason)

        # Classify every individual step in the workflow to prevent prompt injection / hidden attacks
        steps_decision = SafetyGuardrail.classify_workflow_steps(recorded_steps)
        if steps_decision.risk_tier == SafetyTier.BLOCKED:
            raise SecurityBlockedError(action=norm_name, reason=steps_decision.reason)

        # Merge risk tiering: if either is CONFIRM, whole workflow requires confirmation
        if (
            intent_decision.risk_tier == SafetyTier.CONFIRM
            or steps_decision.risk_tier == SafetyTier.CONFIRM
        ):
            effective_tier = SafetyTier.CONFIRM
            requires_confirmation = True
            reason = (
                intent_decision.reason
                if intent_decision.risk_tier == SafetyTier.CONFIRM
                else steps_decision.reason
            )
        else:
            effective_tier = SafetyTier.SAFE
            requires_confirmation = False
            reason = "Workflow is safe to execute"

        tool_safety = ToolSafety(
            risk_tier=effective_tier,
            requires_confirmation=requires_confirmation,
            reason=reason,
        )

        # 3. Process steps
        action_steps: list[ActionStep] = []
        parameters_props: dict[str, Any] = {}
        parameters_req: list[str] = []
        postconditions: list[Postcondition] = []

        registry = scan_result.internal_registry if scan_result else {}

        for idx, raw_step in enumerate(recorded_steps):
            coord_fallback: UiBounds | None = None
            click_path: list[str] = []
            val_template: str | None = None

            if isinstance(raw_step, ActionStep):
                step_obj = raw_step
                action_steps.append(step_obj)
                val_template = step_obj.value_template
                target_sel = step_obj.target
                act = step_obj.action
            else:
                act = str(raw_step.get("action", "click")).lower()
                if act == "type":
                    act = "set_text"

                step_id = str(raw_step.get("step_id") or f"step_{idx + 1}")
                val_template = raw_step.get("value_template") or raw_step.get("value")

                # Resolve target selector
                raw_target = raw_step.get("target")
                if isinstance(raw_target, Selector):
                    target_sel = raw_target
                elif isinstance(raw_target, dict):
                    target_sel = Selector.model_validate(raw_target)
                else:
                    role = raw_step.get("role") or ("text_input" if act == "set_text" else "button")
                    name = raw_step.get("name") or raw_step.get("control_id_or_name") or raw_target or f"Control_{idx+1}"
                    target_sel = Selector(role=str(role), name=str(name))

                # Resolve coordinate fallback: ONLY populate if real UiBounds are known
                if raw_step.get("coordinate_fallback"):
                    coord_fallback = UiBounds.model_validate(raw_step["coordinate_fallback"])
                elif raw_step.get("bounds"):
                    coord_fallback = UiBounds.model_validate(raw_step["bounds"])
                elif target_sel.name and target_sel.name in registry and registry[target_sel.name].bounds:
                    coord_fallback = registry[target_sel.name].bounds
                elif target_sel.automation_id and target_sel.automation_id in registry and registry[target_sel.automation_id].bounds:
                    coord_fallback = registry[target_sel.automation_id].bounds
                else:
                    # Do NOT fabricate synthetic default coordinates!
                    coord_fallback = None

                if raw_step.get("click_path"):
                    click_path = list(raw_step["click_path"])

                step_obj = ActionStep(
                    step_id=step_id,
                    action=act,  # type: ignore[arg-type]
                    target=target_sel,
                    coordinate_fallback=coord_fallback,
                    click_path=click_path,
                    value_template=val_template,
                )
                action_steps.append(step_obj)

            # Infer template parameters (e.g. {query})
            if val_template:
                placeholders = re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", val_template)
                for ph in placeholders:
                    parameters_props[ph] = {
                        "type": "string",
                        "description": f"Value for parameter {ph}",
                    }
                    if ph not in parameters_req:
                        parameters_req.append(ph)

            # Generate smart postconditions
            if act in ("toggle",) or "checkbox" in target_sel.role.lower():
                postconditions.append(
                    Postcondition(
                        type="state_equals",
                        target=target_sel,
                        expected={"checked": True},
                        timeout_ms=2000,
                    )
                )
            elif act in ("set_text", "type") and val_template:
                postconditions.append(
                    Postcondition(
                        type="text_contains",
                        target=target_sel,
                        expected=val_template,
                        timeout_ms=2000,
                    )
                )

        # Default postcondition if none were auto-generated
        if not postconditions and action_steps:
            last_target = action_steps[-1].target
            postconditions.append(
                Postcondition(
                    type="element_exists",
                    target=last_target,
                    timeout_ms=2000,
                )
            )

        # 4. Provenance
        now_iso = datetime.now(UTC).isoformat()
        budget = scanner_budget or ScannerBudget(max_depth=6, max_nodes=250, timeout_ms=3000)
        snap_hash = (
            scan_result.snapshot_hash
            if scan_result
            else hashlib.sha256(f"{app_info.app_id}:{intent}".encode()).hexdigest()[:16]
        )

        provenance = ToolProvenance(
            compiled_at=now_iso,
            intent=intent,
            scanner_budget=budget,
            snapshot_hash=snap_hash,
            compiler_version=self.compiler_version,
        )

        # 5. App & Tool definitions
        tool_app = ToolAppInfo(
            name=app_info.name,
            app_id=app_info.app_id,
            version=app_info.version,
            platform=app_info.platform,
            toolkit=app_info.toolkit,
        )

        tool_def = ToolDefinition(
            name=norm_name,
            description=intent or f"Automated workflow for {app_info.name}",
            parameters={
                "type": "object",
                "properties": parameters_props,
                "required": parameters_req,
            },
        )

        return ToolSpec(
            schema_version="1.0.0",
            app=tool_app,
            tool=tool_def,
            steps=action_steps,
            postconditions=postconditions,
            safety=tool_safety,
            provenance=provenance,
        )


class LlmCompilerBackend:
    """Optional LLM-assisted workflow compiler backend with heuristic fallback."""

    compiler_version: str = "llm-1.0.0"

    def __init__(
        self,
        llm_fn: Callable[[str], str] | None = None,
        fallback_backend: CompilerBackend | None = None,
    ) -> None:
        self.llm_fn = llm_fn
        self.fallback = fallback_backend or HeuristicCompilerBackend()

    def compile_workflow(
        self,
        app_info: AppInfo,
        recorded_steps: list[dict[str, Any]] | list[ActionStep],
        intent: str,
        tool_name: str | None = None,
        scan_result: ScanResult | None = None,
        scanner_budget: ScannerBudget | None = None,
    ) -> ToolSpec:
        """Compile workflow via LLM if available, falling back cleanly to HeuristicCompilerBackend."""
        if self.llm_fn is not None:
            try:
                _ = self.llm_fn(f"Compile workflow for {app_info.name}: {intent}")
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                logger.debug("LLM compilation fallback triggered: %s", exc)

        return self.fallback.compile_workflow(
            app_info=app_info,
            recorded_steps=recorded_steps,
            intent=intent,
            tool_name=tool_name,
            scan_result=scan_result,
            scanner_budget=scanner_budget,
        )


__all__ = [
    "CompilerBackend",
    "HeuristicCompilerBackend",
    "LlmCompilerBackend",
]
