# 60-Second Quickstart Guide

Get up and running with `jit-mcp` in under a minute to automate Linux desktop applications from your AI assistant.

---

## 1. Prerequisites & Installation

### System Requirements
- **OS:** Linux with X11 (GNOME, XFCE, Cinnamon, etc.). Works with GTK, Qt, and LibreOffice desktop applications via Linux desktop accessibility (AT-SPI2, the built-in accessibility service).
- **Python:** Python **3.12+**.
- **Virtual Environment Tip:** If you use a virtual environment, enable system site packages so desktop accessibility bindings (`python3-gi` / `gir1.2-atspi-2.0`) are visible:
  ```bash
  python3 -m venv --system-site-packages .venv
  source .venv/bin/activate
  ```

### Desktop Accessibility Check
If applications are not detected by `jit-mcp`, ensure system accessibility is enabled:
```bash
gsettings set org.gnome.desktop.interface toolkit-accessibility true
```
*(Restart your open desktop applications after running this command).*

### Install Package
Install `jit-mcp` using `pip` or run directly with `uv`:

```bash
# Using pip
pip install jit-mcp

# Or install from source
pip install -e .

# Or run directly via uv (works once published to PyPI; locally from source use: uv run --from . jit-mcp)
uv tool run jit-mcp
```

---

## 2. Add to Your MCP Client Configuration

Add `jit-mcp` to your MCP client configuration file (e.g., Claude Desktop, Cursor, or your custom host):

### Option A: Standard Stdio (Recommended)

```json
{
  "mcpServers": {
    "jit-mcp": {
      "command": "jit-mcp"
    }
  }
}
```

### Option B: UV Tool Runner

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

*(Restart your MCP client after updating the configuration).*

---

## 3. Automation Walkthrough (Steps 1 to 4)

### Step 1: List Running Applications
Discover open desktop applications and their identifiers:

```json
// Tool: list_desktop_apps
{}
```

*Example Response:*
```json
[
  {"app_id": "org.gnome.TextEditor", "name": "Text Editor", "window_title": "Untitled Document"},
  {"app_id": "calculator", "name": "Calculator", "window_title": "Calculator"}
]
```

---

### Step 2: Scan the Application UI
Inspect the application's available controls. The scan produces a clean list of controls with raw coordinates hidden to keep context concise:

```json
// Tool: scan_app_ui
{
  "app_id": "org.gnome.TextEditor"
}
```

*Example Response:*
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

You can target any control using its `control_id` (e.g. `"txt:doc"`) or its accessible `name` (e.g. `"Document Text"` or `"Save"`). If a button is disabled (`enabled: false`), clicks are refused until prerequisites are met.

---

### Step 3: Execute Actions or Action Batches

#### Single Action
Type text or click buttons directly:

```json
// Tool: set_text
{
  "app_id": "org.gnome.TextEditor",
  "control_id_or_name": "Document Text",
  "text": "Hello from jit-mcp!"
}
```

#### Action Batching (`execute_action_batch`)
Send multiple actions in a single turn to eliminate back-and-forth network delays:

```json
// Tool: execute_action_batch
{
  "app_id": "org.gnome.TextEditor",
  "actions": [
    {"action": "set_text", "control_id_or_name": "Document Text", "value": "Automated document draft"},
    {"action": "shortcut", "shortcut": "Ctrl+S"}
  ]
}
```

**Supported Batch Actions and Fields:**
- `{"action": "click", "control_id_or_name": "Save", "confirmed": false}`
- `{"action": "hover", "control_id_or_name": "File Menu"}`
- `{"action": "set_text", "control_id_or_name": "Document Text", "value": "My text"}`
- `{"action": "toggle", "control_id_or_name": "Auto-Save"}`
- `{"action": "select", "control_id_or_name": "Theme", "option": "Dark"}`
- `{"action": "scroll", "control_id_or_name": "ScrollArea", "direction": "down", "amount": 3}`
- `{"action": "draw_path", "control_id_or_name": "Canvas", "points": [[0.1, 0.1], [0.9, 0.9]], "normalized": true}`
- `{"action": "shortcut", "shortcut": "Ctrl+S"}`

*(Note: for single tools use `text` with `set_text(..., text=...)`; in batches, use `value` for the text string).*

---

### Step 4: Save & Run Reusable Workflows

#### 1. Save with Parameter Templates
Compile repeatable tasks into custom tools using dynamic placeholders like `{title}` and `{body}`.
Each step accepts `{"action", "target", "value_template"}`:
- `action`: the operation to run (e.g. `"set_text"`, `"click"`, `"shortcut"`).
- `target`: an object identifying the control (e.g. `{"name": "Document Text"}`).
- `value_template`: text or template string with placeholders (e.g. `{title}`, `{body}`).

```json
// Tool: save_workflow_tool
{
  "app_id": "org.gnome.TextEditor",
  "tool_name": "save_memo",
  "description": "Writes a titled memo and saves the document",
  "steps": [
    {
      "action": "set_text",
      "target": {"name": "Document Text"},
      "value_template": "Memo: {title}\nDate: 2026-11-24\n\n{body}"
    },
    {
      "action": "shortcut",
      "shortcut": "Ctrl+S"
    }
  ]
}
```

**Persistence:** Workflows are saved atomically to disk at `~/.cache/jit-mcp/<app_id>/<tool_name>.json` (or under `$JIT_MCP_CACHE_DIR`). They persist across restarts and are automatically loaded whenever `jit-mcp` boots.

#### 2. Execute Saved Workflow
Run your custom workflow anytime with `call_compiled_tool` (pass `"confirmed": true` if the workflow contains destructive steps):

```json
// Tool: call_compiled_tool
{
  "app_id": "org.gnome.TextEditor",
  "tool_name": "save_memo",
  "arguments": {
    "title": "Quarterly Planning",
    "body": "Deliverables and timeline confirmed."
  }
}
```

---

## 4. Safety Confirmations

Actions are automatically categorized by risk tier before execution:

- **Safe Actions**: Reading controls, UI scanning, typing text, and standard shortcuts (e.g. `Ctrl+S` to save, `Ctrl+F` to search). These execute immediately without prompts.
- **Destructive Actions**: Irreversible actions like "Delete File", "Overwrite Document", or "Close without Saving". The server will pause and return an explanation asking for confirmation. To execute, call the tool again with `"confirmed": true`:
  ```json
  // Tool: click_control
  {
    "app_id": "org.gnome.TextEditor",
    "control_id_or_name": "Delete File",
    "confirmed": true
  }
  ```
- **Blocked Actions**: Prohibited system-level commands (e.g., formatting a disk, shutting down the computer, or dumping credentials). These cannot be executed or confirmed.
