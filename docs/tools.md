# Tools Reference

Mayflower Sandbox provides 10 core LangChain tools plus 3 optional JavaScript/TypeScript tools that extend `BaseTool` for use with LangGraph agents.

## Creating Tools

```python
from mayflower_sandbox.tools import create_sandbox_tools

# Create all 10 core tools for a specific thread
tools = create_sandbox_tools(
    db_pool=db_pool,
    thread_id="user_123",
)

# Enable JavaScript/TypeScript support (requires Deno)
tools = create_sandbox_tools(
    db_pool=db_pool,
    thread_id="user_123",
    enable_javascript=True,  # Adds 3 JavaScript tools (13 total)
)

# Use with LangGraph
from langgraph.prebuilt import create_react_agent
agent = create_react_agent(llm, tools)
```

## Tool Categories

### Python Code Execution (3 tools)
- `ExecutePythonTool` (`python_run`) - Execute Python code directly
- `RunPythonFileTool` (`python_run_file`) - Execute .py files from VFS
- `ExecuteCodeTool` (`python_run_prepared`) - Execute code from graph state

### JavaScript/TypeScript Code Execution (3 optional tools)
⚡ **Requires `enable_javascript=True` and Deno installation**
- `ExecuteJavascriptTool` (`javascript_run`) - Execute JavaScript/TypeScript code directly
- `RunJavascriptFileTool` (`javascript_run_file`) - Execute .js/.ts files from VFS
- `ExecuteJavascriptCodeTool` (`javascript_run_prepared`) - Execute code from graph state

### File Management (5 tools)
- `FileReadTool` (`file_read`) - Read files from VFS
- `FileWriteTool` (`file_write`) - Write files to VFS
- `FileEditTool` (`file_edit`) - Edit files via string replacement
- `FileListTool` (`file_list`) - List files in VFS
- `FileDeleteTool` (`file_delete`) - Delete files from VFS

### File Search (2 tools)
- `FileGlobTool` (`file_glob`) - Find files by glob patterns
- `FileGrepTool` (`file_grep`) - Search file contents with regex

---

# Python Code Execution Tools

## ExecutePythonTool

Execute Python code in an isolated Pyodide sandbox with automatic VFS integration.

### Features
- Automatic file pre-loading from PostgreSQL VFS
- Automatic file post-saving to PostgreSQL VFS
- Configurable network access
- Timeout protection (default: 120 seconds)
- Captures stdout/stderr
- Returns created files

### Usage

```python
from mayflower_sandbox.tools import ExecutePythonTool

tool = ExecutePythonTool(
    db_pool=pool,
    thread_id="user_1",
    allow_net=True,
    timeout_seconds=120.0
)

result = await tool._arun(code="""
# Files from VFS are automatically available
with open('/tmp/data.csv', 'w') as f:
    f.write('name,value\\n')
    f.write('Alice,100\\n')

# Process data
import csv
with open('/tmp/data.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        print(f"{row['name']}: {row['value']}")

# New files are automatically saved to VFS
""")
```

### Parameters
- `code` (str, required): Python code to execute

### Returns
String containing stdout output and information about created files.

## FileReadTool

Read file contents from the PostgreSQL-backed virtual filesystem.

### Usage

```python
from mayflower_sandbox.tools import FileReadTool

tool = FileReadTool(db_pool=pool, thread_id="user_1")
content = await tool._arun(file_path="/tmp/data.csv")
```

### Parameters
- `file_path` (str, required): Absolute path to the file

### Returns
String containing the file contents (decoded as UTF-8).

### Errors
- Returns error message if file doesn't exist
- Returns error if path validation fails

## FileWriteTool

Write content to a file in the virtual filesystem (20MB limit per file).

### Usage

```python
from mayflower_sandbox.tools import FileWriteTool

tool = FileWriteTool(db_pool=pool, thread_id="user_1")
result = await tool._arun(
    file_path="/tmp/config.json",
    content='{"setting": "value", "enabled": true}'
)
```

