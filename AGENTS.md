# Repository Guidelines

## Project Structure & Module Organization
- Core Python runtime lives in `src/mayflower_sandbox`, with `sandbox_executor.py`, `manager.py`, and helper subpackages for document tooling.
- TypeScript glue code (`executor.ts`, `deno.json`) resides alongside the Python modules; keep Python and Deno updates in sync.
- Integration and regression coverage is under `tests/`, mirroring tool names (for example `test_file_write.py`).
- Database setup SQL is in `migrations/`, while contributor scripts such as `setup-test-db.sh` are in `scripts/`.
- Reference docs and walkthroughs are stored in `docs/`; update them when behavior changes.

## Build, Test, and Development Commands
- `make install` — install the package in editable mode with dev extras.
- `make db-setup` — launch PostgreSQL via Docker and apply migrations for local testing.
- `make test` or `pytest -v` — run the full pytest suite, including async and LangGraph scenarios.
- `make lint` / `make format` — run Ruff linting or formatting; prefer `make lint` before submitting.
- `make typecheck` — execute mypy against `src/mayflower_sandbox`.
- `make quality` — aggregate lint, format-check, and typecheck for pre-PR verification.

## Coding Style & Naming Conventions
- Python uses Ruff with 4-space indentation, 100-character lines, and PEP-8 naming; tests allow looser naming via per-file ignores.
- Format Python via `ruff format`; avoid manual reflow.
- TypeScript code should follow Deno defaults; keep module filenames lowercase with underscores.
- Prefer explicit imports and type hints in new modules; enable mypy clean output.

## Testing Guidelines
- Primary framework is pytest with `pytest-asyncio`; async tests rely on auto mode configured in `pytest.ini`.
- Mirror feature names in test filenames (`test_<feature>.py`) and functions (`test_<behavior>`).
- For targeted checks use `pytest tests/test_file_write.py -k happy_path`; add regression tests when fixing bugs.
- When database interactions are involved, ensure `make db-setup` has been run and clean up created threads or files.

## Commit & Pull Request Guidelines
- Follow the existing history: concise, imperative subject lines (`Add`, `Fix`, `Implement`) without trailing punctuation.
- Include context in the body when touching multiple layers (Python + Deno) or altering migrations.
- Reference issue IDs or tickets when relevant, and note database or sandbox compatibility changes.
- Pull requests should summarize behavior changes, list test evidence (`make test`, `make quality`), and attach screenshots/logs for UX-facing updates.
