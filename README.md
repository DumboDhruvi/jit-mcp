# jit-mcp

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Control any Linux desktop application from your AI assistant using standard Model Context Protocol (MCP) tools. Instead of relying on brittle screen coordinates, slow mouse movements, or heavy vision models analyzing screenshots, `jit-mcp` connects directly to Linux desktop accessibility APIs (AT-SPI2, the built-in Linux accessibility system). This allows your assistant to inspect visible UI controls, click buttons, enter text, and run multi-step workflows in real time. It runs locally on your machine with zero cloud dependencies.

---

## Prerequisites

- **Operating System:** Linux with an X11 desktop environment (GNOME, XFCE, Cinnamon, etc.). Works with native GTK, Qt, and LibreOffice applications.
- **Python Version:** Python **3.12** or higher.
- **Python Virtual Environment Note:** When creating a Python virtual environment on Linux, pass `--system-site-packages` so that the desktop accessibility libraries (such as `python3-gi` / `gir1.2-atspi-2.0`) are accessible:
  ```bash
  python3 -m venv --system-site-packages .venv
  source .venv/bin/activate
  ```
- **Desktop Accessibility Setting:** If applications are not detected, make sure system accessibility is enabled:
  ```bash
  gsettings set org.gnome.desktop.interface toolkit-accessibility true
  ```
  *(Restart open desktop applications after running this command.)*

---

## Installation

Install `jit-mcp` with `pip` or run it with `uv`:

```bash
# Using pip
pip install jit-mcp

# Or install in editable mode from source
pip install -e .

# Or run directly via uv (works once published to PyPI; locally from source use: uv run --from . jit-mcp)
uv tool run jit-mcp
```

---

## MCP Client Configuration

Add `jit-mcp` to your MCP client configuration (such as Claude Desktop, Cursor, or your preferred MCP client):

### Standard Stdio Configuration

```json
{
  "mcpServers": {
    "jit-mcp": {
      "command": "jit-mcp"
    }
  }
}
```

### Direct UV Tool Configuration

```json
{
  "mcpServers": {
    "jit-mcp": {
      "command": "uv",
      "args": ["tool", "run", "jit-mcp"]
    }
  }
}
```

*(Restart your MCP client after updating the configuration file.)*

---

## How It Works: 2-Stage Interaction

`jit-mcp` operates in two straightforward stages:

### Stage 1: Discover & Inspect

1. **`list_desktop_apps()`**: Lists running desktop applications with their `app_id` and window title:
   ```json
   [
     {"app_id": "org.gnome.TextEditor", "name": "Text Editor", "window_title": "Untitled Document"},
     {"app_id": "calculator", "name": "Calculator", "window_title": "Calculator"}
   ]
   ```

2. **`scan_app_ui(app_id)`**: Inspects the target application and returns an interactive list of UI controls. Raw pixel coordinates are hidden to keep messages concise:
   ```json
   {
     "app": {"app_id": "org.gnome.TextEditor", "name": "Text Editor"},
     "controls": [
       {"control_id": "txt:doc", "name": "Document Text", "role": "text_input", "enabled": true},
       {"control_id": "btn:save", "name": "Save", "role": "button", "enabled": true, "shortcut": "Ctrl+S"},
       {"control_id": "btn:delete", "name": "Delete File", "role": "button", "enabled": true},
       {"control_id": "btn:submit", "name": "Proceed", "role": "button", "enabled": false}
     ]
   }
   ```
   You can identify any control by passing its `control_id` (e.g. `"txt:doc"`) or its accessible `name` (e.g. `"Save"`) directly to the action tools. If a button is greyed out (`enabled: false`), the server refuses the click with an error until it becomes active.

---

### Stage 2: Interact & Automate

#### Individual Semantic Tools

- **`click_control(app_id, control_id_or_name, confirmed=False)`**: Clicks a button, menu item, or tab. Automatically opens parent menus if clicking a nested item (e.g. `File > Save`).
- **`set_text(app_id, control_id_or_name, text)`**: Replaces the text content of an entry or document view using the `text` parameter.
- **`toggle_checkbox(app_id, control_id_or_name)`**: Toggles a checkbox between checked and unchecked.
- **`select_dropdown(app_id, control_id_or_name, option)`**: Selects an item from a combo box or dropdown.
- **`hover_control(app_id, control_id_or_name)`**: Hovers the cursor over a menu or button to reveal tooltips or submenus.
- **`scroll_control(app_id, control_id_or_name, direction="down", amount=3)`**: Scrolls a list, table, or window up, down, left, or right.
- **`draw_or_drag_path(app_id, control_id_or_name, points, normalized=True, button_down=True)`**: Draws strokes on a canvas or drags a splitter using normalized `[x, y]` coordinates (`0.0` to `1.0`).
- **`press_shortcut(app_id, shortcut)`**: Synthesizes key combinations directly on the application (e.g. `"Ctrl+S"`, `"Ctrl+Shift+P"`, `"Alt+F4"`).

