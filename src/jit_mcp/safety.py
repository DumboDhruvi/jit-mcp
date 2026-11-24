"""Safety Guardrail: Risk classification and prompt injection defense.

Licensed under Apache-2.0.
"""

from __future__ import annotations

import re
from typing import Any

from jit_mcp.models import SafetyDecision, SafetyTier

# Fallback exceptions in case adapters/base.py is not yet loaded
try:
    from jit_mcp.adapters.base import ConfirmationRequiredError, SecurityBlockedError
except (ImportError, ModuleNotFoundError):

    class JitMcpError(Exception):
        """Base exception for all jit-mcp domain errors."""

    class SecurityBlockedError(JitMcpError):  # type: ignore[no-redef]
        """Action is permanently blocked for security reasons."""

    class ConfirmationRequiredError(JitMcpError):  # type: ignore[no-redef]
        """Action requires explicit user confirmation."""

        def __init__(self, action: str, reason: str = "") -> None:
            self.action = action
            self.reason = reason
            super().__init__(
                f"Action '{action}' requires explicit confirmation: {reason}"
            )


# Regex pattern to strip ANSI escape codes
_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# Patterns for permanently blocked high-risk actions (never allowed)
BLOCKED_PATTERNS = [
    r"\bformat\s+(?:disk|drive|partition|primary\s+partition)\b",
    r"\bwipe\s+(?:all\s+partitions|disk|drive)\b",
    r"\bmkfs\b",
    r"\bfdisk\b",
    r"\bparted\b",
    r"\bexport\s+(?:private\s+keys?|saved\s+passwords?|passwords?|keychain)\b",
    r"\bdump\s+(?:credentials|passwords|keychain)\b",
    r"\b(?:shutdown\s+system|system\s+shutdown|power\s*off)\b",
    r"\brm\s+-rf\b",
    r"\breboot\s+system\b",
    r"\bexecute\s+arbitrary\s+terminal\s+shell\b",
    r"\blaunch\s+root\s+terminal\b",
    r"\brun\s+shell\s+command\b",
]

# Patterns for destructive actions requiring user confirmation
CONFIRM_PATTERNS = [
    r"\bdelete\b",
    r"\bremove\b",
    r"\boverwrite\b",
    r"\bdrop\s+table\b",
    r"\bclear\s+all\b",
    r"\bsend\s+email\b",
    r"\bsubmit\s+payment\b",
    r"\bclose\s+without\s+saving\b",
    r"\bdiscard\s+changes\b",
    r"\buninstall\b",
    r"\berase\b",
    r"\btruncate\b",
    r"\bkill\s+process\b",
    r"\bterminate\s+process\b",
    r"\breset\s+to\s+factory\b",
]

MODIFIER_KEYS = frozenset({"alt", "ctrl", "meta", "shift", "super"})

# High-risk keyboard shortcuts permanently blocked (canonical: sorted modifiers + base key)
BLOCKED_SHORTCUTS = {
    "alt+ctrl+backspace",
    "alt+ctrl+del",
    "alt+ctrl+delete",
    "ctrl+alt+backspace",
    "ctrl+alt+del",
    "ctrl+alt+delete",
    "super+l",
}
for _f_idx in range(1, 13):
    BLOCKED_SHORTCUTS.add(f"alt+ctrl+f{_f_idx}")
    BLOCKED_SHORTCUTS.add(f"ctrl+alt+f{_f_idx}")

# Destructive keyboard shortcuts requiring user confirmation (canonical: sorted modifiers + base key)
CONFIRM_SHORTCUTS = {
    "alt+f4",
    "ctrl+q",
    "ctrl+w",
    "del+shift",
    "delete+shift",
    "shift+del",
    "shift+delete",
}


