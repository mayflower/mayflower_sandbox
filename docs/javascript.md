# JavaScript/TypeScript Sandbox

⚡ **EXPERIMENTAL FEATURE** - JavaScript/TypeScript execution in WebAssembly sandbox

Mayflower Sandbox provides optional JavaScript/TypeScript code execution alongside Python, using QuickJS compiled to WebAssembly and hosted in Deno.

## Overview

The JavaScript/TypeScript sandbox mirrors the Python sandbox architecture but uses a different WebAssembly runtime:

- **Python sandbox**: Pyodide (CPython compiled to Wasm)
- **JavaScript sandbox**: QuickJS (JavaScript engine compiled to Wasm)

Both sandboxes share:
- PostgreSQL-backed virtual filesystem (VFS)
- Same security model (no host filesystem, no network by default)
- Same resource limits (20MB per file, 100 files max, configurable timeout)
- Thread isolation via `thread_id`
- Session management and expiration

## Architecture

```
┌─────────────────────────────────────────────────┐
│ LangGraph Agent                                 │
│ ├─ Python Tools (Pyodide)                       │
│ └─ JavaScript Tools (QuickJS) ⚡                 │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│ Shared PostgreSQL VFS                           │
│ - Files accessible from both Python and JS      │
│ - Thread isolation                              │
│ - Resource quotas                               │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│ Execution Runtimes                              │
│ ├─ Deno + Pyodide (Python)                      │
│ └─ Deno + QuickJS-Wasm (JavaScript) ⚡          │
└─────────────────────────────────────────────────┘
```

## Requirements

### Deno Runtime

JavaScript/TypeScript execution requires Deno to be installed:

```bash
# Install Deno (macOS/Linux)
curl -fsSL https://deno.land/x/install/install.sh | sh

# Install Deno (Windows)
irm https://deno.land/install.ps1 | iex

# Verify installation
deno --version
```

**Why Deno?**
- Secure by default (permissions model)
- Native TypeScript support
- WebAssembly support
- Same runtime used for Python sandbox (Pyodide)

### QuickJS-Wasm

QuickJS is automatically loaded from npm CDN when JavaScript code executes. No manual installation needed.

## Enabling JavaScript Support

### In LangGraph Applications

Enable JavaScript tools when creating sandbox tools:

```python
import asyncpg
from mayflower_sandbox.tools import create_sandbox_tools
from langgraph.prebuilt import create_react_agent
from langchain_anthropic import ChatAnthropic

# Setup database
db_pool = await asyncpg.create_pool(
    host="localhost",
    database="mayflower_test",
    user="postgres",
    password="postgres"
)

# Enable JavaScript/TypeScript tools
tools = create_sandbox_tools(
    db_pool,
    thread_id="user_123",
    enable_javascript=True  # Adds 3 JavaScript tools
)

# Create LangGraph agent with both Python and JavaScript
llm = ChatAnthropic(model="claude-sonnet-4.5")
agent = create_react_agent(llm, tools)

# Agent can now use both languages
result = await agent.ainvoke({
    "messages": [("user", "Process data.json with JavaScript and analyze with Python")]
})
```

### Direct Executor Usage

Use `JavascriptSandboxExecutor` directly for JavaScript execution:

```python
from mayflower_sandbox import JavascriptSandboxExecutor

executor = JavascriptSandboxExecutor(
    db_pool=db_pool,
    thread_id="user_123",
    timeout_seconds=60.0,
    allow_net=False,  # Network not yet implemented for JS
)

result = await executor.execute("""
const numbers = [1, 2, 3, 4, 5];
const sum = numbers.reduce((a, b) => a + b, 0);
console.log('Sum:', sum);
sum;
""")

print(result.stdout)  # "Sum: 15"
print(result.result)  # 15
```

## JavaScript VFS API

JavaScript code can access the shared VFS using injected global functions:

### writeFile(path, content)

Create or update a file in the VFS.