### Parameters
- `file_path` (str, required): Absolute path where file should be written
- `content` (str, required): Content to write to the file

### Returns
Success message with file path.

### Size Limit
Files are limited to 20MB, enforced at the database level.

## FileEditTool (str_replace)

Edit existing files by replacing a unique string with a new string. The tool requires the old string to appear exactly once in the file for safety.

### Features
- Safe string replacement with uniqueness validation
- Prevents unintended multiple replacements
- Clear error messages for debugging
- Supports multiline string replacement

### Usage

```python
from mayflower_sandbox.tools import FileEditTool

tool = FileEditTool(db_pool=pool, thread_id="user_1")

# Edit a configuration file
result = await tool._arun(
    file_path="/tmp/config.py",
    old_string="DEBUG = False",
    new_string="DEBUG = True"
)

# Edit code with multiline strings
result = await tool._arun(
    file_path="/tmp/script.py",
    old_string='def hello():\n    print("Hello")',
    new_string='def hello():\n    print("Hi there")'
)
```

### Parameters
- `file_path` (str, required): Absolute path to the file to edit
- `old_string` (str, required): Unique string to find and replace (must appear exactly once)
- `new_string` (str, required): New string to replace it with

### Returns
Success message with the replaced text, or error message if:
- String not found in file
- String appears more than once (must be unique)
- File doesn't exist

### Best Practices
- Provide enough context in `old_string` to make it unique
- For code edits, include surrounding context (e.g., function name + line)
- Use multiline strings for complex replacements
- If string appears multiple times, provide more context to make it unique

### Example: Making a String Unique

```python
# This will fail - "x = " appears twice
result = await tool._arun(
    file_path="/tmp/vars.py",
    old_string="x = ",
    new_string="x = "
)
# Error: String appears 2 times (must be unique)

# This succeeds - "x = 1" is unique
result = await tool._arun(
    file_path="/tmp/vars.py",
    old_string="x = 1",
    new_string="x = 10"
)
# Success!
```

## FileListTool

List files in the virtual filesystem with optional prefix filtering.

### Usage

```python
from mayflower_sandbox.tools import FileListTool

tool = FileListTool(db_pool=pool, thread_id="user_1")

# List all files
files = await tool._arun()

# List files with prefix
files = await tool._arun(prefix="/tmp/")
```

### Parameters
- `prefix` (str, optional): Filter files by path prefix

### Returns
String containing list of file paths, one per line.

## FileDeleteTool

Delete files from the virtual filesystem.

### Usage

```python
from mayflower_sandbox.tools import FileDeleteTool

tool = FileDeleteTool(db_pool=pool, thread_id="user_1")
result = await tool._arun(file_path="/tmp/old_file.txt")
```

### Parameters
- `file_path` (str, required): Absolute path to the file to delete

### Returns
Success message if file was deleted.

### Errors
- Returns error if file doesn't exist
- Returns error if path validation fails

## FileGlobTool (glob_files)

Find files matching glob patterns like `*.py`, `**/*.txt`, `/data/*.json`.

### Features
- Simple wildcard matching (`*.py`)
- Recursive matching (`**/*.txt`)
- Directory-specific patterns (`/tmp/*.json`)

### Usage

```python
from mayflower_sandbox.tools import FileGlobTool

tool = FileGlobTool(db_pool=pool, thread_id="user_1")

# Find all Python files
result = await tool._arun(pattern="*.py")

# Find all text files recursively
result = await tool._arun(pattern="**/*.txt")

# Find JSON files in specific directory
result = await tool._arun(pattern="/data/*.json")
```

### Parameters
- `pattern` (str, required): Glob pattern to match file paths

### Returns
List of matching file paths with sizes and types.

### Pattern Examples
- `*.py` - All Python files
- `**/*.txt` - All text files recursively
- `/tmp/*.json` - All JSON files in /tmp
- `/data/**/*.csv` - All CSV files under /data

## FileGrepTool (grep_files)

Search file contents using regular expressions with multiple output modes.

