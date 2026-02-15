# Quick Start Guide

## Create a Backend

```python
import asyncpg
from mayflower_sandbox import MayflowerSandboxBackend

db_pool = await asyncpg.create_pool(
    host="localhost",
    database="mayflower_test",
    user="postgres",
    password="postgres",
)

backend = MayflowerSandboxBackend(
    db_pool,
    thread_id="user_123",
    allow_net=False,
    stateful=True,
    timeout_seconds=60.0,
)
```

## Execute Python Code

### Inline Execution

```python
result = await backend.aexecute('python -c "print(2 + 2)"')
print(result.output)     # "4\n"
print(result.exit_code)  # 0
```

### File-Based Execution

```python
await backend.awrite("/tmp/script.py", "print('Hello from script')")
result = await backend.aexecute("python /tmp/script.py")
print(result.output)  # "Hello from script\n"
```

### With Arguments

```python
await backend.awrite("/tmp/greet.py", """\
import sys
name = sys.argv[1] if len(sys.argv) > 1 else "World"
print(f"Hello, {name}!")
""")
result = await backend.aexecute("python /tmp/greet.py Alice")
print(result.output)  # "Hello, Alice!\n"
```

## Execute Shell Commands

```python
result = await backend.aexecute("echo hello | grep hello")
print(result.output)  # "hello\n"

result = await backend.aexecute("echo -e 'a\\nb\\nc' | wc -l")
print(result.output)  # "3\n"
```

## File Operations

### Write and Read

```python
await backend.awrite("/tmp/data.csv", "name,value\nAlice,100\nBob,200")
content = await backend.aread("/tmp/data.csv")
print(content)
```

### List Files

```python
files = await backend.als_info("/tmp")
for f in files:
    print(f"{f.path}  {f.size} bytes")
```

### Edit Files

```python
result = await backend.aedit("/tmp/data.csv", "Alice,100", "Alice,150")
```

### Find Files

```python
# Glob
matches = await backend.aglob_info("*.csv", "/tmp")

# Grep
hits = await backend.agrep_raw("Alice", "/tmp")
```

### Upload and Download Binary Files

```python
await backend.aupload_files([("/tmp/image.png", png_bytes)])
downloads = await backend.adownload_files(["/tmp/image.png"])
```

## Stateful Execution

When `stateful=True`, variables persist across executions:

```python
await backend.aexecute('python -c "x = 42"')
result = await backend.aexecute('python -c "print(x)"')
print(result.output)  # "42\n"
```

## DeepAgents Integration

Use `MayflowerSandboxBackend` as the backend in a DeepAgents application:

```python
from deepagents.backends import CompositeBackend, StateBackend
from mayflower_sandbox import MayflowerSandboxBackend, PostgresBackend

# Full sandbox backend for code execution
sandbox = MayflowerSandboxBackend(db_pool, thread_id="user_123")

# Or use PostgresBackend for persistent file storage in specific paths
composite = CompositeBackend(
    default=StateBackend(runtime),
    routes={"/memories/": PostgresBackend(db_pool, thread_id)},
)
```

## Working with Documents

Document helpers are pre-loaded in the sandbox:

```python
result = await backend.aexecute('''python -c "
from document.docx_ooxml import docx_extract_text
docx_bytes = open('/tmp/report.docx', 'rb').read()
text = docx_extract_text(docx_bytes)
print(f'Document has {len(text)} characters')
"''')
```

See [Document Processing](../how-to/document-processing.md) for the full helper library.

## Next Steps

- [Backend API Reference](../reference/backend-api.md) -- All methods and return types
- [Document Processing](../how-to/document-processing.md) -- Word, Excel, PowerPoint, PDF helpers
- [Skills & MCP](../how-to/skills-and-mcp.md) -- Install skills and bind MCP servers
- [Configuration](../reference/configuration.md) -- Environment variables and tuning
