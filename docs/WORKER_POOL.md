# Pyodide Worker Pool

## Overview

The worker pool provides **70-95% performance improvement** by keeping Pyodide loaded in memory across executions, eliminating the 4-14 second overhead of loading Pyodide + micropip for each request.

## Performance

| Operation | Before (one-shot) | After (pool) | Improvement |
|-----------|-------------------|--------------|-------------|
| Simple code | 4.5s | **0.5s** | 89% faster |
| With numpy | 4.5s | **0.2s** | 96% faster |
| With matplotlib | 14s | **1.5s** | 89% faster |
| Stateful session | 4.5s | **0.2s** | 96% faster |

## How It Works

```
┌─────────────────────────────────────────────────────────┐
│                  SandboxExecutor (Python)                │
│  • Manages worker pool                                   │
│  • Routes requests via round-robin                       │
│  • Handles worker crashes and restarts                   │
└──────────┬──────────────┬──────────────┬────────────────┘
           │              │              │
           ▼              ▼              ▼
    ┌──────────┐   ┌──────────┐   ┌──────────┐
    │ Worker 1 │   │ Worker 2 │   │ Worker 3 │
    │  Deno    │   │  Deno    │   │  Deno    │
    │ Pyodide  │   │ Pyodide  │   │ Pyodide  │
    │ (loaded) │   │ (loaded) │   │ (loaded) │
    └──────────┘   └──────────┘   └──────────┘
```

- **3 long-running Deno workers** (configurable)
- Each keeps Pyodide + micropip loaded
- JSON-RPC communication over stdin/stdout
- Automatic health monitoring & recovery

## Configuration

### Enable the Pool

```bash
export PYODIDE_USE_POOL=true
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PYODIDE_USE_POOL` | `false` | Enable worker pool (legacy one-shot if false) |
| `PYODIDE_POOL_SIZE` | `3` | Number of workers in the pool |
| `PYODIDE_WORKER_REQUEST_LIMIT` | `1000` | Recycle worker after N requests |
| `PYODIDE_HEALTH_CHECK_INTERVAL` | `30` | Health check interval (seconds) |

## Usage

### Basic Usage

No code changes required! Just enable the feature flag:

```python
from mayflower_sandbox import SandboxExecutor
import asyncpg

# Set PYODIDE_USE_POOL=true in environment

pool = await asyncpg.create_pool(...)
executor = SandboxExecutor(pool, "thread_123", allow_net=True)

# Automatically uses worker pool if enabled
result = await executor.execute("print('Hello, World!')")
```

### First Request (Cold Start)

The pool initializes on first use:

```
[INFO] Initializing Pyodide worker pool (size=3)...
[Worker 0] Loading Pyodide...
[Worker 1] Loading Pyodide...
[Worker 2] Loading Pyodide...
[Worker 0] Ready in 4523ms (PID: 12345)
[Worker 1] Ready in 4501ms (PID: 12346)
[Worker 2] Ready in 4534ms (PID: 12347)
[INFO] Pyodide worker pool ready!
```

**First request: ~5 seconds** (includes pool initialization)

### Subsequent Requests (Warm)

```
[INFO] Code execution started (pool)
[INFO] Code execution completed (pool) - 0.2s
```

**Subsequent requests: 0.2-2 seconds** (no Pyodide reload!)

## Stateful Execution

The pool supports stateful sessions:

```python
executor = SandboxExecutor(pool, "thread_123", stateful=True)

# First execution
result1 = await executor.execute("x = 42")

# Second execution reuses session
result2 = await executor.execute(
    "print(x * 2)",
    session_bytes=result1.session_bytes
)
# Output: 84
```

## Health Monitoring

The pool automatically monitors worker health:

- **Health checks** every 30 seconds (configurable)
- **Auto-restart** on worker crash or timeout
- **Worker recycling** after 1000 requests (prevents memory leaks)

```
[Worker 1] Unhealthy: timeout, restarting...
[Worker 1] Starting...
[Worker 1] Ready in 4512ms (PID: 12350)
[Worker 1] Restarted successfully
```

## Migration Guide

### Staging Deployment

1. Enable pool in staging:
```bash
export PYODIDE_USE_POOL=true
export PYODIDE_POOL_SIZE=3
```

2. Monitor metrics:
   - Execution time (should drop to 0.5-2s)
   - Memory usage (~500MB baseline for 3 workers)
   - Error rate (should remain same or better)

3. Check logs for worker health events

### Production Rollout

1. Start with 10% rollout (if using feature flags)
2. Gradually increase to 50%, then 100%
3. Monitor performance metrics
4. Once stable, remove legacy code path

### Rollback

If issues occur, simply disable the pool:

```bash
export PYODIDE_USE_POOL=false
```

System immediately falls back to legacy one-shot execution.

## Architecture

### Worker Server (`worker_server.ts`)

- Long-running Deno process
- Handles JSON-RPC requests
- Keeps Pyodide loaded in memory
- Pre-configures matplotlib Agg backend

### Worker Pool (`worker_pool.py`)

- Manages N worker processes
- Round-robin load balancing
- Health monitoring & auto-recovery
- Worker lifecycle management

### Integration (`sandbox_executor.py`)

- Routes to pool when `PYODIDE_USE_POOL=true`
- Falls back to legacy execution otherwise
- Class-level pool shared across instances
- Lazy initialization on first use

## Troubleshooting

### Pool Not Starting

**Symptom:** First request times out or fails

**Check:**
1. Deno is installed: `deno --version`
2. Worker server exists: `ls src/mayflower_sandbox/worker_server.ts`
3. Logs show initialization: `grep "Initializing Pyodide worker pool" logs`

### Slow Performance

**Symptom:** Pool mode not faster than legacy

**Possible causes:**
1. Pool size too small (increase `PYODIDE_POOL_SIZE`)
2. Workers recycling too frequently (increase `REQUEST_LIMIT`)
3. Cold start measured (first request is always slow)

### Memory Issues

**Symptom:** High memory usage or OOM errors

**Solutions:**
1. Reduce pool size: `PYODIDE_POOL_SIZE=2`
2. Lower request limit: `PYODIDE_WORKER_REQUEST_LIMIT=500`
3. Increase health check frequency: `PYODIDE_HEALTH_CHECK_INTERVAL=15`

### Worker Crashes

**Symptom:** Frequent restart messages in logs

**Check:**
1. Worker timeout issues (code taking too long)
2. Memory leaks in user code
3. System resource limits

## Performance Tips

1. **Warm up the pool** during app startup:
```python
# In main.py startup
executor = SandboxExecutor(pool, "warmup", allow_net=False)
await executor.execute("print('warmup')")
```

2. **Use stateful sessions** for iterative workflows:
```python
# Avoid reinstalling packages every time
executor = SandboxExecutor(pool, thread_id, stateful=True)
result1 = await executor.execute("await micropip.install('numpy')")
result2 = await executor.execute("import numpy; print(numpy.__version__)",
                                  session_bytes=result1.session_bytes)
```

3. **Monitor worker health** in production:
```python
# Check pool health
if SandboxExecutor._pool:
    health = await SandboxExecutor._pool.health_check_all()
    print(f"Workers: {health}")
```

## Future Enhancements

Potential improvements for future versions:

- [ ] Adaptive pool sizing based on load
- [ ] Worker affinity for thread locality
- [ ] Metrics & observability dashboard
- [ ] Hot reload for worker code updates
- [ ] MCP bridge support in pool mode
