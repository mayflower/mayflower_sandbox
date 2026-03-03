# Command Routing

How `execute()` determines whether a command runs as Python or shell, and the internals of each path.

## Routing Table

When `MayflowerSandboxBackend.execute(command)` is called, the command is inspected in this order:

| Priority | Detection | Pattern | Engine |
|----------|-----------|---------|--------|
| 1 | `__PYTHON__` sentinel | `__PYTHON__\n<code>` | Pyodide (direct) |
| 2 | Inline Python | `python -c "..."` / `python3 -c '...'` | Pyodide (inline) |
| 3 | Python script | `python script.py` / `python3 /path/to/script.py arg1` | Pyodide (file-based) |
| 4 | Default fallback | Everything else | BusyBox WASM shell |

The first matching rule wins. If no Python pattern matches, the command is sent to the BusyBox shell executor.

## `__PYTHON__` Sentinel

```
__PYTHON__
import pandas as pd
df = pd.read_csv('/tmp/data.csv')
print(df.describe())
```

The `__PYTHON__` sentinel is used by DeepAgents' `ToolCallContentMiddleware`. When the middleware intercepts a code block from the LLM, it prepends this sentinel and passes the whole string as a single `execute()` call. The backend strips the sentinel line and runs the remaining content as Python code.

## Inline Python Detection

```bash
python -c "print('hello world')"
python3 -c 'import sys; print(sys.version)'
```

Uses `shlex.split()` for robust quote handling. The regex `r"^python3?\s+-c\s+(.+)$"` identifies inline commands, then shlex extracts the code string respecting both single and double quotes.

## File-Based Python Execution

```bash
python script.py
python3 /tmp/analysis.py arg1 arg2
```

The `_parse_python_command()` method:

1. Splits the command into tokens
2. Checks if the first token is `python` or `python3`
3. Validates the script path ends with `.py`
4. Returns `(script_path, args)` or `None`

When a script path is detected:

1. The script is read from the VFS (PostgreSQL)
2. If arguments are provided, `sys.argv` is injected before execution
3. The code is passed to Pyodide for execution

## BusyBox Shell Fallback

Everything that does not match a Python pattern is sent to the BusyBox WASM shell executor.

### Supported Features

- **Commands:** `echo`, `cat`, `grep`, `wc`, `ls`, `mkdir`, `rm`, `sed`, `awk`, etc.
- **Pipes:** `echo hello | cat | grep hello`
- **Chaining:** `cmd1 && cmd2`, `cmd1 ; cmd2`
- **Redirections:** `>`, `>>`, `<`

### Pipeline Architecture

Each pipe stage runs in a separate Deno Worker, connected via SharedArrayBuffer ring buffers:

```
echo hello | cat | grep hello
     |         |         |
  Worker 1 → Worker 2 → Worker 3
       \    SharedArrayBuffer    /
              Ring Buffer Pipes
```

Worker-based isolation is necessary because BusyBox WASM has global state that prevents running multiple commands in the same process. Each Worker gets its own BusyBox instance with VFS files mounted.

### Ring Buffer Communication

Workers communicate via SharedArrayBuffer ring buffers synchronized with `Atomics.wait()` / `Atomics.notify()`. This provides:

- Zero-copy data transfer between pipe stages
- Backpressure when a downstream stage is slow
- Proper EOF signaling when an upstream stage completes

## Pyodide Execution Internals

Regardless of how Python code enters the system (sentinel, inline, or file-based), execution follows the same path:

1. **VFS sync**: All files for the thread are loaded from PostgreSQL
2. **Serialization**: Files serialized to JSON for Deno
3. **Execution**: Deno spawns Pyodide (or uses a pool worker)
4. **File mount**: Files mounted in Pyodide's virtual filesystem
5. **Run**: Python code executed in the Pyodide sandbox
6. **Capture**: stdout/stderr captured, modified files extracted
7. **VFS save**: Changed files written back to PostgreSQL
8. **Result**: `ExecuteResponse` returned with output and exit code
