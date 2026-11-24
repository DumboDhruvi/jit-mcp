# Level 2: Subpart & Component Test Specifications

Level 2 component tests verify the integration across multiple modules within `jit-mcp` (e.g. Scanner -> Control Registry -> Safety -> Executor -> Cache -> FastMCP Server) using a realistic in-memory fake `AccessibilityAdapter`. They execute without needing an active display server or GUI process.

---

## 1. Dynamic Live-Scan & Semantic-Action Flow (`WI-005`, `WI-009`, `WI-013`)

### `TC-C-001`
- **Linked WI**: `WI-005`, `WI-009`, `WI-013`
- **Why this test exists**: Confirms the primary Two-Stage workflow: scan an app's UI to obtain compact control IDs, click an enabled control by ID or name, and verify the resulting UI state change.
- **Given**: A fake `AccessibilityAdapter` hosting a simulated application with a text input (`"txt:entry"`) and a button (`"btn:submit"`, `enabled=True`).
- **When**:
  1. `scanner.scan("demo.app")` is called.
  2. The caller executes `executor.click("demo.app", "btn:submit")`.
- **Then**:
  1. The scan produces a `ScanResult` with compact LLM view showing `"btn:submit"` with `enabled=True` and no pixel coordinates.
  2. The internal registry resolves `"btn:submit"` to its live node ref and `UiBounds`.
  3. The fake adapter receives the action, increments the submit click count, and reports success.

### `TC-C-002`
- **Linked WI**: `WI-005`, `WI-009`, `WI-013`
- **Why this test exists**: Verifies that `click_control` refuses to click a greyed-out control, but after typing into an input, a dynamic re-scan reflects `enabled=True` and allows clicking.
- **Given**: A simulated dialog where a "Proceed" button is initially greyed-out (`enabled=False`) until text is entered into an "Agreement" input field.
- **When**:
  1. The client attempts to click `"Proceed"`.
  2. The client invokes `executor.set_text("demo.app", "Agreement", "I AGREE")`.
  3. The client calls `scanner.scan("demo.app")` again.
  4. The client attempts to click `"Proceed"` once more.
- **Then**:
  1. First click attempt raises `ControlDisabledError` with an actionable message.
  2. `set_text` updates the fake UI state, triggering the simulated app logic to set `"Proceed"` `enabled=True`.
  3. Re-scan compact view lists `"Proceed"` as `enabled=True`.
  4. Second click attempt executes successfully.

### `TC-C-003`
- **Linked WI**: `WI-004`, `WI-009`, `WI-013`
- **Why this test exists**: Tests the full suite of rich interactive control operations: `hover_control`, `toggle_checkbox`, `select_dropdown`, and `scroll_control`.
- **Given**: A fake adapter with a menu bar item, a checkbox, a dropdown combo box, and a scrollable container.
- **When**:
  1. `executor.hover("demo.app", "File Menu")` is called.
  2. `executor.toggle_checkbox("demo.app", "Auto-Save")` is called.
  3. `executor.select_dropdown("demo.app", "Theme", "Dark")` is called.
  4. `executor.scroll_control("demo.app", "Document View", direction="down", amount=3)` is called.
- **Then**:
  1. Hover reveals child menu items in the fake tree.
  2. Checkbox state toggles from unchecked to checked.
  3. Dropdown value updates to `"Dark"`.
  4. Scroll container offset increases by 3 units.

### `TC-C-004`
- **Linked WI**: `WI-004`, `WI-009`, `WI-013`
- **Why this test exists**: Verifies that `draw_or_drag_path` accurately interpolates waypoints inside a canvas or divider control and emits continuous mouse path events.
- **Given**: A fake adapter exposing a drawing canvas with `UiBounds(x=100, y=100, width=200, height=200)` and a draggable split-pane divider.
- **When**:
  1. `executor.draw_or_drag_path("demo.app", "Canvas", points=[(0.0, 0.0), (1.0, 1.0)], normalized=True, button_down=True)` is called.
  2. `executor.draw_or_drag_path("demo.app", "Splitter", points=[(100, 150), (100, 250)], normalized=False, button_down=True)` is called.
- **Then**:
  1. Canvas receives mouse move to (100, 100), mouse down, mouse move to (300, 300), and mouse up.
  2. Splitter receives pixel drag from (100, 150) to (100, 250), resizing the simulated panes.

---

## 2. FastMCP Meta-Server Dynamic Tool Registration (`WI-010`, `WI-013`)

### `TC-C-005`
- **Linked WI**: `WI-010`, `WI-013`
- **Why this test exists**: Confirms that `FastMcpServer` initializes with the complete core meta-tool suite (including batching, shortcuts, and workflow persistence) registered with typed schemas.
- **Given**: An instantiated `FastMcpServer`.
- **When**: `server.list_tools()` is queried.
- **Then**: Returns the full meta-tool suite: `list_desktop_apps`, `scan_app_ui`, `click_control`, `hover_control`, `set_text`, `toggle_checkbox`, `select_dropdown`, `scroll_control`, `draw_or_drag_path`, `press_shortcut`, `execute_action_batch`, `save_workflow_tool`, `call_compiled_tool`.

