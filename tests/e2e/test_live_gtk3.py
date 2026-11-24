"""Level 3 E2E test: Live Linux X11 / AT-SPI2 GTK3 ground-truth verification (TC-E-005).

Licensed under Apache-2.0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from jit_mcp.adapters.atspi2 import AtSpi2Adapter
from jit_mcp.adapters.base import ControlDisabledError
from jit_mcp.executor import ActionExecutor
from jit_mcp.scanner import UiScanner


def test_tc_e_005_live_gtk3_ground_truth(tmp_path: Path) -> None:
    """TC-E-005: Live Linux X11/AT-SPI2 GTK3 ground-truth comparison & semantic actions."""
    display = os.environ.get("DISPLAY")
    if not display:
        pytest.skip("Skipping live X11 test: no DISPLAY environment variable.")

    state_file = tmp_path / "gtk_state.json"
    harness_script = Path(__file__).parent / "gtk_harness.py"

    env = os.environ.copy()
    env["GTK_MODULES"] = "gail:atk-bridge"
    env["ACCESSIBILITY_ENABLED"] = "1"

    proc = subprocess.Popen(
        [
            sys.executable,
            str(harness_script),
            "--state-file",
            str(state_file),
            "--quit-after",
            "20",
        ],
        env=env,
    )

    try:
        # Wait for state file to be written with ground truth coordinates
        # Wait for X11 window manager to map and center the window on screen
        time.sleep(0.8)
        deadline = time.time() + 8.0
        ground_truth: dict[str, Any] = {}
        while time.time() < deadline:
            if state_file.exists():
                try:
                    with open(state_file, encoding="utf-8") as f:
                        data = json.load(f)
                        widgets = data.get("widgets", {})
                        # Wait until the window is centered/placed on screen (cx > 200)
                        if widgets.get("Proceed") and widgets.get("Query Entry", {}).get("cx", 0) > 200:
                            ground_truth = data
                            break
                except (json.JSONDecodeError, OSError):
                    continue
            time.sleep(0.2)

        assert ground_truth, f"GTK3 harness failed to write ground truth state to {state_file}"
        gt_widgets = ground_truth["widgets"]
        target_pid = ground_truth["pid"]

        # Step 2: Instantiate adapter, scanner, executor
        adapter = AtSpi2Adapter()
        scanner = UiScanner(adapter)
        executor = ActionExecutor(adapter, scanner=scanner)

        # Step 3: Discover app
        apps = adapter.list_apps()
        gtk_app = None
        for a in apps:
            if a.pid == target_pid or "gtk_harness" in a.app_id.lower() or "gtk_harness" in a.name.lower():
                gtk_app = a
                break

        assert gtk_app is not None, f"Could not find GTK3 harness (PID {target_pid}) in apps: {[a.app_id for a in apps]}"
        app_id = gtk_app.app_id

        # Step 4: Scan app UI
        scan_result = scanner.scan(app_id)
        compact_view = scan_result.to_compact_llm_view()
        compact_str = json.dumps(compact_view)

        # Assert zero coordinate keys leaked
        for coord_k in ("x", "y", "width", "height", "cx", "cy", "bounds"):
            assert f'"{coord_k}":' not in compact_str

        # Check required controls in compact view
        controls_by_name = {c.name: c for c in scan_result.controls}
        assert "Query Entry" in controls_by_name
        assert "Proceed" in controls_by_name
        assert controls_by_name["Proceed"].enabled is False
        assert "Enable Feature" in controls_by_name
        assert "Drawing Canvas" in controls_by_name

        # Compare ground-truth coordinates with internal registry UiBounds (+/- 2 pixels)
        for w_name, gt_coords in gt_widgets.items():
            reg_node = None
            for node in scan_result.internal_registry.values():
                if node.name == w_name:
                    reg_node = node
                    break
            if reg_node and reg_node.bounds:
                b = reg_node.bounds
                dx = abs(b.cx - gt_coords["cx"])
                dy = abs(b.cy - gt_coords["cy"])
                print(
                    f"Widget '{w_name}': AT-SPI2 (cx={b.cx}, cy={b.cy}) vs "
                    f"GTK3 (cx={gt_coords['cx']}, cy={gt_coords['cy']}) -> diff: (dx={dx}, dy={dy})"
                )
                assert dx <= 2, f"Widget '{w_name}' cx diff {dx} > 2px"
                assert dy <= 2, f"Widget '{w_name}' cy diff {dy} > 2px"

        # Step 5: Test dynamic interaction live
        # 5a. Click disabled Proceed -> raises ControlDisabledError
        with pytest.raises(ControlDisabledError):
            executor.click(app_id, "Proceed", scan_result=scan_result)

        # 5b. Set text in Query Entry
        executor.set_text(app_id, "Query Entry", "Hello World", scan_result=scan_result)
        time.sleep(0.5)

        # 5c. Re-scan shows Proceed is now enabled
        rescan = scanner.scan(app_id)
        rescan_by_name = {c.name: c for c in rescan.controls}
        assert rescan_by_name["Proceed"].enabled is True

        # 5d. Click Proceed
        executor.click(app_id, "Proceed", scan_result=rescan)
        time.sleep(0.5)
        with open(state_file, encoding="utf-8") as f:
            st = json.load(f)
        assert st["events"]["proceed_clicks"] >= 1

        # 5e. Toggle checkbox
        executor.toggle_checkbox(app_id, "Enable Feature", scan_result=rescan)
        time.sleep(0.5)
        with open(state_file, encoding="utf-8") as f:
            st = json.load(f)
        assert st["events"]["checkbox_active"] is True

        # 5f. Select dropdown option
        executor.select_dropdown(app_id, "Combo Box", "Beta", scan_result=rescan)
        time.sleep(0.5)
        with open(state_file, encoding="utf-8") as f:
            st = json.load(f)
        assert st["events"]["combo_active_text"] == "Beta"

        # 5g. Draw path on canvas
        executor.draw_or_drag_path(
            app_id,
            "Drawing Canvas",
            [(0.2, 0.2), (0.8, 0.8)],
            normalized=True,
            button_down=True,
            scan_result=rescan,
        )
        time.sleep(0.5)
        with open(state_file, encoding="utf-8") as f:
            st = json.load(f)
        assert st["events"]["canvas_strokes_count"] >= 1 or len(st["events"]["canvas_points"]) >= 2

    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