### Features
- Regex pattern matching
- Case-insensitive search
- Multiple output modes (files, content, count)
- Efficient content scanning

### Usage

```python
from mayflower_sandbox.tools import FileGrepTool

tool = FileGrepTool(db_pool=pool, thread_id="user_1")

# Find files containing "TODO"
result = await tool._arun(pattern="TODO")

# Show matching lines with content
result = await tool._arun(pattern="ERROR", output_mode="content")

# Count matches per file
result = await tool._arun(pattern="import", output_mode="count")

# Case-insensitive search
result = await tool._arun(pattern="error", case_insensitive=True)
```

### Parameters
- `pattern` (str, required): Regular expression pattern
- `output_mode` (str, optional): `files_with_matches`, `content`, or `count` (default: `files_with_matches`)
- `case_insensitive` (bool, optional): Perform case-insensitive search (default: `False`)

### Returns
Search results formatted according to output_mode:
- `files_with_matches`: List of file paths
- `content`: Matching lines with line numbers
- `count`: Match counts per file

### Output Modes
- **files_with_matches**: Shows only file paths containing matches (default)
- **content**: Shows matching lines with line numbers (limited to 10 per file)
- **count**: Shows number of matches per file

### Pattern Examples
- `TODO` - Find literal text
- `def \w+\(` - Find function definitions
- `^import ` - Find lines starting with "import"
- `error|warning` - Find either "error" or "warning"

## Thread Isolation

All tools use `thread_id` for complete isolation:
- Files are isolated per thread
- Different threads cannot access each other's files
- Enables multi-user applications with shared database

## Path Validation

All tools validate paths to prevent:
- Directory traversal attacks (`../`)
- Invalid characters
- Non-absolute paths

---

# JavaScript/TypeScript Code Execution Tools

⚡ **EXPERIMENTAL FEATURE** - Requires `enable_javascript=True` and Deno installation

These tools mirror the Python execution tools but run JavaScript/TypeScript code in a QuickJS-Wasm sandbox instead of Pyodide. They share the same PostgreSQL VFS as Python tools, enabling cross-language workflows.

## ExecuteJavascriptTool

**Tool ID**: `javascript_run`

Execute JavaScript or TypeScript code in an isolated QuickJS-Wasm sandbox with automatic VFS integration.

### Features
- Fast initialization (~1-5ms vs ~500-1000ms for Python)
- Automatic file pre-loading from PostgreSQL VFS
- Automatic file post-saving to PostgreSQL VFS
- No network access (sandboxed execution)
- Timeout protection (default: 60 seconds)
- Captures console.log/console.error output
- Returns created files
- Basic TypeScript support (runtime transpilation)

### Usage

```python
from mayflower_sandbox.tools import ExecuteJavascriptTool

tool = ExecuteJavascriptTool(
    db_pool=pool,
    thread_id="user_1"
)

result = await tool._arun(code="""
// VFS files are automatically available
const data = JSON.parse(readFile('/data/input.json'));

// Process data with JavaScript
const processed = data.values.map(v => v * 2);
const sum = processed.reduce((a, b) => a + b, 0);

// Write results back to VFS
writeFile('/data/output.json', JSON.stringify({
    processed,
    sum,
    count: processed.length
}, null, 2));

// Always use console.log() for output!
console.log('Processed', data.values.length, 'values');
console.log('Sum:', sum);
""")
```

### Parameters
- `code` (str, required): JavaScript or TypeScript code to execute

### Returns
String containing console output and information about created files.

### VFS Functions Available in Code

- `writeFile(path, content)` - Create/update file in VFS
- `readFile(path)` - Read file from VFS as string
- `listFiles()` - List all files in VFS
- `console.log(...)` - Print to stdout (captured)
- `console.error(...)` - Print to stderr (captured)

### Supported JavaScript Features

✅ **Supported:**
- ES6+ syntax (arrow functions, const/let, template strings)
- Array methods (map, filter, reduce, forEach, etc.)
- JSON.stringify and JSON.parse
- Math operations
- String manipulation
- Regular expressions
- Date and time

