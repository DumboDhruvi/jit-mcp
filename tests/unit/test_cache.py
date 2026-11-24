"""Unit tests for ToolSpec disk cache and JSON schema validator (WI-007, WI-012).

Tests TC-U-023, TC-U-024, TC-U-025.
Licensed under Apache-2.0.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jit_mcp.cache import (
    ToolSpecCache,
    ToolSpecSchemaError,
    get_tool_spec,
    list_cached_tools,
    save_tool_spec,
    validate_tool_spec,
)
from jit_mcp.models import (
    ActionStep,
    Postcondition,
    ScannerBudget,
    Selector,
    ToolAppInfo,
    ToolDefinition,
    ToolProvenance,
    ToolSafety,
    ToolSpec,
    UiBounds,
)


@pytest.fixture
def sample_valid_spec() -> ToolSpec:
    """Provide a valid, canonical ToolSpec instance."""
    return ToolSpec(
        app=ToolAppInfo(
            name="gedit",
            app_id="org.gnome.gedit",
            platform="linux-atspi2",
            version="46.0",
        ),
        tool=ToolDefinition(
            name="create_file",
            description="Create a new document in gedit",
            parameters={
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                },
            },
        ),
        steps=[
            ActionStep(
                step_id="step_1",
                action="click",
                target=Selector(role="push button", name="New"),
                coordinate_fallback=UiBounds(
                    x=10, y=20, width=30, height=40, cx=25, cy=40
                ),
            )
        ],
        postconditions=[
            Postcondition(
                type="element_exists",
                target=Selector(role="text_input", name="Document"),
                timeout_ms=2000,
            )
        ],
        safety=ToolSafety(
            risk_tier="safe",
            requires_confirmation=False,
            reason="Non-destructive UI action",
        ),
        provenance=ToolProvenance(
            compiled_at="2026-11-24T00:00:00Z",
            intent="create a new file",
            scanner_budget=ScannerBudget(
                max_depth=6, max_nodes=250, timeout_ms=3000
            ),
            snapshot_hash="abcdef0123456789",
            compiler_version="0.1.0",
        ),
    )


class TestToolSpecSchemaValidation:
    """Test suite for validate_tool_spec and TOOL_SPEC_V1_SCHEMA (TC-U-023)."""

    def test_tc_u_023_coordinate_fallback_missing_cx_or_cy(
        self, sample_valid_spec: ToolSpec
    ) -> None:
        """TC-U-023: Catches coordinate_fallback lacking required cx/cy."""
        raw_dict = sample_valid_spec.model_dump(mode="json")
        # Strip required cx coordinate from step 0
        del raw_dict["steps"][0]["coordinate_fallback"]["cx"]

        with pytest.raises(ToolSpecSchemaError) as exc_info:
            validate_tool_spec(raw_dict)

        err = exc_info.value
        assert "coordinate_fallback" in err.field
        assert "cx" in err.field
        assert err.field == "coordinate_fallback.cx"
        assert "cx" in str(err)
        assert "required" in str(err).lower()

    def test_tc_u_023_coordinate_fallback_missing_cy(
        self, sample_valid_spec: ToolSpec
    ) -> None:
        """TC-U-023 variant: Catches coordinate_fallback lacking cy."""
        raw_dict = sample_valid_spec.model_dump(mode="json")
        del raw_dict["steps"][0]["coordinate_fallback"]["cy"]

        with pytest.raises(ToolSpecSchemaError) as exc_info:
            validate_tool_spec(raw_dict)

        err = exc_info.value
        assert err.field == "coordinate_fallback.cy"
        assert "cy" in str(err)

    def test_validate_valid_spec_instance(
        self, sample_valid_spec: ToolSpec
    ) -> None:
        """Valid ToolSpec object passes validation and returns itself."""
        validated = validate_tool_spec(sample_valid_spec)
        assert validated is sample_valid_spec

    def test_validate_valid_dict(self, sample_valid_spec: ToolSpec) -> None:
        """Valid dictionary is parsed into a Pydantic ToolSpec model."""
        raw_dict = sample_valid_spec.model_dump(mode="json")
        validated = validate_tool_spec(raw_dict)
        assert isinstance(validated, ToolSpec)
        assert validated.app.app_id == "org.gnome.gedit"
        assert validated.tool.name == "create_file"

    def test_schema_error_missing_provenance_snapshot_hash(
        self, sample_valid_spec: ToolSpec
    ) -> None:
        """Validates that missing required field produces exact field path."""
        raw_dict = sample_valid_spec.model_dump(mode="json")
        del raw_dict["provenance"]["snapshot_hash"]

        with pytest.raises(ToolSpecSchemaError) as exc_info:
            validate_tool_spec(raw_dict)

        err = exc_info.value
        assert err.field == "provenance.snapshot_hash"
        assert "snapshot_hash" in str(err)

    def test_schema_error_invalid_action_enum(
        self, sample_valid_spec: ToolSpec
    ) -> None:
        """Validates that unrecognized action string fails validation."""
        raw_dict = sample_valid_spec.model_dump(mode="json")
        raw_dict["steps"][0]["action"] = "teleport"

        with pytest.raises(ToolSpecSchemaError) as exc_info:
            validate_tool_spec(raw_dict)

        err = exc_info.value
        assert "action" in err.field
        assert "teleport" in str(err)

    def test_schema_error_non_dict_input(self) -> None:
        """Passing non-dict and non-ToolSpec input raises ToolSpecSchemaError."""
        with pytest.raises(ToolSpecSchemaError):
            validate_tool_spec("invalid string input")  # type: ignore[arg-type]


class TestToolSpecCachePersistence:
    """Test suite for save_tool_spec and get_tool_spec (TC-U-024, TC-U-025)."""

    def test_tc_u_024_save_and_retrieve_identically(
        self, sample_valid_spec: ToolSpec, temp_cache_dir: Path
    ) -> None:
        """TC-U-024: Saved tool spec is persisted namespaced and retrieved identically."""
        dest_path = save_tool_spec(sample_valid_spec)

        expected_path = (
            temp_cache_dir / "org.gnome.gedit" / "create_file.json"
        )
        assert dest_path == expected_path
        assert dest_path.is_file()

        loaded = get_tool_spec("org.gnome.gedit", "create_file")
        assert loaded is not None
        assert loaded.schema_version == "1.0.0"
        assert loaded.app.app_id == sample_valid_spec.app.app_id
        assert loaded.tool.name == sample_valid_spec.tool.name
        assert loaded.tool.description == sample_valid_spec.tool.description
        assert len(loaded.steps) == len(sample_valid_spec.steps)
        assert (
            loaded.steps[0].coordinate_fallback.cx
            == sample_valid_spec.steps[0].coordinate_fallback.cx
        )
        assert (
            loaded.provenance.snapshot_hash
            == sample_valid_spec.provenance.snapshot_hash
        )

    def test_tc_u_025_corrupted_json_cache_file(
        self, temp_cache_dir: Path
    ) -> None:
        """TC-U-025: Truncated JSON returns None and is quarantined safely."""
        app_dir = temp_cache_dir / "org.gnome.gedit"
        app_dir.mkdir(parents=True, exist_ok=True)
        broken_file = app_dir / "broken_tool.json"
        broken_file.write_text(
            '{"schema_version": "1.0.0", "app":', encoding="utf-8"
        )

        res = get_tool_spec("org.gnome.gedit", "broken_tool")
        assert res is None

        # The broken file must be isolated / renamed to .corrupt
        assert not broken_file.exists()
        corrupt_files = list(app_dir.glob("broken_tool.corrupt*"))
        assert len(corrupt_files) >= 1

        # Subsequent call does not crash and returns None
        assert get_tool_spec("org.gnome.gedit", "broken_tool") is None

    def test_missing_cache_file_returns_none(
        self, temp_cache_dir: Path
    ) -> None:
        """Querying a non-existent tool returns None."""
        assert get_tool_spec("nonexistent.app", "missing_tool") is None


class TestToolSpecCacheListingAndSafety:
    """Test suite for tool discovery, traversal defense, and ToolSpecCache wrapper."""

    def test_list_cached_tools_across_apps(
        self, sample_valid_spec: ToolSpec, temp_cache_dir: Path
    ) -> None:
        """Listing cached tools discovers specs across apps and supports filtering."""
        save_tool_spec(sample_valid_spec)

        calc_spec = sample_valid_spec.model_copy(deep=True)
        calc_spec.app.app_id = "calculator"
        calc_spec.tool.name = "add_numbers"
        save_tool_spec(calc_spec)

        all_tools = list_cached_tools()
        assert len(all_tools) == 2
        names = {t.tool.name for t in all_tools}
        assert names == {"create_file", "add_numbers"}

        gedit_tools = list_cached_tools(app_id="org.gnome.gedit")
        assert len(gedit_tools) == 1
        assert gedit_tools[0].tool.name == "create_file"

        calc_tools = list_cached_tools(app_id="calculator")
        assert len(calc_tools) == 1
        assert calc_tools[0].tool.name == "add_numbers"

        empty_tools = list_cached_tools(app_id="non.existent.app")
        assert empty_tools == []

    def test_path_traversal_protection(self, temp_cache_dir: Path) -> None:
        """Attempts to access paths with traversal characters are blocked."""
        for traversal_input in [
            "../etc/passwd",
            "app/sub",
            "..",
            "app\\sub",
            "",
        ]:
            # get_tool_spec catches invalid identifiers and safely returns None
            assert get_tool_spec(traversal_input, "tool") is None
            assert get_tool_spec("app", traversal_input) is None

    def test_tool_spec_cache_wrapper_class(
        self, sample_valid_spec: ToolSpec, temp_cache_dir: Path
    ) -> None:
        """ToolSpecCache class provides clean object-oriented facade."""
        cache = ToolSpecCache()
        assert cache.cache_dir == temp_cache_dir

        path = cache.save(sample_valid_spec)
        assert path.is_file()

        retrieved = cache.get("org.gnome.gedit", "create_file")
        assert retrieved is not None
        assert retrieved.tool.name == "create_file"

        tools = cache.list()
        assert len(tools) == 1

        deleted = cache.delete("org.gnome.gedit", "create_file")
        assert deleted is True
        assert cache.get("org.gnome.gedit", "create_file") is None
        assert cache.list() == []
        assert cache.delete("org.gnome.gedit", "create_file") is False
