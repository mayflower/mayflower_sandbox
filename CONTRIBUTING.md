# Contributing to Mayflower Sandbox

## Secure Development Policy

All contributions must pass the following quality gates before merge.

### Mandatory CI Checks

1. **Ruff Linting** -- `ruff check src/ tests/` must pass with zero errors
2. **Ruff Formatting** -- `ruff format --check src/ tests/` must pass
3. **Type Checking** -- `mypy src/mayflower_sandbox --ignore-missing-imports` must pass
4. **Security Scan** -- Bandit must show no new high/critical findings
5. **SonarQube Quality Gate** -- must pass
6. **Tests** -- all pytest tests must pass with coverage thresholds met
7. **Code Review** -- at least one approving review from a maintainer

### Security Requirements

- **No secrets in code.** API keys, passwords, and tokens must use environment
  variables. See `.env.example` for the template.
- **No unjustified `# type: ignore`.** If necessary, explain why in a comment.
- **Input validation.** All public API inputs must be validated via Pydantic.
- **Path traversal prevention.** File paths must be sanitized
  (see `filesystem.py` for established patterns).

### Changelog Conventions

When submitting a PR, add an entry to `CHANGELOG.md` under `## [Unreleased]`:

- **Security fixes:** prefix with `[SECURITY]`
  - Example: `- [SECURITY] Fix path traversal in file upload handler`
- **Bug fixes:** use the `### Fixed` section
- **New features:** use the `### Added` section
- **Breaking changes:** use `### Changed` or `### Removed`

Follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

## Development Setup

```bash
# Clone the repository
git clone https://github.com/mayflower/mayflower_sandbox.git
cd mayflower_sandbox

# Install dependencies
uv pip install -e ".[dev]"

# Start the database
docker compose up -d

# Run tests
uv run pytest tests/ -v --tb=short

# Run quality checks
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/mayflower_sandbox --ignore-missing-imports
```

## Pre-commit Hooks

The repository uses pre-commit hooks (`.pre-commit-config.yaml`):

- **ruff** -- linting with auto-fix
- **ruff-format** -- code formatting
- **ty** -- type checking (Astral's Rust-based checker)

Install hooks after cloning:

```bash
pre-commit install
```

## Pull Request Process

1. Create a branch from `main` (`feature/`, `fix/`, `refactor/`, `docs/`)
2. Make changes with tests
3. Update `CHANGELOG.md`
4. Ensure all CI checks pass
5. Request review from a maintainer
6. Squash-merge after approval
