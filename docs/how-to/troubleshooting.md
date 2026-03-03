# Troubleshooting

Common issues and their solutions.

## Database Connection Issues

### Cannot connect to PostgreSQL

```
asyncpg.exceptions.ConnectionDoesNotExistError
```

**Check:**

1. Database is running: `docker compose ps`
2. Port is correct: default is `5432`, tests may use `5433`
3. Credentials match environment variables

```bash
# Verify connection
psql -h localhost -p 5432 -U postgres -d mayflower_test -c "SELECT 1"
```

### Schema not applied

```
asyncpg.exceptions.UndefinedTableError: relation "sandbox_sessions" does not exist
```

**Fix:** Apply the migration:

```bash
psql -d mayflower_test -f migrations/001_sandbox_schema.sql
```

Or use the Makefile shortcut:

```bash
make db-setup
```

## Deno Not Found

### Pyodide execution fails

```
FileNotFoundError: [Errno 2] No such file or directory: 'deno'
```

**Fix:** Install Deno and ensure it is on your PATH:

```bash
curl -fsSL https://deno.land/x/install/install.sh | sh

# Add to ~/.bashrc or ~/.zshrc
export DENO_INSTALL="$HOME/.deno"
export PATH="$DENO_INSTALL/bin:$PATH"
```

Verify:

```bash
deno --version
```

## Worker Pool Problems

### Pool not starting

**Symptom:** First request times out or fails.

**Check:**

1. Deno is installed: `deno --version`
2. Worker server exists: `ls src/mayflower_sandbox/worker_server.ts`
3. Logs show initialization: look for `"Initializing Pyodide worker pool"` in logs

### Pool not faster than legacy

**Possible causes:**

1. Pool size too small -- increase `PYODIDE_POOL_SIZE`
2. Workers recycling too frequently -- increase `PYODIDE_WORKER_REQUEST_LIMIT`
3. Measuring cold start -- the first request always includes pool initialization (~5s)

### Health check failures

**Symptom:** Frequent `"Unhealthy: timeout, restarting..."` messages in logs.

**Solutions:**

1. Code timing out -- increase execution `timeout_seconds`
2. Worker overloaded -- increase `PYODIDE_POOL_SIZE`
3. System resource limits -- check available memory

### High memory usage / OOM

**Solutions:**

1. Reduce pool size: `PYODIDE_POOL_SIZE=2`
2. Lower request limit: `PYODIDE_WORKER_REQUEST_LIMIT=500`
3. Increase health check frequency: `PYODIDE_HEALTH_CHECK_INTERVAL=15`

### Worker crashes

**Symptom:** Frequent restart messages in logs.

**Check:**

1. Worker timeout issues (user code taking too long)
2. Memory leaks in user code
3. System resource limits (`ulimit -a`)

## File Size Limits

### Write rejected

```
ValueError: File content exceeds 20MB limit
```

Files are limited to 20MB each, enforced at the database level. Split large files or use compression.

## Helper Loading Issues

### Helper not found

```
ImportError: No module named 'document.docx_ooxml'
```

Check that the file exists in `helpers/document/docx_ooxml.py` and that the VFS loading completed. Restart the executor if helpers were recently added.

### Dependency missing

```
ModuleNotFoundError: No module named 'pypdf'
```

Install with micropip before importing:

```python
import micropip
await micropip.install('pypdf')
from document.pdf_manipulation import merge_pdfs
```

## Common pytest Failures

### Tests require running database

Most tests need PostgreSQL. Start it first:

```bash
make db-setup
# or
docker compose up -d
```

### LLM tests timing out

LLM-dependent tests make real API calls and can take 1--3 minutes per test. They are **not stuck** just because output pauses. To skip them:

```bash
uv run pytest tests/ -v --tb=short \
  --ignore=tests/test_langgraph_integration.py \
  --ignore=tests/test_langgraph_realistic.py \
  --ignore=tests/test_langgraph_skills.py \
  --ignore=tests/test_document_skills.py \
  --ignore=tests/test_agent_state.py \
  --ignore=tests/test_python_run_prepared_e2e.py
```

### Port conflicts

If tests fail with port-related errors, check for other processes using the same port:

```bash
lsof -i :5433
```

## Shell Execution Issues

### BusyBox command not found

If shell commands fail, verify that the BusyBox WASM binary is available and that Deno can load it.

### Pipe failures

Each pipe stage runs in a separate Deno Worker with SharedArrayBuffer ring buffers. If pipe commands fail:

1. Check that the command syntax is correct
2. Verify each stage of the pipe works independently
3. Check for overly large pipe outputs (memory limits apply)