❌ **Not Supported:**
- Node.js built-ins (no `require('fs')`, `require('http')`, etc.)
- npm packages
- `fetch` or network access
- `async`/`await` for external operations
- Browser APIs (DOM, localStorage, etc.)

### Limitations

- **No network access**: `fetch()` is not available
- **No Node.js modules**: Use VFS functions instead of `require('fs')`
- **Pure JavaScript only**: No npm packages
- **Basic TypeScript**: Runtime transpilation only, no advanced features
- **Always log output**: Use `console.log()` to see results

### Example: Cross-Language Workflow

```python
# Step 1: Python creates data
python_tool = ExecutePythonTool(db_pool=pool, thread_id="user_1")
await python_tool._arun(code="""
import json
data = {'numbers': [10, 20, 30, 40, 50]}
with open('/data/input.json', 'w') as f:
    json.dump(data, f)
print('Data created by Python')
""")

# Step 2: JavaScript processes data
javascript_tool = ExecuteJavascriptTool(db_pool=pool, thread_id="user_1")
await javascript_tool._arun(code="""
const data = JSON.parse(readFile('/data/input.json'));
const sum = data.numbers.reduce((a, b) => a + b, 0);
const avg = sum / data.numbers.length;

writeFile('/data/result.json', JSON.stringify({ sum, avg }));
console.log('JavaScript calculated: sum=' + sum + ', avg=' + avg);
""")

# Step 3: Python reads results
await python_tool._arun(code="""
import json
with open('/data/result.json') as f:
    result = json.load(f)
print(f"Python sees: sum={result['sum']}, avg={result['avg']}")
""")
```

## RunJavascriptFileTool

**Tool ID**: `javascript_run_file`

Execute JavaScript or TypeScript files stored in the VFS.

### Features
- Execute .js or .ts files from PostgreSQL VFS
- Same features as `ExecuteJavascriptTool`
- Useful for re-running scripts or organized projects

### Usage

```python
from mayflower_sandbox.tools import RunJavascriptFileTool

tool = RunJavascriptFileTool(db_pool=pool, thread_id="user_1")

# Execute a .js file
result = await tool._arun(file_path="/scripts/process.js")

# Execute a .ts file
result = await tool._arun(file_path="/scripts/analyze.ts")
```

### Parameters
- `file_path` (str, required): Path to .js or .ts file in VFS (e.g., `/scripts/process.js`)

### Returns
String containing:
- Which file was executed
- Console output (stdout/stderr)
- Information about created files

### Example Workflow

```python
# Step 1: Create a JavaScript file using FileWriteTool
from mayflower_sandbox.tools import FileWriteTool

write_tool = FileWriteTool(db_pool=pool, thread_id="user_1")
await write_tool._arun(
    file_path="/scripts/data_processor.js",
    content="""
const data = JSON.parse(readFile('/data/raw.json'));
const filtered = data.filter(item => item.value > 100);
writeFile('/data/filtered.json', JSON.stringify(filtered, null, 2));
console.log('Filtered', filtered.length, 'items');
"""
)

# Step 2: Run the JavaScript file
run_tool = RunJavascriptFileTool(db_pool=pool, thread_id="user_1")
result = await run_tool._arun(file_path="/scripts/data_processor.js")
```

## ExecuteJavascriptCodeTool

**Tool ID**: `javascript_run_prepared`

Execute JavaScript/TypeScript code from graph state (state-based extraction).

This tool mirrors `python_run_prepared` and solves the same problem: when LLMs try to pass large code blocks through tool parameters, the serialization layer can drop or truncate them. This tool extracts code from graph state instead.

### Features
- Code extracted from graph state (`pending_content_map`)
- Same execution features as `ExecuteJavascriptTool`
- Automatic file saving to VFS
- LangGraph Command return type support
- State cleanup after execution

### Usage

