"""Unit tests for License compliance and domain models (TC-U-001..TC-U-006).

Licensed under Apache-2.0.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

import jit_mcp
from jit_mcp.models import (
    AppInfo,
    CompactControl,
    ScanResult,
    ToolSpec,
    UiBounds,
    UiNode,
)


def test_tc_u_001_license_compliance_and_dependencies() -> None:
    """TC-U-001: Verify permissive Apache-2.0 license and no copyleft dependencies."""
    root = Path(__file__).resolve().parent.parent.parent

    # 1. Check root LICENSE file
    license_file = root / "LICENSE"
    assert license_file.exists(), "LICENSE file must exist in repository root"
    content = license_file.read_text(encoding="utf-8")
    assert "Apache License" in content and "Version 2.0" in content

    # 2. Check pyproject.toml runtime dependencies
    pyproject_file = root / "pyproject.toml"
    assert pyproject_file.exists()
    data = tomllib.loads(pyproject_file.read_text(encoding="utf-8"))

    project = data.get("project", {})
    assert project.get("license") == "Apache-2.0" or "Apache" in str(project.get("license"))

    # Runtime dependencies must only include approved permissive libraries
    deps = project.get("dependencies", [])
    forbidden = ["gpl", "agpl", "xdotool"]
    for dep in deps:
        for f in forbidden:
            assert f not in dep.lower(), f"Forbidden dependency pattern '{f}' found in {dep}"


def test_tc_u_002_package_version_and_lazy_exports() -> None:
    """TC-U-002: Verify top-level exports and semantic version string."""
    assert hasattr(jit_mcp, "__version__")
    assert re.match(r"^\d+\.\d+\.\d+", jit_mcp.__version__)

    # Verify key model and adapter public exports
    assert jit_mcp.UiBounds is UiBounds
    assert jit_mcp.UiNode is UiNode
    assert jit_mcp.ToolSpec is ToolSpec


def test_tc_u_003_ui_bounds_center_calculation_and_validation() -> None:
    """TC-U-003: Verify UiBounds calculates center (cx, cy) and validates dimensions."""
    data = {"x": 100, "y": 200, "width": 50, "height": 30}
    bounds = UiBounds.model_validate(data)
    assert bounds.cx == 125
    assert bounds.cy == 215

    # Negative width must raise ValidationError
    with pytest.raises(ValidationError):
        UiBounds(x=10, y=20, width=-5, height=30)

    # Negative height must raise ValidationError
    with pytest.raises(ValidationError):
        UiBounds(x=10, y=20, width=50, height=-10)


def test_tc_u_004_ui_node_serialization_and_fields() -> None:
    """TC-U-004: Verify UiNode deserializes enabled, shortcut, menu_path, and bounds."""
    data = {
        "role": "button",
        "name": "Submit",
        "enabled": False,
        "shortcut": "Ctrl+S",
        "menu_path": "File > Save",
        "bounds": {"x": 10, "y": 20, "width": 80, "height": 30},
    }
    node = UiNode.model_validate(data)
    assert node.enabled is False
    assert node.shortcut == "Ctrl+S"
    assert node.menu_path == "File > Save"
    assert node.bounds is not None
    assert node.bounds.cx == 50
    assert node.bounds.cy == 35
    assert node.role == "button"


def test_tc_u_005_scan_result_compact_llm_view_zero_coordinates() -> None:
    """TC-U-005: Verify ScanResult compact LLM view strictly strips coordinates."""
    app = AppInfo(app_id="test.app", name="TestApp")
    controls = [
        CompactControl(
            control_id=f"ctrl_{i}",
            name=f"Button {i}",
            role="button",
            enabled=True,
            menu_path="Menu > Tools",
            shortcut="Ctrl+B",
        )
        for i in range(5)
    ]
    internal_registry = {
        f"ctrl_{i}": UiNode(
            control_id=f"ctrl_{i}",
            node_ref=f"ref_{i}",
            role="button",
            name=f"Button {i}",
            enabled=True,
            bounds=UiBounds(x=i * 10, y=i * 20, width=50, height=30),
        )
        for i in range(5)
    }

    scan_result = ScanResult(
        app=app,
        controls=controls,
        internal_registry=internal_registry,
        snapshot_hash="abcdef0123456789",
    )

    view = scan_result.to_compact_llm_view()
    json_str = json.dumps(view)

    # Internal registry retains full bounds
    assert len(scan_result.internal_registry) == 5
    assert scan_result.internal_registry["ctrl_0"].bounds.cx == 25

    # Compact LLM view must contain zero coordinate keys
    for coord_key in ("x", "y", "width", "height", "cx", "cy", "bounds"):
        assert not re.search(
            rf'"{coord_key}"\s*:', json_str
        ), f"Coordinate key '{coord_key}' leaked into compact view!"


def test_tc_u_006_tool_spec_schema_validation() -> None:
    """TC-U-006: Verify ToolSpec validates against schema v1.0.0 with coordinate_fallback."""
    data = {
        "schema_version": "1.0.0",
        "app": {
            "name": "Text Editor",
            "app_id": "org.gnome.TextEditor",
            "platform": "linux-atspi2",
        },
        "tool": {
            "name": "save_file",
            "description": "Saves current buffer to disk",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                },
                "required": ["filename"],
            },
        },
        "steps": [
            {
                "step_id": "step_1",
                "action": "click",
                "target": {"role": "button", "name": "Save"},
                "coordinate_fallback": {
                    "x": 100,
                    "y": 200,
                    "width": 50,
                    "height": 30,
                },
            }
        ],
        "postconditions": [
            {
                "type": "element_exists",
                "target": {"role": "label", "name": "Saved"},
            }
        ],
        "safety": {
            "risk_tier": "safe",
            "requires_confirmation": False,
            "reason": "Standard save operation",
        },
        "provenance": {
            "compiled_at": "2026-11-24T22:45:00Z",
            "intent": "Save file",
            "scanner_budget": {
                "max_depth": 6,
                "max_nodes": 250,
                "timeout_ms": 3000,
            },
            "snapshot_hash": "1234567890abcdef",
            "compiler_version": "heuristic-1.0.0",
        },
    }

    spec = ToolSpec.model_validate(data)
    assert spec.schema_version == "1.0.0"
    assert spec.app.app_id == "org.gnome.TextEditor"
    assert spec.tool.name == "save_file"
    assert len(spec.steps) == 1
    assert spec.steps[0].coordinate_fallback is not None
    assert spec.steps[0].coordinate_fallback.cx == 125
    assert spec.steps[0].coordinate_fallback.cy == 215