#### Action Batching: `execute_action_batch`

To perform multiple actions in a single message without waiting for back-and-forth network turns, call `execute_action_batch(app_id, actions, stop_on_error=True)`.

Each item in `actions` supports the following exact fields:

| Action (`"action"`) | Supported Fields | Purpose |
| :--- | :--- | :--- |
| `"click"` | `control_id_or_name`, `confirmed` (bool) | Click a button, tab, or menu item |
| `"hover"` | `control_id_or_name` | Hover pointer over a control |
| `"set_text"` | `control_id_or_name`, `value` (string) | Enter text into an input or editor |
| `"toggle"` | `control_id_or_name` | Toggle a checkbox or switch |
| `"select"` | `control_id_or_name`, `option` or `value` | Select an option in a dropdown |
| `"scroll"` | `control_id_or_name`, `direction` ("down"/"up"), `amount` (int) | Scroll through content |
| `"draw_path"` | `control_id_or_name`, `points` (list of [x, y]), `normalized` (bool) | Draw lines or drag dividers |
| `"shortcut"` | `shortcut` or `value` (e.g. `"Ctrl+S"`) | Synthesize keyboard shortcuts |

*Batch Example:*
```json
{
  "app_id": "org.gnome.TextEditor",
  "actions": [
    {"action": "set_text", "control_id_or_name": "Document Text", "value": "Meeting Notes"},
    {"action": "shortcut", "shortcut": "Ctrl+S"}
  ]
}
```

---

## Saving and Running Repeatable Workflows

Compile multi-step sequences into named, reusable tools that persist across server and system restarts.

### 1. Save a Workflow: `save_workflow_tool`

Each step in `save_workflow_tool` accepts `{"action", "target", "value_template"}`:
- `action`: the operation to run (e.g. `"set_text"`, `"click"`, `"shortcut"`).
- `target`: an object identifying the control (e.g. `{"name": "Document Text"}` or `{"role": "button", "name": "Save"}`).
- `value_template`: text or template string with placeholders (e.g. `{title}`, `{body}`).

Workflows support dynamic template placeholders like `{title}` and `{body}`:

```json
// Tool: save_workflow_tool
{
  "app_id": "org.gnome.TextEditor",
  "tool_name": "create_note",
  "description": "Writes a titled note and saves the file",
  "steps": [
    {
      "action": "set_text",
      "target": {"name": "Document Text"},
      "value_template": "Title: {title}\nDate: 2026-11-24\n\n{body}"
    },
    {
      "action": "shortcut",
      "shortcut": "Ctrl+S"
    }
  ]
}
```

Saved tools are validated and stored atomically on disk in `~/.cache/jit-mcp/<app_id>/<tool_name>.json` (or under the `$JIT_MCP_CACHE_DIR` environment variable). They persist across restarts and are automatically loaded whenever the server starts.

### 2. Execute a Saved Workflow: `call_compiled_tool`

Call your saved workflow anytime by supplying the required arguments (if the workflow contains destructive steps, pass `"confirmed": true` to authorize execution):

```json
// Tool: call_compiled_tool
{
  "app_id": "org.gnome.TextEditor",
  "tool_name": "create_note",
  "arguments": {
    "title": "Roadmap Sync",
    "body": "Finalized sprint goals and deliverables."
  }
}
```

*(In supporting MCP clients, saved tools also register dynamically as first-class tools).*

---

## Safety Guardrails & Confirmations

To protect against unintended data loss, `jit-mcp` classifies every action into three risk tiers before execution:

1. **Safe Actions (Immediate Execution)**:
   - Reading UI states, scanning windows, entering non-destructive text, clicking menus/tabs, and shortcuts like standard save (`Ctrl+S`) or find (`Ctrl+F`).
   - Run immediately without confirmation prompts.

2. **Destructive Actions (Explicit Confirmation Required)**:
   - Irreversible operations such as clicking "Delete File", "Remove Item", "Overwrite Existing File", "Drop Table", or "Close without Saving".
   - The server will pause and return a prompt explaining why confirmation is required.
   - To proceed, re-run the action with `"confirmed": true` (e.g. `click_control(..., confirmed=True)`).

3. **Blocked Actions (Strictly Prohibited)**:
   - Dangerous system-level commands like formatting drives, deleting root filesystems, shutting down the OS, or exporting private keys.
   - These actions are blocked under all circumstances and cannot be bypassed.

---

## License

This project is licensed under the [Apache-2.0 License](LICENSE).