```python
from mayflower_sandbox.tools import ExecuteJavascriptCodeTool

tool = ExecuteJavascriptCodeTool(db_pool=pool, thread_id="user_1")

# In LangGraph, the code is stored in state by the LLM
state = {
    "pending_content_map": {
        "tool_call_id_123": """
const numbers = [1, 2, 3, 4, 5, 10, 20, 30];
const filtered = numbers.filter(n => n > 5);
const doubled = filtered.map(n => n * 2);
const sum = doubled.reduce((a, b) => a + b, 0);

writeFile('/data/results.json', JSON.stringify({
    original: numbers,
    filtered: filtered,
    doubled: doubled,
    sum: sum
}, null, 2));

console.log('Sum of doubled filtered numbers:', sum);
"""
    }
}

# Tool extracts code from state using tool_call_id
result = await tool._arun(
    file_path="/tmp/process.js",
    description="Process and filter numbers",
    _state=state,
    tool_call_id="tool_call_id_123"
)
```

### Parameters
- `file_path` (str, optional): Where to save the code (default: `/tmp/script.js`)
- `description` (str, optional): Brief description of what the code does
- `_state` (dict, internal): Graph state containing `pending_content_map`
- `tool_call_id` (str, internal): ID to look up code in `pending_content_map`

### Returns
String containing execution results, or LangGraph `Command` object with state updates.

### When to Use

Use `javascript_run_prepared` instead of `javascript_run` when:
- Code is large (20+ lines)
- Code has complex string escaping
- You encounter "missing required parameter" errors
- Code is too large for tool parameter serialization

### LangGraph Integration

This tool is designed for use with LangGraph's custom tool nodes that inject graph state:

```python
from langgraph.prebuilt import create_react_agent
from mayflower_sandbox.tools import create_sandbox_tools

# Create tools with JavaScript support
tools = create_sandbox_tools(db_pool, enable_javascript=True)

# LangGraph automatically handles state injection for *_run_prepared tools
agent = create_react_agent(llm, tools)
```

## JavaScript Tool Comparison

| Feature | `javascript_run` | `javascript_run_file` | `javascript_run_prepared` |
|---------|------------------|----------------------|--------------------------|
| Code source | Tool parameter | VFS file (.js/.ts) | Graph state |
| Best for | Small snippets | Reusable scripts | Large code blocks |
| File saved | No | N/A (already in VFS) | Yes (to file_path) |
| State injection | No | No | Yes (requires LangGraph) |
| Max code size | ~10-20 lines | Unlimited (VFS limit) | Unlimited (via state) |

## Installation Requirements

JavaScript/TypeScript tools require Deno to be installed:

```bash
# Install Deno (macOS/Linux)
curl -fsSL https://deno.land/x/install/install.sh | sh

# Install Deno (Windows)
irm https://deno.land/install.ps1 | iex

# Verify installation
deno --version
```

If Deno is not installed, tools will fail with a clear error message.

## Security Model

JavaScript/TypeScript tools have the same security model as Python tools:

- ✅ **VFS access only** - No host filesystem access
- ✅ **No network** - `fetch()` is not available
- ✅ **No process access** - No `process.env` or system calls
- ✅ **Sandboxed execution** - Code runs in WebAssembly
- ✅ **Resource limits** - Same file size/count limits as Python
- ✅ **Timeout protection** - Configurable execution timeout

See [JavaScript/TypeScript Sandbox](javascript.md) for detailed security documentation.

## Performance

- **Initialization**: ~1-5ms (vs ~500-1000ms for Python)
- **Memory footprint**: ~5-10MB (vs ~50-100MB for Python)
- **Best for**: JSON manipulation, text processing, quick calculations
- **Not ideal for**: Heavy numerical computation (use Python with NumPy instead)

## Related Documentation

- [JavaScript/TypeScript Sandbox](javascript.md) - Comprehensive JavaScript sandbox documentation
- [Helpers Reference](helpers.md) - Document processing helpers available in ExecutePythonTool
- [Advanced Features](advanced.md) - Stateful execution, file server, cleanup
- [Examples](examples.md) - Complete working examples
