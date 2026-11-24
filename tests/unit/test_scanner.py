"""Unit tests for Dynamic Accessibility Tree Scanner (WI-005, WI-012).

Tests TC-U-013 through TC-U-018.
Licensed under Apache-2.0.
"""

from __future__ import annotations

import json
import re

from jit_mcp.models import ScannerBudget
from jit_mcp.scanner import UiScanner

try:
    from tests.conftest import FakeAccessibilityAdapter
except ImportError:
    from conftest import FakeAccessibilityAdapter  # type: ignore[no-redef]


class TestScannerBudgetsAndDualViews:
    """Test suite covering tree budgets, pruning, coordinate privacy, and menu breadcrumbs."""

    def test_tc_u_013_max_depth_truncation(
        self, fake_adapter: FakeAccessibilityAdapter
    ) -> None:
        """TC-U-013: Tree scanning halts strictly at max_depth."""
        budget = ScannerBudget(max_depth=4, max_nodes=500, timeout_ms=5000)
        scanner = UiScanner(adapter=fake_adapter, budget=budget)

        result = scanner.scan("deep.tree")

        assert "node:d1" in result.internal_registry
        assert "node:d2" in result.internal_registry
        assert "node:d3" in result.internal_registry
        assert "node:d4" in result.internal_registry
        # Deeper nodes beyond max_depth=4 must be pruned
        assert "node:d5" not in result.internal_registry
        assert "node:d15" not in result.internal_registry

    def test_tc_u_014_max_nodes_truncation(
        self, fake_adapter: FakeAccessibilityAdapter
    ) -> None:
        """TC-U-014: Scanner halts immediately when max_nodes budget is reached."""
        budget = ScannerBudget(max_depth=10, max_nodes=40, timeout_ms=5000)
        scanner = UiScanner(adapter=fake_adapter, budget=budget)

        result = scanner.scan("wide.tree")

        assert result.truncated is True
        assert len(result.controls) <= 40
        assert any("max_nodes budget" in w for w in result.warnings)

    def test_tc_u_015_timeout_ms_abort(
        self, fake_adapter: FakeAccessibilityAdapter
    ) -> None:
        """TC-U-015: Scanning aborts cleanly without unhandled errors when timeout_ms is exceeded."""
        fake_adapter.child_delay_ms = 40
        budget = ScannerBudget(timeout_ms=100)
        scanner = UiScanner(adapter=fake_adapter, budget=budget)

        result = scanner.scan("demo.app")

        assert result.truncated is True
        assert any("timed out" in w for w in result.warnings)
        # Returns partial tree discovered so far without hanging
        assert len(result.internal_registry) > 0

    def test_tc_u_016_layout_only_wrapper_pruning(
        self, fake_adapter: FakeAccessibilityAdapter
    ) -> None:
        """TC-U-016: Layout wrappers and zero-size elements pruned, retaining interactive controls."""
        scanner = UiScanner(adapter=fake_adapter)

        result = scanner.scan("layout.prune.app")

        # 10 filler/zero-size containers are pruned from compact controls
        control_ids = {c.control_id for c in result.controls}
        assert "btn:enabled" in control_ids
        assert "btn:disabled" in control_ids
        assert not any(cid.startswith("filler:") for cid in control_ids)
        assert len(result.controls) == 2

        btn_enabled = next(
            c for c in result.controls if c.control_id == "btn:enabled"
        )
        btn_disabled = next(
            c for c in result.controls if c.control_id == "btn:disabled"
        )
        assert btn_enabled.enabled is True
        assert btn_disabled.enabled is False

    def test_tc_u_017_strict_coordinate_privacy_in_compact_view(
        self, fake_adapter: FakeAccessibilityAdapter
    ) -> None:
        """TC-U-017: to_compact_llm_view() strips all raw coordinate numbers and keys."""
        scanner = UiScanner(adapter=fake_adapter)
        result = scanner.scan("demo.app")

        compact_view = result.to_compact_llm_view()

        # The controls list must contain ZERO coordinate keys
        controls_json = json.dumps(compact_view["controls"])
        coordinate_patterns = [
            r'"x"\s*:',
            r'"y"\s*:',
            r'"width"\s*:',
            r'"height"\s*:',
            r'"cx"\s*:',
            r'"cy"\s*:',
            r'"bounds"\s*:',
        ]
        for pattern in coordinate_patterns:
            matches = re.findall(pattern, controls_json)
            assert not matches, f"Coordinate key leaked in compact view: {pattern}"

        # Verify internal registry still contains UiBounds for executor fallback
        assert any(
            node.bounds is not None
            for node in result.internal_registry.values()
        )

    def test_tc_u_018_menu_path_and_shortcut_extraction(
        self, fake_adapter: FakeAccessibilityAdapter
    ) -> None:
        """TC-U-018: Flattens menu hierarchy into breadcrumbs and captures shortcuts."""
        fake_adapter.demo_file_menu_hovered = True
        scanner = UiScanner(adapter=fake_adapter)

        result = scanner.scan("demo.app")

        export_node = next(
            c for c in result.controls if "Export as PDF" in c.name
        )
        assert export_node.shortcut == "Ctrl+Shift+E"
        assert export_node.menu_path == ["File Menu", "Export As", "Export as PDF"]

        save_node = next(c for c in result.controls if c.name == "Save")
        assert save_node.shortcut == "Ctrl+S"
        assert save_node.menu_path == ["File Menu", "Save"]

        # Deterministic snapshot hash
        assert result.snapshot_hash
        assert len(result.snapshot_hash) == 64
        # Rescanning identical state produces identical hash
        result_2 = scanner.scan("demo.app")
        assert result_2.snapshot_hash == result.snapshot_hash
