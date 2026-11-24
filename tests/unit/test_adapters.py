"""Unit tests for AccessibilityAdapter protocol and AtSpi2Adapter (TC-U-007..TC-U-012).

Licensed under Apache-2.0.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jit_mcp.adapters.atspi2 import AtSpi2Adapter
from jit_mcp.adapters.base import (
    AccessibilityAdapter,
    AccessibilityDisabledWarning,
    AdapterUnavailableError,
    ControlDisabledError,
)
from jit_mcp.models import UiNode


def test_tc_u_007_accessibility_adapter_protocol_compliance() -> None:
    """TC-U-007: Incomplete adapter fails isinstance check; compliant passes."""

    class IncompleteAdapter:
        def get_root(self, app_id: str) -> UiNode:
            return UiNode(control_id="1", node_ref="r1", role="window")

    # Incomplete adapter lacking required methods fails protocol check
    assert not isinstance(IncompleteAdapter(), AccessibilityAdapter)

    # Compliant AtSpi2Adapter passes protocol check
    adapter = AtSpi2Adapter()
    assert isinstance(adapter, AccessibilityAdapter)


def test_tc_u_008_control_disabled_error_diagnostic_context() -> None:
    """TC-U-008: ControlDisabledError preserves app_id, control_id, and actionable message."""
    err = ControlDisabledError(
        app_id="org.gnome.TextEditor",
        control_id="btn:submit",
        reason="Control is greyed-out (enabled=False)",
    )
    assert err.app_id == "org.gnome.TextEditor"
    assert err.control_id == "btn:submit"
    assert err.reason == "Control is greyed-out (enabled=False)"
    assert "Cannot click control 'btn:submit': Control is greyed-out (enabled=False)" in str(
        err
    )


def test_tc_u_009_check_accessibility_enabled_warning() -> None:
    """TC-U-009: Emits AccessibilityDisabledWarning when toolkit-accessibility is false."""
    mock_subp = MagicMock()
    mock_subp.returncode = 0
    mock_subp.stdout = "false\n"

    with patch("subprocess.run", return_value=mock_subp):
        with pytest.warns(AccessibilityDisabledWarning) as record:
            enabled = AtSpi2Adapter.check_accessibility_enabled()

        assert enabled is False
        assert len(record) == 1
        msg = str(record[0].message)
        assert "gsettings set org.gnome.desktop.interface toolkit-accessibility true" in msg


def test_tc_u_010_atspi_missing_library_handling() -> None:
    """TC-U-010: Raises AdapterUnavailableError with guidance when Atspi cannot be imported."""
    with patch.dict("sys.modules", {"gi": None, "gi.repository": None, "gi.repository.Atspi": None}):
        with pytest.raises(AdapterUnavailableError) as exc_info:
            AtSpi2Adapter(atspi_module=None)

        msg = str(exc_info.value)
        assert "--system-site-packages" in msg or "gir1.2-atspi-2.0" in msg


def test_tc_u_011_convert_accessible_to_ui_node_mapping() -> None:
    """TC-U-011: Verify role mapping, enabled flag from SENSITIVE state, and bounds center."""
    mock_atspi = MagicMock()
    mock_atspi.Role.PUSH_BUTTON = 100
    mock_atspi.StateType.SENSITIVE = 1

    mock_state_set = MagicMock()
    # Missing SENSITIVE state simulates disabled/greyed-out control
    mock_state_set.contains.return_value = False
    mock_state_set.get_states.return_value = []

    mock_accessible = MagicMock()
    mock_accessible.get_name.return_value = "Save"
    mock_accessible.get_role.return_value = mock_atspi.Role.PUSH_BUTTON
    mock_accessible.get_role_name.return_value = "push button"
    mock_accessible.get_state_set.return_value = mock_state_set
    mock_accessible.get_child_count.return_value = 0

    # Bounding box (10, 20, 80, 30) -> cx=50, cy=35
    mock_rect = MagicMock()
    mock_rect.x = 10
    mock_rect.y = 20
    mock_rect.width = 80
    mock_rect.height = 30
    mock_accessible.get_extents.return_value = mock_rect

    adapter = AtSpi2Adapter(atspi_module=mock_atspi)
    node = adapter._convert_accessible_to_ui_node(mock_accessible)

    assert node.role == "button"
    assert node.name == "Save"
    assert node.enabled is False
    assert node.bounds is not None
    assert node.bounds.x == 10
    assert node.bounds.y == 20
    assert node.bounds.width == 80
    assert node.bounds.height == 30
    assert node.bounds.cx == 50
    assert node.bounds.cy == 35


def test_tc_u_012_synthesize_mouse_events_without_xdotool() -> None:
    """TC-U-012: Synthesizes mouse events directly via generate_mouse_event without subprocess."""
    mock_atspi = MagicMock()
    mock_atspi.generate_mouse_event.return_value = True

    adapter = AtSpi2Adapter(atspi_module=mock_atspi)

    # Click coordinates
    with patch("subprocess.run") as mock_subp:
        adapter.click_coordinates(250, 180)
        assert not mock_subp.called, "Must not spawn subprocesses (e.g. xdotool)!"

    # Mouse path
    points = [(250, 180), (300, 180), (300, 220)]
    with patch("subprocess.run") as mock_subp:
        adapter.mouse_path(points, button_down=True)
        assert not mock_subp.called, "Must not spawn subprocesses for mouse_path!"

    # Ensure generate_mouse_event was called for moves and button clicks
    assert mock_atspi.generate_mouse_event.called