```javascript
// Write text file
writeFile('/data/output.txt', 'Hello from JavaScript!');

// Write JSON file
const data = { name: 'Test', values: [1, 2, 3] };
writeFile('/data/results.json', JSON.stringify(data, null, 2));

// Files persist in PostgreSQL and are accessible from Python
```

### readFile(path)

Read a file from the VFS as a string.

```javascript
// Read text file
const content = readFile('/data/input.txt');
console.log('Content:', content);

// Read and parse JSON file
const jsonStr = readFile('/data/config.json');
const config = JSON.parse(jsonStr);
console.log('Config loaded:', config.name);
```

### listFiles()

List all files in the VFS for the current thread.

```javascript
const files = listFiles();
console.log('Available files:', files);

// Example output: ['/data/input.txt', '/data/config.json', '/tmp/script.js']
```

## Cross-Language Workflows

Files created in Python are accessible in JavaScript, and vice versa:

### Example: Python → JavaScript → Python

```python
from mayflower_sandbox import SandboxExecutor, JavascriptSandboxExecutor

# Step 1: Python creates data
py_executor = SandboxExecutor(db_pool, thread_id="user_123")
await py_executor.execute("""
import json
data = {'numbers': [10, 20, 30, 40, 50]}
with open('/data/input.json', 'w') as f:
    json.dump(data, f)
print('Data created by Python')
""")

# Step 2: JavaScript processes data
js_executor = JavascriptSandboxExecutor(db_pool, thread_id="user_123")
await js_executor.execute("""
const data = JSON.parse(readFile('/data/input.json'));
const sum = data.numbers.reduce((a, b) => a + b, 0);
const result = { sum, count: data.numbers.length, average: sum / data.numbers.length };
writeFile('/data/output.json', JSON.stringify(result, null, 2));
console.log('Processed by JavaScript:', result);
""")

# Step 3: Python reads results
await py_executor.execute("""
import json
with open('/data/output.json') as f:
    result = json.load(f)
print(f"Python sees: sum={result['sum']}, average={result['average']}")
""")
```

## JavaScript Features

### Supported

✅ **ES6+ JavaScript**
- Arrow functions, const/let, template strings
- Destructuring, spread operator
- Array methods (map, filter, reduce, etc.)
- Object methods and property shorthand
- Classes and modules (in single context)

✅ **Built-in Objects**
- JSON (stringify, parse)
- Math (all methods)
- Date and time
- String manipulation
- Regular expressions
- Arrays and Sets

✅ **TypeScript (Basic)**
- Type annotations (stripped at runtime)
- Interfaces (stripped at runtime)
- Simple generics
- Const assertions

### Not Supported

❌ **Node.js Built-ins**
- No `require()` or `import` from npm packages
- No `fs`, `http`, `path`, `os`, `crypto`, etc.
- Use VFS functions instead of `fs`

❌ **Network Access**
- No `fetch`, `XMLHttpRequest`, `WebSocket`
- Network access not yet implemented (future enhancement)

❌ **Async Operations**
- No `async`/`await` for external operations
- Synchronous VFS operations only
- No promises that depend on external resources

❌ **Browser APIs**
- No DOM (`document`, `window`, `navigator`)
- No browser storage (`localStorage`, `sessionStorage`)
- No browser-specific APIs

❌ **Advanced TypeScript**
- No decorators
- No advanced generics (conditional types, mapped types)
- No module resolution (imports/exports)
- Basic runtime transpilation only

## Performance Characteristics

### Initialization Time

- **Python (Pyodide)**: ~500-1000ms to initialize VM
- **JavaScript (QuickJS)**: ~1-5ms to initialize VM

JavaScript is significantly faster to start, making it ideal for:
- Quick data transformations
- JSON manipulation
- Text processing
- Calculations without library dependencies

### Execution Speed

- QuickJS is generally slower than V8/SpiderMonkey for compute-intensive tasks
- Python with NumPy/Pandas is faster for numerical operations
- Choose the right tool for the job:
  - JavaScript: JSON, text, simple calculations
  - Python: Data science, numerical computing, libraries