def sanitize_ui_label(raw: str) -> str:
    """Normalize untrusted UI labels into a safe inspection string.

    Strips ANSI escape codes, non-printable control characters, and collapses
    multiline whitespace into a single space while treating the entire label
    strictly as untrusted literal text (preventing prompt injection).
    """
    if not raw:
        return ""

    # Remove ANSI escape codes
    text = _ANSI_ESCAPE_RE.sub("", raw)

    # Remove non-printable control characters (keep printable ASCII and Unicode)
    text = "".join(ch for ch in text if ch >= " " or ch == "\t" or ch == "\n")

    # Collapse any sequence of whitespace (newlines, tabs, spaces) into a single space
    text = re.sub(r"\s+", " ", text).strip()

    return text


def normalize_shortcut_str(shortcut: str) -> str:
    """Normalize shortcut strings for deterministic matching (e.g. 'Alt + Ctrl + Del' -> 'alt+ctrl+del').

    Modifiers ('alt', 'ctrl', 'meta', 'shift', 'super') are sorted alphabetically
    before the base key, ensuring invariant matching regardless of modifier order.
    """
    if not shortcut:
        return ""
    clean = sanitize_ui_label(shortcut).lower()
    tokens = [t.strip() for t in re.split(r"[+\-\s]+", clean) if t.strip()]
    modifiers = sorted([t for t in tokens if t in MODIFIER_KEYS])
    base_keys = [t for t in tokens if t not in MODIFIER_KEYS]
    return "+".join(modifiers + base_keys)