### `TC-C-006`
- **Linked WI**: `WI-005`, `WI-010`, `WI-013`
- **Why this test exists**: Verifies that `scan_app_ui` returns a token-efficient compact view to the caller, strictly hiding raw pixel coordinates while storing `UiBounds` in the server's session registry.
- **Given**: A connected client calling `scan_app_ui(app_id="calculator")`.
- **When**: The response payload is inspected.
- **Then**: The returned payload contains controls with `control_id`, `name`, `role`, and `enabled`, but zero occurrences of `x, y, cx, cy`. The server's in-memory session registry holds the corresponding `UiBounds` mapped to each `control_id`.

### `TC-C-007`
- **Linked WI**: `WI-007`, `WI-010`, `WI-013`
- **Why this test exists**: Ensures that compiling and saving a multi-step workflow via `save_workflow_tool` persists it to disk cache, dynamically exposes it on the server, and emits `notifications/tools/list_changed`.
- **Given**: A sequence of verified actions recorded on app `"org.gnome.TextEditor"` and a client subscribed to MCP notifications.
- **When**: `server.save_workflow_tool(app_id="org.gnome.TextEditor", tool_name="create_note", steps=[...], intent="create a note")` is invoked.
- **Then**:
  1. A valid `ToolSpec` is saved to `$JIT_MCP_CACHE_DIR/org.gnome.TextEditor/create_note.json`.
  2. The server dynamically adds `texteditor_create_note` to its tool registry.
  3. A JSON-RPC notification `notifications/tools/list_changed` is emitted to the client session.

### `TC-C-008`
- **Linked WI**: `WI-010`, `WI-013`
- **Why this test exists**: Validates that clients without dynamic notification support can invoke saved workflow tools via `call_compiled_tool`.
- **Given**: A workflow tool `create_note` cached for `"org.gnome.TextEditor"`.
- **When**: `server.call_compiled_tool(app_id="org.gnome.TextEditor", tool_name="create_note", arguments={"title": "Test"})` is called.
- **Then**: The server executes the cached workflow step-by-step and returns the execution result.

---

## 3. Latency Optimization: Action Batching & Flattened Menus (`WI-005`, `WI-009`, `WI-010`, `WI-013`)

### `TC-C-009`
- **Linked WI**: `WI-009`, `WI-010`, `WI-013`
- **Why this test exists**: Verifies that `execute_action_batch` executes multiple actions in a single MCP turn and halts immediately if an action raises a confirmation challenge.
- **Given**: A batch containing: (1) `set_text("Doc Title")`, (2) `click_control("Delete Document", confirmed=False)`, (3) `click_control("Close")`.
- **When**: `server.execute_action_batch(app_id="demo.app", actions=batch, stop_on_error=True)` is called.
- **Then**: Step 1 executes; Step 2 detects `risk_tier="confirm"` with `confirmed=False`, pausing the batch and returning a confirmation prompt without executing Step 3.

### `TC-C-010`
- **Linked WI**: `WI-005`, `WI-009`, `WI-010`, `WI-013`
- **Why this test exists**: Ensures `click_control` targeting a leaf item with `menu_path` (e.g. `File > Export As > PDF`) automatically expands parent menus if unexpanded.
- **Given**: A closed menu structure where "Export As > PDF" is hidden until "File" and "Export As" are opened.
- **When**: `click_control(app_id="demo.app", control_id_or_name="File > Export As > PDF")` is called.
- **Then**: The executor follows the `menu_path` breadcrumb, clicks "File", hovers/clicks "Export As", and clicks "PDF" in one call without requiring multiple round-trips to the LLM.

---

## 4. Safety Confirmation Gating & Fallback Resolution (`WI-006`, `WI-009`, `WI-010`, `WI-013`)

### `TC-C-011`
- **Linked WI**: `WI-006`, `WI-010`, `WI-013`
- **Why this test exists**: Prevents destructive actions invoked via `click_control` or `call_compiled_tool` from executing when `confirmed=False`.
- **Given**: A control `"Delete File"` or workflow tool with `risk_tier="confirm"`.
- **When**: `server.click_control(app_id="demo.app", control_id_or_name="Delete File", confirmed=False)` is called.
- **Then**: Execution is paused, no adapter click is dispatched, and the server returns a confirmation prompt detailing the action and safety reason. When re-invoked with `confirmed=True`, the click is executed.

### `TC-C-012`
- **Linked WI**: `WI-006`, `WI-010`, `WI-013`
- **Why this test exists**: Ensures that actions in the `blocked` tier (e.g. system wipe or formatting) are rejected unconditionally, even if the caller supplies `confirmed=True`.
- **Given**: A control targeting `"Format Drive"`.
- **When**: `server.click_control(app_id="system", control_id_or_name="Format Drive", confirmed=True)` is called.
- **Then**: The safety guardrail raises `SecurityBlockedError`, rejecting the action completely.

### `TC-C-013`
- **Linked WI**: `WI-005`, `WI-009`, `WI-013`
- **Why this test exists**: Tests that when an application dynamically refreshes and a control's accessible name drifts, the executor falls back to cached `UiBounds` coordinates transparently.
- **Given**: A button originally scanned with `control_id="btn:1"`, name `"Submit"`, and `UiBounds(cx=150, cy=250)`. Between scan and execution, the app dynamically changes its label to `"Sending..."`.
- **When**: The client calls `click_control(app_id="demo.app", control_id_or_name="btn:1")`.
- **Then**: Semantic lookup by name fails, the executor accesses the cached `UiBounds(cx=150, cy=250)` from the registry, dispatches `click_coordinates(150, 250)`, and logs a successful coordinate fallback.