### Memory Footprint

- **Python (Pyodide)**: ~50-100MB per VM
- **JavaScript (QuickJS)**: ~5-10MB per VM

## Security Model

The JavaScript sandbox has the same security constraints as the Python sandbox:

### No Host Filesystem Access

```javascript
// ❌ This will fail - no require('fs')
const fs = require('fs');  // Error: require is not defined

// ✅ Use VFS functions instead
writeFile('/data/output.txt', 'content');
const content = readFile('/data/input.txt');
```

### No Network Access

```javascript
// ❌ This will fail - no fetch
fetch('https://example.com');  // Error: fetch is not defined

// Future: Network access will be configurable via allow_net parameter
```

### No Process/Environment Access

```javascript
// ❌ This will fail - no process
console.log(process.env.HOME);  // Error: process is not defined

// ❌ This will fail - no global objects from host
console.log(__dirname);  // Error: __dirname is not defined
```

### Deno Permissions

The Deno host process runs with minimal permissions:

```typescript
deno run \
  --allow-read      # Only for QuickJS Wasm module loading
  --allow-write     # Only for QuickJS Wasm module loading
  quickjs_executor.ts
```

No additional permissions (network, env, run, ffi) are granted.

## Resource Limits

JavaScript execution shares the same resource limits as Python:

| Resource | Limit | Enforced By |
|----------|-------|-------------|
| File size | 20MB per file | VirtualFilesystem |
| File count | 100 files per thread | VirtualFilesystem |
| Total storage | 20MB total | VirtualFilesystem (configurable) |
| Execution timeout | 60 seconds (default) | JavascriptSandboxExecutor |
| Memory | Not yet enforced | Future enhancement |

### Configuring Limits

```python
executor = JavascriptSandboxExecutor(
    db_pool=db_pool,
    thread_id="user_123",
    timeout_seconds=30.0,      # Shorter timeout
    max_file_size_mb=10,       # Lower file size limit
    max_files=50,              # Fewer files allowed
)
```

## Statefulness Model

**Current (Phase 1)**: Stateless execution
- Each `execute()` call creates a fresh QuickJS VM
- No state preserved between executions
- Files in VFS provide persistence

**Future (Phase 2)**: Optional worker pool with state
- Long-running QuickJS workers (similar to PyodideWorker)
- Session state serialization via JSON
- Controlled by `QUICKJS_USE_POOL` environment variable

Stateless is acceptable because:
1. QuickJS VM init is fast (~1-5ms)
2. VFS provides data persistence
3. Reduces memory footprint
4. Prevents state leakage

## Error Handling

### Syntax Errors

```python
result = await executor.execute("const x = ;")

assert not result.success
assert "Syntax" in result.stderr or "Unexpected" in result.stderr
```

### Runtime Errors

```python
result = await executor.execute("throw new Error('Test error');")

assert not result.success
assert "Error: Test error" in result.stderr
```

### Timeout Errors

```python
executor = JavascriptSandboxExecutor(db_pool, thread_id, timeout_seconds=2.0)
result = await executor.execute("while (true) {}")

assert not result.success
assert "timeout" in result.stderr.lower()
```

### VFS Errors

```python
result = await executor.execute("readFile('/nonexistent.txt');")

assert not result.success
assert "File not found" in result.result or "not found" in result.stderr
```

## Debugging

### Enable Debug Logging

```python
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('mayflower_sandbox.javascript_executor')
logger.setLevel(logging.DEBUG)
```

### Check Execution Result

```python
result = await executor.execute(code)

print(f"Success: {result.success}")
print(f"Stdout: {result.stdout}")
print(f"Stderr: {result.stderr}")
print(f"Result: {result.result}")
print(f"Execution time: {result.execution_time}s")
print(f"Created files: {result.created_files}")
```

### Common Issues

**Issue**: "Deno is not installed"
- **Solution**: Install Deno from https://deno.land/

**Issue**: "QuickJS module not found"
- **Solution**: Check network connectivity (QuickJS loaded from npm CDN)

