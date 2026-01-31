# Tools Reference

Mayflower Sandbox provides 12 LangChain tools that extend `BaseTool` for use with LangGraph agents.

## Creating Tools

```python
from mayflower_sandbox.tools import create_sandbox_tools

# Create all tools for a specific thread
tools = create_sandbox_tools(
    db_pool=db_pool,
    thread_id="user_123"
)

# Create context-aware tools (recommended for LangGraph)
tools = create_sandbox_tools(db_pool=db_pool, thread_id=None)

# Create only specific tools
tools = create_sandbox_tools(
    db_pool=db_pool,
    thread_id=None,
    include_tools=["python_run", "file_read", "file_write"]
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

## RunPythonFileTool

Execute existing Python files from the virtual filesystem.

### Usage

```python
from mayflower_sandbox.tools import RunPythonFileTool

tool = RunPythonFileTool(db_pool=pool, thread_id="user_1")
result = await tool._arun(file_path="/tmp/script.py")
```

### Parameters
- `file_path` (str, required): Path to the Python file in VFS

### Returns
String containing execution output (stdout).

### Best For
- Re-running previously created scripts
- Organized multi-file projects
- Scripts stored permanently in VFS

## ExecuteCodeTool (python_run_prepared)

Execute code from graph state using state-based extraction. This solves LangGraph/AG-UI tool parameter serialization issues for large code blocks.

### Usage

```python
from mayflower_sandbox.tools import ExecuteCodeTool

tool = ExecuteCodeTool(db_pool=pool, thread_id="user_1")
result = await tool._arun(
    file_path="/tmp/viz.py",
    description="Create subplot visualization"
)
```

### Parameters
- `file_path` (str, required): Where to save the code in VFS
- `description` (str, required): Description of what the code does

### How It Works
1. LLM generates Python code (stored in graph state's `pending_code` field)
2. LLM calls `python_run_prepared` with file_path and description
3. Tool extracts code from state, saves to VFS, and executes
4. Code is cleared from state after successful execution

### Best For
- Complex visualizations with subplots
- Large code blocks (20+ lines)
- Multi-step data analysis pipelines
- Code too large for tool parameter serialization

## SkillInstallTool

Install Claude Skills into the sandbox's virtual filesystem.

### Usage

```python
from mayflower_sandbox.tools import SkillInstallTool

tool = SkillInstallTool(db_pool=pool, thread_id="user_1")
result = await tool._arun(source="github:anthropics/skills/algorithmic-art")
```

### Parameters
- `source` (str, required): Skill source (e.g., `github:anthropics/skills/algorithmic-art`)

### How It Works
- Downloads `SKILL.md`, parses YAML front matter
- Writes package under `/site-packages/skills/<skill>/`
- Generated modules importable as `from skills.<skill_name> import instructions`
- Metadata persisted to `sandbox_skills` table

## MCPBindHttpTool

Bind Streamable HTTP MCP servers to make their tools available in the sandbox.

### Usage

```python
from mayflower_sandbox.tools import MCPBindHttpTool

tool = MCPBindHttpTool(db_pool=pool, thread_id="user_1")
result = await tool._arun(
    name="my_server",
    url="http://localhost:8000/mcp",
    headers={"Authorization": "Bearer token"}
)
```

### Parameters
- `name` (str, required): Server name for imports
- `url` (str, required): Streamable HTTP MCP endpoint URL
- `headers` (dict, optional): HTTP headers (e.g., auth tokens)

### How It Works
- Caches connection metadata in `sandbox_mcp_servers`
- Generates wrappers in `/site-packages/servers/<name>/`
- Tools available via `from servers.<name> import tools`
- Calls routed back to host via `mayflower_mcp.call`

### Security
Set `MAYFLOWER_MCP_ALLOWLIST` (comma-separated names or host suffixes) to restrict which servers can be bound.

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
- [Advanced Features](../advanced/stateful-execution.md) - Stateful execution, file server, cleanup
- [Examples](examples.md) - Complete working examples
