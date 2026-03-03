# Deployment

How to set up, test, and deploy Mayflower Sandbox.

## Docker Setup

### Start the Database

```bash
# Start PostgreSQL and run migrations
make db-setup

# Or manually with docker compose
docker compose up -d

# Wait for healthy status
docker compose ps
```

### Stop the Database

```bash
make db-down
# or
docker compose down
```

## Running Tests

### Install Dependencies

```bash
uv venv && uv pip install -e ".[dev]"
```

### Run All Tests

```bash
POSTGRES_PORT=5433 uv run pytest tests/ -v --tb=short
```

!!! note
    Tests can take 10+ minutes due to LLM-dependent tests making real API calls.

### Run Core Tests Only (Fast)

```bash
uv run pytest tests/test_schema_codegen.py tests/test_schema_validator.py tests/test_filesystem.py -v --tb=short
```

### Exclude LLM-Dependent Tests

These tests make real API calls and can take 1--3 minutes each:

```bash
uv run pytest tests/ -v --tb=short \
  --ignore=tests/test_langgraph_integration.py \
  --ignore=tests/test_langgraph_realistic.py \
  --ignore=tests/test_langgraph_skills.py \
  --ignore=tests/test_document_skills.py \
  --ignore=tests/test_agent_state.py \
  --ignore=tests/test_python_run_prepared_e2e.py
```

### Run Tests with Coverage

```bash
uv run pytest --cov=src/mayflower_sandbox --cov-report=html tests/
```

## Quality Checks

### Linting

```bash
uv run ruff check src/ tests/
```

### Formatting

```bash
uv run ruff format --check src/ tests/
```

### Type Checking

```bash
uv run mypy src/ --ignore-missing-imports
```

### All Checks (Pre-commit)

The pre-commit hook at `.git/hooks/pre-commit` runs ruff and mypy automatically on staged Python files. You can run them manually:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/ --ignore-missing-imports
```

## Environment Variables

### PostgreSQL

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_HOST` | `localhost` | Database host |
| `POSTGRES_DB` | `mayflower_test` | Database name |
| `POSTGRES_USER` | `postgres` | Database user |
| `POSTGRES_PASSWORD` | `postgres` | Database password |
| `POSTGRES_PORT` | `5432` | Database port |

### Worker Pool

| Variable | Default | Description |
|----------|---------|-------------|
| `PYODIDE_USE_POOL` | `false` | Enable worker pool (recommended for production) |
| `PYODIDE_POOL_SIZE` | `3` | Number of workers in the pool |
| `PYODIDE_WORKER_REQUEST_LIMIT` | `1000` | Recycle worker after N requests |
| `PYODIDE_HEALTH_CHECK_INTERVAL` | `30` | Health check interval (seconds) |

### MCP

| Variable | Default | Description |
|----------|---------|-------------|
| `MAYFLOWER_MCP_ALLOWLIST` | (none) | Comma-separated server names or host suffixes to allow |
| `MAYFLOWER_MCP_SESSION_TTL` | `300` | Session TTL in seconds |
| `MAYFLOWER_MCP_CALL_INTERVAL` | `0.1` | Minimum interval between calls (seconds) |
| `MAYFLOWER_SANDBOX_NET_ALLOW` | (none) | Additional hosts to allow outbound connections to |

### LLM (for tests and examples)

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | (none) | For LLM-dependent tests |
| `OPENAI_API_KEY` | (none) | Alternative LLM provider |

## Worker Pool Production Configuration

The worker pool keeps Pyodide loaded in memory for **70--95% faster execution**. Enable it in production:

```bash
export PYODIDE_USE_POOL=true
export PYODIDE_POOL_SIZE=3
export PYODIDE_WORKER_REQUEST_LIMIT=1000
export PYODIDE_HEALTH_CHECK_INTERVAL=30
```

### Resource Requirements

- Each Deno worker uses ~150--200MB RAM with Pyodide loaded
- 3 workers = ~500MB baseline memory
- First request takes ~5 seconds (pool initialization)
- Subsequent requests take 0.2--2 seconds

### Warm-Up on Startup

Warm up the pool during application startup to avoid cold-start latency on the first user request:

```python
executor = SandboxExecutor(pool, "warmup", allow_net=False)
await executor.execute("print('warmup')")
```

## Database Schema

Apply the schema with:

```bash
psql -d mayflower_test -f migrations/001_sandbox_schema.sql
```

This creates:

- **sandbox_sessions** -- Session tracking (180-day expiration)
- **sandbox_filesystem** -- File storage (20MB per file limit)
- **sandbox_session_bytes** -- Stateful execution support (serialized Pyodide namespaces)
- **sandbox_skills** -- Installed Claude Skills metadata
- **sandbox_mcp_servers** -- MCP server bindings