class SafetyGuardrail:
    """Evaluates the risk tier of requested actions, controls, and workflows."""

    @classmethod
    def sanitize_label(cls, raw: str) -> str:
        """Convenience alias for sanitize_ui_label."""
        return sanitize_ui_label(raw)

    @classmethod
    def classify_shortcut(cls, shortcut: str) -> SafetyDecision:
        """Classify keyboard shortcut combinations into risk tiers."""
        norm = normalize_shortcut_str(shortcut)

        if norm in BLOCKED_SHORTCUTS:
            return SafetyDecision(
                risk_tier=SafetyTier.BLOCKED,
                requires_confirmation=False,
                reason=f"Keyboard shortcut '{shortcut}' is permanently blocked for system safety.",
            )

        if norm in CONFIRM_SHORTCUTS:
            return SafetyDecision(
                risk_tier=SafetyTier.CONFIRM,
                requires_confirmation=True,
                reason=f"Potentially destructive shortcut '{shortcut}' requires confirmation.",
            )

        return SafetyDecision(
            risk_tier=SafetyTier.SAFE,
            requires_confirmation=False,
            reason="Shortcut is safe to execute.",
        )

    @classmethod
    def classify_action(
        cls,
        action: str,
        control_name: str = "",
        intent: str = "",
    ) -> SafetyDecision:
        """Classify action, target control label, and intent into a SafetyDecision."""
        clean_action = sanitize_ui_label(action).lower()
        clean_name = sanitize_ui_label(control_name).lower()
        clean_intent = sanitize_ui_label(intent).lower()

        # Dedicated shortcut classification if action is shortcut-related
        if clean_action in ("shortcut", "press_shortcut", "key_press"):
            shortcut_candidate = control_name or intent
            if shortcut_candidate:
                sc_decision = cls.classify_shortcut(shortcut_candidate)
                if sc_decision.risk_tier != SafetyTier.SAFE:
                    return sc_decision

        # Explicit shortcut strings in control_name or intent
        if normalize_shortcut_str(control_name) in BLOCKED_SHORTCUTS:
            return SafetyDecision(
                risk_tier=SafetyTier.BLOCKED,
                requires_confirmation=False,
                reason=f"Action target matches prohibited shortcut '{control_name}'",
            )
        if normalize_shortcut_str(control_name) in CONFIRM_SHORTCUTS:
            return SafetyDecision(
                risk_tier=SafetyTier.CONFIRM,
                requires_confirmation=True,
                reason=f"Action target matches potentially destructive shortcut '{control_name}'",
            )
        if normalize_shortcut_str(intent) in BLOCKED_SHORTCUTS:
            return SafetyDecision(
                risk_tier=SafetyTier.BLOCKED,
                requires_confirmation=False,
                reason=f"Action intent matches prohibited shortcut '{intent}'",
            )
        if normalize_shortcut_str(intent) in CONFIRM_SHORTCUTS:
            return SafetyDecision(
                risk_tier=SafetyTier.CONFIRM,
                requires_confirmation=True,
                reason=f"Action intent matches potentially destructive shortcut '{intent}'",
            )

        # Combine into one inspection text to scan for blocked/confirm triggers
        combined = f"{clean_action} {clean_name} {clean_intent}".strip()

        # 1. Check BLOCKED triggers
        for pat in BLOCKED_PATTERNS:
            if re.search(pat, combined, re.IGNORECASE):
                matched = re.search(pat, combined, re.IGNORECASE).group(0)  # type: ignore[union-attr]
                return SafetyDecision(
                    risk_tier=SafetyTier.BLOCKED,
                    requires_confirmation=False,
                    reason=f"Action is permanently blocked: contains prohibited security pattern '{matched}'",
                )

        # 2. Check CONFIRM triggers
        for pat in CONFIRM_PATTERNS:
            if re.search(pat, combined, re.IGNORECASE):
                matched = re.search(pat, combined, re.IGNORECASE).group(0)  # type: ignore[union-attr]
                return SafetyDecision(
                    risk_tier=SafetyTier.CONFIRM,
                    requires_confirmation=True,
                    reason=f"Potentially destructive action requires confirmation: matched pattern '{matched}'",
                )

        # 3. Safe by default for standard read/scan/navigate operations
        return SafetyDecision(
            risk_tier=SafetyTier.SAFE,
            requires_confirmation=False,
            reason="Action is safe to execute",
        )

    @classmethod
    def classify_intent_safety(cls, intent: str) -> SafetyDecision:
        """Classify a high-level user intent or workflow description."""
        return cls.classify_action(action="", control_name="", intent=intent)

    @classmethod
    def classify_workflow_steps(cls, steps: list[Any]) -> SafetyDecision:
        """Inspect all steps in a workflow and return the highest risk tier decision."""
        has_confirm = False
        confirm_reason = ""

        for step in steps:
            action = ""
            target_name = ""
            value_template = ""

            if isinstance(step, dict):
                action = str(step.get("action", ""))
                raw_target = step.get("target")
                if isinstance(raw_target, dict):
                    target_name = str(raw_target.get("name") or "")
                elif hasattr(raw_target, "name"):
                    target_name = str(raw_target.name or "")
                elif isinstance(raw_target, str):
                    target_name = raw_target
                target_name = target_name or str(step.get("name") or step.get("control_id_or_name") or "")
                value_template = str(step.get("value_template") or step.get("value") or step.get("shortcut") or "")
            elif hasattr(step, "action"):
                action = getattr(step, "action", "")
                target = getattr(step, "target", None)
                if hasattr(target, "name"):
                    target_name = str(target.name or "")
                value_template = str(getattr(step, "value_template", "") or "")

            decision = cls.classify_action(
                action=action,
                control_name=target_name,
                intent=value_template,
            )

            if decision.risk_tier == SafetyTier.BLOCKED:
                return decision

            if decision.risk_tier == SafetyTier.CONFIRM:
                has_confirm = True
                confirm_reason = decision.reason

        if has_confirm:
            return SafetyDecision(
                risk_tier=SafetyTier.CONFIRM,
                requires_confirmation=True,
                reason=confirm_reason or "Workflow contains steps requiring confirmation.",
            )

        return SafetyDecision(
            risk_tier=SafetyTier.SAFE,
            requires_confirmation=False,
            reason="All workflow steps are safe.",
        )

    @classmethod
    def check_action(
        cls,
        action_name: str,
        control_info: Any = None,
        confirmed: bool = False,
    ) -> SafetyDecision:
        """Check action against control info (dict, object, or string) and return decision."""
        control_name = ""
        intent = ""

        if isinstance(control_info, str):
            control_name = control_info
        elif isinstance(control_info, dict):
            control_name = str(
                control_info.get("name")
                or control_info.get("control_id")
                or control_info.get("label", "")
            )
            intent = str(control_info.get("intent", ""))
        elif control_info is not None:
            control_name = str(
                getattr(control_info, "name", "")
                or getattr(control_info, "control_id", "")
            )

        decision = cls.classify_action(
            action=action_name,
            control_name=control_name,
            intent=intent,
        )

        return decision

    @classmethod
    def enforce(
        cls,
        decision: SafetyDecision,
        action_name: str = "action",
        confirmed: bool = False,
    ) -> None:
        """Raise appropriate security exceptions if the decision requires it."""
        # Resolve real exception classes from adapters.base if available
        sec_err_cls = SecurityBlockedError
        conf_err_cls = ConfirmationRequiredError
        try:
            from jit_mcp.adapters.base import (
                ConfirmationRequiredError as Cre,
            )
            from jit_mcp.adapters.base import (
                SecurityBlockedError as Sbe,
            )

            sec_err_cls = Sbe
            conf_err_cls = Cre
        except (ImportError, ModuleNotFoundError):
            pass

        if decision.risk_tier == SafetyTier.BLOCKED:
            raise sec_err_cls(decision.reason)

        if decision.risk_tier == SafetyTier.CONFIRM and not confirmed:
            raise conf_err_cls(action=action_name, reason=decision.reason)


