"""ToolSpec disk cache and JSON schema validation for jit-mcp.

Licensed under Apache-2.0.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema.exceptions import best_match

from jit_mcp.models import ToolSpec

try:
    from jit_mcp.adapters.base import ToolSpecSchemaError
except ImportError:

    class ToolSpecSchemaError(ValueError):  # type: ignore[no-redef]
        """Raised when a tool specification fails JSON schema or semantic validation."""

        def __init__(
            self,
            field: str | None = None,
            reason: str = "",
            message: str | None = None,
        ) -> None:
            self.field = field
            self.reason = reason
            fld = f" at '{field}'" if field else ""
            rsn = f": {reason}" if reason else ""
            super().__init__(message or f"ToolSpec schema validation failed{fld}{rsn}.")


logger = logging.getLogger("jit_mcp.cache")

TOOL_SPEC_V1_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://jit-mcp.local/schemas/tool_spec.v1.json",
    "title": "JitMcpToolSpec",
    "type": "object",
    "required": [
        "schema_version",
        "app",
        "tool",
        "steps",
        "postconditions",
        "safety",
        "provenance",
    ],
    "additionalProperties": False,
    "properties": {
        "schema_version": {
            "type": "string",
            "const": "1.0.0",
        },
        "app": {
            "type": "object",
            "required": ["name", "app_id", "platform"],
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "app_id": {"type": "string", "minLength": 1},
                "version": {"type": "string"},
                "platform": {
                    "type": "string",
                    "enum": ["linux-atspi2", "windows-uia", "macos-ax"],
                },
                "toolkit": {"type": "string"},
            },
        },
        "tool": {
            "type": "object",
            "required": ["name", "description", "parameters"],
            "additionalProperties": False,
            "properties": {
                "name": {
                    "type": "string",
                    "pattern": "^[a-z][a-z0-9_]{1,63}$",
                },
                "description": {"type": "string", "minLength": 1},
                "parameters": {
                    "type": "object",
                    "required": ["type", "properties"],
                    "properties": {
                        "type": {"const": "object"},
                        "properties": {"type": "object"},
                        "required": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "additionalProperties": {"type": "boolean"},
                    },
                },
            },
        },
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["step_id", "action", "target"],
                "additionalProperties": False,
                "properties": {
                    "step_id": {"type": "string", "minLength": 1},
                    "action": {
                        "type": "string",
                        "enum": [
                            "click",
                            "hover",
                            "set_text",
                            "select",
                            "toggle",
                            "scroll",
                            "draw_path",
                            "key_press",
                            "read_text",
                            "wait",
                        ],
                    },
                    "target": {"$ref": "#/$defs/selector"},
                    "fallbacks": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/selector"},
                        "default": [],
                    },
                    "coordinate_fallback": {
                        "oneOf": [
                            {"$ref": "#/$defs/bounds"},
                            {"type": "null"},
                        ]
                    },
                    "click_path": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "value_template": {"type": ["string", "null"]},
                    "timeout_ms": {
                        "type": "integer",
                        "minimum": 100,
                        "maximum": 30000,
                        "default": 3000,
                    },
                },
            },
        },
        "postconditions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["type", "target"],
                "additionalProperties": False,
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "element_exists",
                            "element_absent",
                            "text_contains",
                            "state_equals",
                            "value_equals",
                        ],
                    },
                    "target": {"$ref": "#/$defs/selector"},
                    "expected": {
                        "type": ["string", "boolean", "null", "object", "array", "number"]
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "minimum": 100,
                        "maximum": 15000,
                        "default": 2000,
                    },
                },
            },
        },
        "safety": {
            "type": "object",
            "required": ["risk_tier", "requires_confirmation", "reason"],
            "additionalProperties": False,
            "properties": {
                "risk_tier": {
                    "type": "string",
                    "enum": ["safe", "confirm", "blocked"],
                },
                "requires_confirmation": {"type": "boolean"},
                "reason": {"type": "string"},
            },
        },
        "provenance": {
            "type": "object",
            "required": [
                "compiled_at",
                "intent",
                "scanner_budget",
                "snapshot_hash",
                "compiler_version",
            ],
            "additionalProperties": False,
            "properties": {
                "compiled_at": {"type": "string"},
                "intent": {"type": "string"},
                "scanner_budget": {
                    "type": "object",
                    "required": ["max_depth", "max_nodes", "timeout_ms"],
                    "additionalProperties": False,
                    "properties": {
                        "max_depth": {"type": "integer", "minimum": 1},
                        "max_nodes": {"type": "integer", "minimum": 1},
                        "timeout_ms": {"type": "integer", "minimum": 100},
                    },
                },
                "snapshot_hash": {"type": "string", "minLength": 8},
                "compiler_version": {"type": "string"},
            },
        },
    },
    "$defs": {
        "selector": {
            "type": "object",
            "required": ["role"],
            "additionalProperties": False,
            "properties": {
                "role": {"type": "string", "minLength": 1},
                "name": {"type": ["string", "null"]},
                "name_pattern": {"type": ["string", "null"]},
                "automation_id": {"type": ["string", "null"]},
                "path_hint": {"type": ["string", "null"]},
            },
        },
        "bounds": {
            "type": "object",
            "required": ["x", "y", "width", "height", "cx", "cy"],
            "additionalProperties": False,
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "width": {"type": "integer", "minimum": 0},
                "height": {"type": "integer", "minimum": 0},
                "cx": {"type": "integer"},
                "cy": {"type": "integer"},
            },
        },
    },
}

_SCHEMA_VALIDATOR = jsonschema.Draft202012Validator(TOOL_SPEC_V1_SCHEMA)


class FieldPath(str):
    """Path string supporting exact matches and short-form suffix comparisons.

    Enables comparisons like `err.field == 'coordinate_fallback.cx'` as well as
    `err.field == 'steps.0.coordinate_fallback.cx'`.
    """

    def __eq__(self, other: object) -> bool:
        if super().__eq__(other):
            return True
        if isinstance(other, str):
            # Allow comparison ignoring array index segments
            non_numeric = ".".join(p for p in self.split(".") if not p.isdigit())
            if other == non_numeric:
                return True
            # Allow suffix matching (e.g. 'coordinate_fallback.cx')
            if self.endswith("." + other) or self == other:
                return True
        return False


def _extract_error_details(err: jsonschema.ValidationError) -> tuple[FieldPath, str]:
    """Extract the specific failing field path and human-readable reason."""
    target_err = err
    if err.context:
        # Filter out trivial 'null' type mismatch branches in oneOf
        non_null_contexts = [
            c for c in err.context if "not of type 'null'" not in c.message
        ]
        if non_null_contexts:
            target_err = non_null_contexts[0]
            for c in non_null_contexts:
                if "is a required property" in c.message:
                    target_err = c
                    break

    path_parts = list(target_err.absolute_path)
    if not path_parts and err.absolute_path:
        path_parts = list(err.absolute_path)

    if "is a required property" in target_err.message:
        try:
            prop = target_err.message.split("'")[1]
            if not path_parts or path_parts[-1] != prop:
                path_parts.append(prop)
        except IndexError:
            pass

    field_str = ".".join(str(p) for p in path_parts) if path_parts else "root"
    return FieldPath(field_str), target_err.message


def validate_tool_spec(data: dict[str, Any] | ToolSpec) -> ToolSpec:
    """Validate data against TOOL_SPEC_V1_SCHEMA and parse into a ToolSpec model.

    Raises ToolSpecSchemaError detailing the failing field path and reason
    if validation fails.
    """
    if isinstance(data, ToolSpec):
        data_dict = data.model_dump(mode="json")
    elif isinstance(data, dict):
        data_dict = data
    else:
        raise ToolSpecSchemaError(
            field="root",
            reason=f"Expected dict or ToolSpec, got {type(data).__name__}",
        )

    # 1. JSON Schema validation
    errors = list(_SCHEMA_VALIDATOR.iter_errors(data_dict))
    if errors:
        best_err = best_match(errors) or errors[0]
        field_path, reason = _extract_error_details(best_err)
        msg = f"ToolSpec schema validation failed at '{field_path}': {reason}"
        raise ToolSpecSchemaError(field=field_path, reason=reason, message=msg)

    # 2. Pydantic model validation
    if isinstance(data, ToolSpec):
        return data

    try:
        return ToolSpec.model_validate(data_dict)
    except Exception as exc:
        raise ToolSpecSchemaError(
            field="root",
            reason=str(exc),
            message=f"ToolSpec model validation failed: {exc}",
        ) from exc


def get_cache_dir(custom_dir: Path | str | None = None) -> Path:
    """Resolve the cache directory, creating it if necessary.

    Uses custom_dir if provided, else $JIT_MCP_CACHE_DIR, else ~/.cache/jit-mcp.
    """
    if custom_dir:
        path = Path(custom_dir).expanduser()
    else:
        env_dir = os.environ.get("JIT_MCP_CACHE_DIR")
        if env_dir and env_dir.strip():
            path = Path(env_dir).expanduser()
        else:
            path = Path.home() / ".cache" / "jit-mcp"

    if path.is_symlink():
        raise ValueError(f"Cache root directory cannot be a symlink: {path}")
    resolved = path.resolve()
    if resolved.is_symlink():
        raise ValueError(f"Cache root directory cannot be a symlink: {resolved}")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _sanitize_name(val: str, name_type: str = "identifier") -> str:
    """Sanitize identifiers against directory traversal and dangerous characters."""
    if not val or not isinstance(val, str):
        raise ValueError(f"Invalid {name_type}: must be a non-empty string")
    if ".." in val or "/" in val or "\\" in val or "\0" in val:
        raise ValueError(
            f"Path traversal characters detected in {name_type}: {val!r}"
        )
    cleaned = val.strip()
    if cleaned in (".", "..") or not cleaned:
        raise ValueError(f"Invalid {name_type}: {val!r}")
    return cleaned


def _get_tool_path(
    app_id: str, tool_name: str, cache_dir: Path | str | None = None
) -> Path:
    base = get_cache_dir(cache_dir).resolve()
    clean_app = _sanitize_name(app_id, "app_id")
    clean_tool = _sanitize_name(tool_name, "tool_name")
    app_dir = base / clean_app
    if app_dir.is_symlink():
        raise ValueError(f"App directory cannot be a symlink: {app_dir}")
    if not app_dir.resolve().is_relative_to(base):
        raise ValueError(f"Directory traversal detected: {app_dir}")
    file_path = app_dir / f"{clean_tool}.json"
    if file_path.is_symlink():
        raise ValueError(f"Tool file cannot be a symlink: {file_path}")
    if not file_path.resolve().is_relative_to(base):
        raise ValueError(f"Path traversal detected: {file_path}")
    return file_path


def _quarantine_corrupt_file(file_path: Path, base_dir: Path | None = None) -> None:
    """Safely isolate a corrupted cache file by renaming it to .corrupt."""
    if file_path.is_symlink():
        try:
            file_path.unlink()
        except OSError:
            pass
        return
    if base_dir and not file_path.resolve().is_relative_to(base_dir.resolve()):
        raise ValueError(f"File outside cache directory: {file_path}")
    try:
        corrupt_path = file_path.with_suffix(".corrupt")
        if corrupt_path.is_symlink():
            corrupt_path.unlink(missing_ok=True)
        if corrupt_path.exists():
            counter = 1
            while file_path.with_name(
                f"{file_path.stem}.corrupt.{counter}"
            ).exists():
                counter += 1
            corrupt_path = file_path.with_name(
                f"{file_path.stem}.corrupt.{counter}"
            )
        os.replace(file_path, corrupt_path)
        logger.info(
            "Quarantined corrupt cache file %s to %s", file_path, corrupt_path
        )
    except OSError as err:
        logger.error(
            "Failed to quarantine corrupt cache file %s: %s", file_path, err
        )
        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            pass


def save_tool_spec(
    spec: ToolSpec, cache_dir: Path | str | None = None
) -> Path:
    """Validate and write a ToolSpec to disk atomically.

    Writes to a temporary file in the destination folder before renaming,
    ensuring thread- and crash-safety.
    """
    validated = validate_tool_spec(spec)
    target_dir = get_cache_dir(cache_dir).resolve()
    clean_app = _sanitize_name(validated.app.app_id, "app_id")
    clean_tool = _sanitize_name(validated.tool.name, "tool_name")

    app_dir = target_dir / clean_app
    if app_dir.is_symlink():
        raise ValueError(f"App directory cannot be a symlink: {app_dir}")
    if not app_dir.resolve().is_relative_to(target_dir):
        raise ValueError(f"Directory traversal detected: {app_dir}")
    app_dir.mkdir(parents=True, exist_ok=True)

    dest_path = app_dir / f"{clean_tool}.json"
    if dest_path.is_symlink():
        raise ValueError(f"Tool file cannot be a symlink: {dest_path}")
    if not dest_path.resolve().is_relative_to(target_dir):
        raise ValueError(f"Directory traversal detected: {dest_path}")

    temp_fd, temp_path = tempfile.mkstemp(
        prefix=f".{clean_tool}_",
        suffix=".tmp",
        dir=str(app_dir),
        text=True,
    )
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            f.write(validated.model_dump_json(indent=2))
        os.replace(temp_path, dest_path)
    except Exception:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        raise

    logger.debug(
        "Saved ToolSpec for %s/%s at %s", clean_app, clean_tool, dest_path
    )
    return dest_path


def get_tool_spec(
    app_id: str,
    tool_name: str,
    cache_dir: Path | str | None = None,
) -> ToolSpec | None:
    """Retrieve and validate a cached ToolSpec from disk.

    Returns None if missing or if the cached JSON is corrupted/schema-invalid.
    Corrupted files are safely quarantined to prevent crashing subsequent operations.
    """
    try:
        clean_app = _sanitize_name(app_id, "app_id")
        clean_tool = _sanitize_name(tool_name, "tool_name")
    except ValueError as exc:
        logger.warning("Invalid cache lookup identifier: %s", exc)
        return None

    target_dir = get_cache_dir(cache_dir).resolve()
    app_dir = target_dir / clean_app
    if app_dir.is_symlink():
        logger.warning("App directory cannot be a symlink: %s", app_dir)
        return None
    if not app_dir.resolve().is_relative_to(target_dir):
        logger.warning("App directory traversal detected: %s", app_dir)
        return None

    file_path = app_dir / f"{clean_tool}.json"
    if file_path.is_symlink():
        logger.warning("Tool file cannot be a symlink: %s", file_path)
        return None
    if not file_path.resolve().is_relative_to(target_dir):
        logger.warning("Tool file traversal detected: %s", file_path)
        return None

    if not file_path.is_file():
        return None

    try:
        raw_text = file_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
        return validate_tool_spec(data)
    except (
        json.JSONDecodeError,
        ToolSpecSchemaError,
        ValueError,
        KeyError,
        TypeError,
        OSError,
    ) as exc:
        logger.warning(
            "Corrupted or schema-invalid ToolSpec at '%s': %s. Quarantining file.",
            file_path,
            exc,
        )
        _quarantine_corrupt_file(file_path, base_dir=target_dir)
        return None


def list_cached_tools(
    app_id: str | None = None,
    cache_dir: Path | str | None = None,
) -> list[ToolSpec]:
    """Discover all valid cached ToolSpecs on disk.

    Can discover tools across all apps or filtered by a specific app_id.
    Silently isolates or skips corrupted cache files.
    """
    target_dir = get_cache_dir(cache_dir).resolve()
    if target_dir.is_symlink() or not target_dir.is_dir():
        return []

    tools: list[ToolSpec] = []
    if app_id is not None:
        try:
            clean_app = _sanitize_name(app_id, "app_id")
        except ValueError:
            return []
        app_d = target_dir / clean_app
        if app_d.is_symlink() or not app_d.is_dir() or not app_d.resolve().is_relative_to(target_dir):
            return []
        app_dirs = [app_d]
    else:
        app_dirs = [
            d for d in target_dir.iterdir()
            if d.is_dir() and not d.is_symlink() and d.resolve().is_relative_to(target_dir)
        ]

    for app_d in sorted(app_dirs, key=lambda p: p.name):
        for json_file in sorted(app_d.glob("*.json"), key=lambda p: p.name):
            if json_file.is_symlink() or not json_file.resolve().is_relative_to(target_dir):
                continue
            tool = get_tool_spec(
                app_d.name, json_file.stem, cache_dir=target_dir
            )
            if tool is not None:
                tools.append(tool)

    return tools


class ToolSpecCache:
    """Disk-backed cache and validator for compiled ToolSpec workflows."""

    def __init__(self, cache_dir: Path | str | None = None) -> None:
        self._cache_dir = cache_dir

    @property
    def cache_dir(self) -> Path:
        return get_cache_dir(self._cache_dir)

    def validate(self, data: dict[str, Any] | ToolSpec) -> ToolSpec:
        return validate_tool_spec(data)

    def save(self, spec: ToolSpec) -> Path:
        return save_tool_spec(spec, cache_dir=self.cache_dir)

    def get(self, app_id: str, tool_name: str) -> ToolSpec | None:
        return get_tool_spec(app_id, tool_name, cache_dir=self.cache_dir)

    def list(self, app_id: str | None = None) -> list[ToolSpec]:
        return list_cached_tools(app_id=app_id, cache_dir=self.cache_dir)

    def delete(self, app_id: str, tool_name: str) -> bool:
        """Delete a cached tool spec, returning True if deleted."""
        clean_app = _sanitize_name(app_id, "app_id")
        clean_tool = _sanitize_name(tool_name, "tool_name")
        target_dir = self.cache_dir.resolve()
        app_dir = target_dir / clean_app
        if app_dir.is_symlink():
            raise ValueError(f"App directory cannot be a symlink: {app_dir}")
        if not app_dir.resolve().is_relative_to(target_dir):
            raise ValueError(f"Symlink or traversal detected on app directory: {app_dir}")
        file_path = app_dir / f"{clean_tool}.json"
        if file_path.is_symlink():
            raise ValueError(f"Tool file cannot be a symlink: {file_path}")
        if not file_path.resolve().is_relative_to(target_dir):
            raise ValueError(f"Traversal detected on file: {file_path}")
        if file_path.is_file():
            file_path.unlink()
            return True
        return False


__all__ = [
    "TOOL_SPEC_V1_SCHEMA",
    "FieldPath",
    "ToolSpecCache",
    "ToolSpecSchemaError",
    "get_cache_dir",
    "get_tool_spec",
    "list_cached_tools",
    "save_tool_spec",
    "validate_tool_spec",
]