**Issue**: Code executes but no output
- **Solution**: Use `console.log()` - JavaScript sandbox only captures logged output

**Issue**: "File not found" when reading VFS file
- **Solution**: Verify file exists using `listFiles()`, check file path format

## Best Practices

### 1. Always Use console.log()

JavaScript sandbox only shows output you explicitly log:

```javascript
// ❌ Bad - no output
const result = 5 + 7;

// ✅ Good - output visible
const result = 5 + 7;
console.log('Result:', result);
```

### 2. Prefer JavaScript for JSON Operations

```javascript
// Fast JavaScript JSON manipulation
const data = JSON.parse(readFile('/data/input.json'));
data.processed = data.values.map(v => v * 2);
writeFile('/data/output.json', JSON.stringify(data, null, 2));
console.log('Processed items:', data.processed.length);
```

### 3. Use Python for Heavy Computation

```python
# Python with NumPy for numerical work
import numpy as np
data = np.load('/data/matrix.npy')
result = np.linalg.svd(data)
np.save('/data/result.npy', result)
```

### 4. Share Data via JSON Files

```javascript
// JavaScript creates JSON
const summary = { count: 100, average: 42.5 };
writeFile('/shared/summary.json', JSON.stringify(summary));
```

```python
# Python reads JSON
import json
with open('/shared/summary.json') as f:
    summary = json.load(f)
print(f"Count: {summary['count']}")
```

### 5. Handle Errors Gracefully

```javascript
try {
    const data = JSON.parse(readFile('/data/input.json'));
    // Process data...
    console.log('Success');
} catch (error) {
    console.error('Error:', error.message);
}
```

## Limitations and Future Work

### Current Limitations

- ⚠️ No network access (allow_net not implemented)
- ⚠️ No session state serialization
- ⚠️ No worker pool mode (stateless only)
- ⚠️ Basic TypeScript support (runtime transpilation only)
- ⚠️ No async/await for external operations
- ⚠️ No npm packages

### Future Enhancements

Planned features for future releases:

- 🔮 Network access with whitelist (via Deno permissions)
- 🔮 Worker pool mode for stateful execution
- 🔮 Session state serialization (JSON-based)
- 🔮 npm package support (bundled via esbuild)
- 🔮 Memory limit enforcement
- 🔮 Enhanced TypeScript support
- 🔮 Async/await support for VFS operations

## Comparison: Python vs JavaScript

| Feature | Python (Pyodide) | JavaScript (QuickJS) |
|---------|------------------|----------------------|
| Initialization | ~500-1000ms | ~1-5ms ⚡ |
| Memory footprint | ~50-100MB | ~5-10MB ⚡ |
| Package ecosystem | pip/micropip | None (pure JS only) |
| Numerical computing | ✅ NumPy, Pandas | ❌ Limited |
| JSON operations | ✅ json module | ✅ Native ⚡ |
| Text processing | ✅ re, string | ✅ Native ⚡ |
| Stateful execution | ✅ Session state | ❌ Not yet |
| Network access | ✅ Configurable | ❌ Not yet |
| Document generation | ✅ Helpers | ❌ Limited |

**Use JavaScript for**: JSON, text, quick transformations, fast initialization
**Use Python for**: Data science, numerical computing, document generation, libraries

## Testing

JavaScript/TypeScript functionality is fully tested. See:
- `tests/test_javascript_executor.py` (20 tests)
- `tests/test_javascript_tools.py` (17 tests)
- `tests/JAVASCRIPT_TESTS.md` (test documentation)

Run tests:
```bash
pytest tests/test_javascript_executor.py tests/test_javascript_tools.py -v
```

Tests skip gracefully if Deno is not installed.

## See Also

- [Tools Reference](tools.md) - JavaScript tool documentation
- [Quick Start](quickstart.md) - Getting started with JavaScript
- [Advanced Features](advanced.md) - Worker pools and session state
- [API Reference](api.md) - Low-level API documentation
