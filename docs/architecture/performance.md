# Performance

Worker pool benchmarks, stateful execution internals, and performance tuning.

## Worker Pool

The worker pool keeps Pyodide loaded in memory for **70--95% faster execution**, eliminating the 4--14 second overhead of loading Pyodide + micropip for each request.

### Benchmarks

| Operation | Without Pool | With Pool | Improvement |
|-----------|-------------|-----------|-------------|
| Simple code | 4.5s | **0.5s** | 89% faster |
| With numpy | 4.5s | **0.2s** | 96% faster |
| With matplotlib | 14s | **1.5s** | 89% faster |
| Stateful session | 4.5s | **0.2s** | 96% faster |

### How It Works

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

- **3 long-running Deno workers** (configurable via `PYODIDE_POOL_SIZE`)
- Each keeps Pyodide + micropip loaded in memory
- JSON-RPC communication over stdin/stdout
- Automatic health monitoring & recovery

### Cold Start vs Warm

- **First request** (~5 seconds): Pool initializes, workers load Pyodide
- **Subsequent requests** (0.2--2 seconds): Pyodide already loaded, only user code runs

### Health Monitoring

The pool automatically monitors worker health:

- **Health checks** every 30 seconds (configurable via `PYODIDE_HEALTH_CHECK_INTERVAL`)
- **Auto-restart** on worker crash or timeout
- **Worker recycling** after 1000 requests (configurable via `PYODIDE_WORKER_REQUEST_LIMIT`) to prevent memory leaks

### Legacy Mode

Without the pool (`PYODIDE_USE_POOL=false`), each execution starts a fresh Deno process (~4--5s per execution). This mode requires no persistent processes and has lower memory usage but significantly higher latency.

## Stateful Execution

Variables and state persist across executions within the same thread.

### How It Works

1. After each execution, Pyodide's namespace is serialized and stored in `sandbox_session_bytes`
2. Before the next execution, the session bytes are restored into the Pyodide instance
3. Variables, functions, and imported modules are available in subsequent calls

```python
backend = MayflowerSandboxBackend(db_pool, "user_123", stateful=True)

await backend.aexecute('python -c "x = 42"')
result = await backend.aexecute('python -c "print(x)"')
# result.output: "42"
```

### State Persistence

State survives:

- Multiple executions within the same session
- Application restarts (stored in PostgreSQL)
- Database connection resets

State is isolated per `thread_id`.

### Resetting State

```python
# Clear all state for a thread via SandboxManager
manager = SandboxManager(db_pool)
await manager.delete_session("user_123")
```

## Architecture Details

### Worker Server (`worker_server.ts`)

- Long-running Deno process
- Handles JSON-RPC requests on stdin/stdout
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
- Class-level pool shared across all backend instances
- Lazy initialization on first use

## Performance Tips

1. **Enable the worker pool** in production for 70--95% faster execution
2. **Warm up** during app startup to avoid first-request latency:
   ```python
   executor = SandboxExecutor(pool, "warmup", allow_net=False)
   await executor.execute("print('warmup')")
   ```
3. **Use stateful sessions** for iterative workflows to avoid reinstalling packages:
   ```python
   backend = MayflowerSandboxBackend(db_pool, thread_id, stateful=True)
   await backend.aexecute('python -c "import micropip; await micropip.install(\'numpy\')"')
   # numpy available in subsequent calls without reinstalling
   ```
4. **Monitor worker health** in production:
   ```python
   if SandboxExecutor._pool:
       health = await SandboxExecutor._pool.health_check_all()
   ```
5. **Tune pool size** based on concurrency and available memory (~150--200MB per worker)
