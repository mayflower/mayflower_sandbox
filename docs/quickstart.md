# Quick Start Guide

## Basic Python Execution

Execute Python code with automatic file persistence:

```python
import asyncpg
from mayflower_sandbox import SandboxExecutor

# Create database connection
db_pool = await asyncpg.create_pool(
    host="localhost",
    database="mayflower_test",
    user="postgres",
    password="postgres"
)

# Create executor for a specific user/thread
executor = SandboxExecutor(
    db_pool=db_pool,
    thread_id="user_123",
    allow_net=True  # Enable network for micropip
)

# Execute Python code
result = await executor.execute("""
# Create a CSV file
with open('/tmp/data.csv', 'w') as f:
    f.write('name,value\\n')
    f.write('Alice,100\\n')
    f.write('Bob,200\\n')

# Process the data
import csv
total = 0
with open('/tmp/data.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        total += int(row['value'])

print(f'Total: {total}')
""")

print(result.stdout)  # Output: Total: 300
print(result.created_files)  # ['/tmp/data.csv']
```

Files are automatically:
1. **Pre-loaded** from PostgreSQL before execution
2. **Post-saved** to PostgreSQL after execution

## Using with LangGraph

Integrate with LangGraph agents:

```python
from mayflower_sandbox.tools import create_sandbox_tools
from langgraph.prebuilt import create_react_agent
from langchain_anthropic import ChatAnthropic

# Create tools for a specific user
tools = create_sandbox_tools(db_pool, thread_id="user_123")

# Create LangGraph agent
llm = ChatAnthropic(model="claude-3-5-sonnet-20241022")
agent = create_react_agent(llm, tools)

# Use the agent
result = await agent.ainvoke({
    "messages": [("user", "Create a CSV file with sample data and calculate the sum")]
})
```

The agent now has access to 5 tools:
- `execute_python` - Execute Python code
- `read_file` - Read files from VFS
- `write_file` - Write files to VFS
- `list_files` - List files
- `delete_file` - Delete files

## Working with Documents

Process Word, Excel, PowerPoint, and PDF files:

```python
result = await executor.execute("""
import micropip
await micropip.install('openpyxl')

from document.xlsx_helpers import xlsx_read_cells, xlsx_to_dict

# Read Excel file from VFS
xlsx_bytes = open('/tmp/data.xlsx', 'rb').read()

# Read specific cells
values = xlsx_read_cells(xlsx_bytes, 'Sheet1', ['A1', 'B2'])
print(f'Values: {values}')

# Convert sheet to dictionaries
data = xlsx_to_dict(xlsx_bytes, 'Sheet1')
print(f'Data: {data}')
""")
```

## Stateful Execution

Variables persist across executions:

```python
from mayflower_sandbox.session import StatefulExecutor

executor = StatefulExecutor(db_pool, thread_id="user_123")

# First execution
await executor.execute("x = 42")

# Second execution - x persists!
result = await executor.execute("print(x)")
print(result.stdout)  # Output: 42

# Reset session to clear state
await executor.reset_session()
```

## Next Steps

- [Tools Reference](tools.md) - Detailed documentation for all 5 tools
- [Helpers Reference](helpers.md) - Document processing helpers
- [Advanced Features](advanced.md) - File server, cleanup, and more
- [Examples](examples.md) - Complete working examples