# Module-level convenience functions
def classify_action(
    action: str, control_name: str = "", intent: str = ""
) -> SafetyDecision:
    """Classify action, target control label, and intent into a SafetyDecision."""
    return SafetyGuardrail.classify_action(
        action=action, control_name=control_name, intent=intent
    )


def classify_shortcut(shortcut: str) -> SafetyDecision:
    """Classify keyboard shortcut combinations into risk tiers."""
    return SafetyGuardrail.classify_shortcut(shortcut=shortcut)


def classify_workflow_steps(steps: list[Any]) -> SafetyDecision:
    """Inspect all steps in a workflow and return the highest risk tier decision."""
    return SafetyGuardrail.classify_workflow_steps(steps=steps)


def classify_intent_safety(intent: str) -> SafetyDecision:
    """Classify a high-level user intent or workflow description."""
    return SafetyGuardrail.classify_intent_safety(intent=intent)


def check_action(
    action_name: str, control_info: Any = None, confirmed: bool = False
) -> SafetyDecision:
    """Check action against control info and return SafetyDecision."""
    return SafetyGuardrail.check_action(
        action_name=action_name, control_info=control_info, confirmed=confirmed
    )


def enforce_safety(
    action_name: str,
    control_info: Any = None,
    intent: str = "",
    confirmed: bool = False,
) -> SafetyDecision:
    """Evaluate safety and enforce restrictions by raising exceptions if unconfirmed/blocked."""
    decision = check_action(
        action_name=action_name, control_info=control_info, confirmed=confirmed
    )
    SafetyGuardrail.enforce(
        decision, action_name=action_name, confirmed=confirmed
    )
    return decision


__all__ = [
    "BLOCKED_PATTERNS",
    "BLOCKED_SHORTCUTS",
    "CONFIRM_PATTERNS",
    "CONFIRM_SHORTCUTS",
    "MODIFIER_KEYS",
    "ConfirmationRequiredError",
    "SafetyGuardrail",
    "SecurityBlockedError",
    "check_action",
    "classify_action",
    "classify_intent_safety",
    "classify_shortcut",
    "classify_workflow_steps",
    "enforce_safety",
    "normalize_shortcut_str",
    "sanitize_ui_label",
]
