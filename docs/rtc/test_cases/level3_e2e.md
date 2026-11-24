# Level 3: End-to-End Test Specifications

Level 3 end-to-end tests exercise the complete `jit-mcp` system across process and transport boundaries. They validate the client-facing MCP protocol over stdio, real Linux desktop integration with AT-SPI2, dev-app vs holdout-app generalization, and zero-jargon usability through an independent blind agent evaluation.

---

## 1. Full MCP Client-to-Server Protocol Lifecycle (`WI-010`, `WI-014`)

### `TC-E-001`
- **Linked WI**: `WI-010`, `WI-014`
- **Why this test exists**: Ensures a standard MCP client (e.g. Claude Desktop, Goose, Claude Code, Cursor) can launch `jit-mcp` as a subprocess over stdio, complete protocol initialization, list apps, and scan an app UI.
- **Given**: A spawned `jit-mcp` server process communicating over standard input/output with an MCP client test harness.
- **When**:
  1. The client sends the standard MCP `initialize` request.
  2. The client calls `list_desktop_apps()`.
  3. The client calls `scan_app_ui(app_id="calculator")`.
- **Then**:
  1. The server returns protocol capabilities including dynamic tool support (`tools.listChanged = true`).
  2. `list_desktop_apps()` returns running applications with `app_id` and window title.
  3. `scan_app_ui()` returns the compact LLM view listing controls with IDs, names, roles, flattened `menu_path`, `shortcut`, and `enabled` states without exposing raw coordinates.

### `TC-E-002`
- **Linked WI**: `WI-005`, `WI-009`, `WI-010`, `WI-014`
- **Why this test exists**: Validates a complete multi-turn interaction cycle: scan -> enter text -> observe greyed-out button become enabled -> click button -> verify result.
- **Given**: An active MCP stdio session with `jit-mcp` connected to an application with an input and a submit button initially disabled.
- **When**:
  1. Client calls `scan_app_ui()`, observing `btn:submit` has `enabled=False`.
  2. Client calls `set_text(app_id="app", control_id_or_name="txt:input", text="Valid Query")`.
  3. Client calls `scan_app_ui()`, observing `btn:submit` now has `enabled=True`.
  4. Client calls `click_control(app_id="app", control_id_or_name="btn:submit")`.
- **Then**:
  1. Each step executes deterministically over MCP stdio.
  2. The final click succeeds without error, and the UI responds to the submission.

### `TC-E-003`
- **Linked WI**: `WI-007`, `WI-010`, `WI-014`
- **Why this test exists**: Verifies that workflow tools compiled with `save_workflow_tool` persist across server restarts and are immediately available on subsequent boots.
- **Given**: A multi-step workflow tool `calculator_add_numbers` saved to cache during session 1.
- **When**: The `jit-mcp` server process is terminated and restarted for session 2, and the client sends `tools/list`.
- **Then**: `calculator_add_numbers` is already listed in `tools/list` with its typed JSON schema parameters at startup, and calling it directly executes the workflow steps successfully.

### `TC-E-004`
- **Linked WI**: `WI-006`, `WI-010`, `WI-014`
- **Why this test exists**: Confirms that destructive actions enforce confirmation prompts end-to-end across the MCP transport.
- **Given**: An active client session connected to `jit-mcp`.
- **When**: The client calls `click_control(app_id="app", control_id_or_name="Delete Workspace", confirmed=False)`.
- **Then**: The server returns an MCP error or structured prompt stating `"Action 'Delete Workspace' requires confirmation: Destructive deletion action."` When the client repeats the call with `confirmed=True`, execution proceeds.

---

## 2. Live Linux X11 / AT-SPI2 Dev Verification: GTK3 Ground-Truth & LibreOffice Writer (`WI-004`, `WI-014`)

