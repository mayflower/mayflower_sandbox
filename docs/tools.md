# Tools Reference

Mayflower Sandbox provides 5 LangChain tools that extend `BaseTool` for use with LangGraph agents.

## Creating Tools

```python
from mayflower_sandbox.tools import create_sandbox_tools

# Create all 5 tools for a specific thread
tools = create_sandbox_tools(
    db_pool=db_pool,
    thread_id="user_123",
    allow_net=True  # Enable network access for Python execution
)

# Use with LangGraph
from langgraph.prebuilt import create_react_agent
agent = create_react_agent(llm, tools)
```

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

## Related Documentation

- [Helpers Reference](helpers.md) - Document processing helpers available in ExecutePythonTool
- [Advanced Features](advanced.md) - Stateful execution, file server, cleanup
- [Examples](examples.md) - Complete working examples
