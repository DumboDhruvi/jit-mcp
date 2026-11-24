# Global Definition of Done (DoD)

A Work Item (`WI-*`) is considered **Done** (`status=done`, `dod_checked=true` in `docs/rtc/work_items.csv`) only when every applicable item below is satisfied:

1. **Code Quality & Modularity**
   - Changes touch only the Work Item's declared `owner_files`.
   - Every module is small, single-purpose, and cleanly typed (type annotations on public functions and models).
   - No unused imports, dead code, or hardcoded machine-specific paths.
   - Code passes linting/formatting checks (`ruff` or equivalent if configured).

2. **License Compliance**
   - Project is licensed under **Apache-2.0** (`LICENSE` present and declared in `pyproject.toml`).
   - All Python package dependencies (runtime and dev) use permissive commercial-friendly open-source licenses only (MIT, Apache-2.0, BSD, ISC).
   - Zero GPL or AGPL Python package dependencies (system `gi.repository.Atspi` dynamically linked LGPL-2.1+ on Linux is permitted).

3. **Given / When / Then Test Traceability**
   - Every behavior introduced or modified has a documented Given/When/Then test case in `docs/rtc/test_cases/` mapped 1:1 to an automated test function.

4. **Passing Tests at All Applicable Levels**
   - **Unit (`tests/unit/`)**: All unit tests for the touched module(s) pass.
   - **Component (`tests/component/`)**: All integration tests covering cross-module boundaries pass.
   - **E2E (`tests/e2e/`)**: Full MCP server lifecycle tests (and live X11/AT-SPI2 GTK smoke test where applicable) pass.

5. **Defect Gate**
   - Zero open `P0` or `P1` defects in `docs/rtc/defects.csv` linked to the Work Item (or globally before release).

6. **Simple Human-Facing Documentation**
   - `README.md` and `docs/quickstart.md` remain clear, accurate, and jargon-free.
   - Final release passes the blind `claude-sonnet-5-5-low` docs-only usability check with no unresolved usability blockers.