### `TC-E-005`
- **Linked WI**: `WI-004`, `WI-014`
- **Why this test exists**: Validates that `AtSpi2Adapter` extracts accurate widget names and exact screen coordinates matching live GTK3 widget geometry, detects dynamic sensitivity changes, executes native mouse events, and tests macro operations on a real desktop app (LibreOffice Writer).
- **Given**:
  - An active Linux X11 display (`DISPLAY`) with AT-SPI2 enabled (`GTK_MODULES=gail:atk-bridge` and `ACCESSIBILITY_ENABLED=1`).
  - A lightweight GTK3 test window launched in a background process containing:
    1. A `GtkEntry` text box.
    2. A `GtkButton` labeled `"Proceed"` that is initially greyed out (`gtk_widget_set_sensitive(btn, FALSE)` until the entry has text).
    3. A `GtkCheckButton` labeled `"Enable Feature"`.
    4. A `GtkComboBoxText` with options `["Alpha", "Beta", "Gamma"]`.
    5. A `GtkDrawingArea` canvas widget.
  - The GTK3 test harness queries its own ground-truth screen coordinates via `gdk_window_get_origin()` + `gtk_widget_get_allocation()`.
  - LibreOffice Writer (`/usr/bin/libreoffice --writer`) running in the background.
- **When**:
  1. `AtSpi2Adapter.list_apps()` discovers the GTK3 test window and LibreOffice Writer.
  2. `scanner.scan()` extracts all controls, roles, states, and `UiBounds` from the GTK3 window.
  3. Ground-truth widget coordinates from GTK3 are compared against `UiBounds` reported by `AtSpi2Adapter`.
  4. The test calls `click_control(..., "Proceed")` while disabled.
  5. The test calls `set_text(..., "GtkEntry", "Hello World")`.
  6. The test re-scans and calls `click_control(..., "Proceed")` now that it is enabled.
  7. The test toggles the checkbox, selects `"Beta"` from the dropdown, and draws a diagonal stroke across the `GtkDrawingArea` canvas using `draw_or_drag_path`.
  8. In LibreOffice Writer, the test calls `press_shortcut("libreoffice", "Ctrl+S")` and clicks `menu_path="File > Save"`.
- **Then**:
  1. The GTK3 test window and LibreOffice are discovered with exact window titles and process IDs.
  2. For every widget in the GTK3 window, `UiBounds.cx` and `UiBounds.cy` match the GTK3 ground-truth screen coordinates within **+/- 2 pixels**.
  3. Clicking `"Proceed"` while disabled raises `ControlDisabledError`.
  4. After `set_text`, the GTK3 app marks the button sensitive; re-scan shows `enabled=True`.
  5. The subsequent click on `"Proceed"` triggers the GTK button `clicked` signal in the live app.
  6. The checkbox toggles, the combo box active item changes to `"Beta"`, and the drawing area receives the mouse drag events and renders the stroke without using `xdotool`.
  7. In LibreOffice Writer, the shortcut and menu path trigger the save dialog successfully.

---

## 3. Generalization & Blind Usability on Unseen Holdout App (`WI-011`, `WI-014`)

### `TC-E-006`
- **Linked WI**: `WI-011`, `WI-014`
- **Why this test exists**: Proves zero overfitting to internal dev fixtures by running an adversarial and blind docs-only evaluation on an unseen holdout app (e.g. LibreOffice Calc, Mousepad, or Galculator) using an isolated `claude-sonnet-5-5-low` agent with access strictly limited to `README.md` and `docs/quickstart.md`.
- **Given**:
  - An unseen desktop application NOT used during development (e.g. LibreOffice Calc or Galculator).
  - An isolated `claude-sonnet-5-5-low` agent instance provided access **only** to `README.md` and `docs/quickstart.md` (no source code, no architecture document, no internal RTC files).
- **When**:
  1. The blind agent is prompted: *"You want to use jit-mcp with your MCP client to automate an unfamiliar desktop app. Follow the documentation to describe how to set it up, connect it, and run your first tool."*
  2. The system executes the agent's synthesized workflow against the holdout application.
- **Then**:
  1. The agent identifies the installation command (`pip install jit-mcp` or `uv tool run jit-mcp`) without errors.
  2. The agent outputs the exact, valid JSON client configuration snippet for stdio.
  3. The agent correctly explains the 2-stage workflow: list apps -> scan UI -> execute semantic actions (click, text, hover, draw, batch, shortcut) or save workflow tools.
  4. The agent explains how destructive action confirmations work (`confirmed=True`).
  5. The agent encounters zero unstated prerequisites or internal jargon.
  6. The holdout app responds accurately to the synthesized semantic commands, verifying generalization to arbitrary unfamiliar GUI toolkits and layouts without overfitting.
