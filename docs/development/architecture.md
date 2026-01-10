# Instruction: Make Mayflower Tools Context-Aware (Dynamic thread_id)

## Problem

Currently, mayflower tools require `thread_id` at initialization, but in LangGraph it should come from runtime context (`RunnableConfig`). This causes:
- Tools bound to LLM at graph creation with dummy `thread_id="default"`
- Need to recreate tools for every request (performance overhead)
- Architectural mismatch with LangGraph patterns

## Solution

Make mayflower tools read `thread_id` dynamically from LangChain's callback context at execution time.

---

## Changes to Mayflower Sandbox

### 1. Update `SandboxTool` Base Class

**File:** `/home/johann/src/ml/mayflower-sandbox/src/mayflower_sandbox/tools/base.py`

**Change `thread_id` from required to optional:**

```python
class SandboxTool(BaseTool):
    """
    Base class for all sandbox tools.

    Provides connection to PostgreSQL and thread isolation.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    db_pool: asyncpg.Pool
    thread_id: str | None = None  # Make optional, will be read from context
```

**Add method to get thread_id from context:**

```python
def _get_thread_id(self, run_manager: AsyncCallbackManagerForToolRun | None = None) -> str:
    """Get thread_id from callback context or use instance default.

    Priority order:
    1. From LangGraph config via callback metadata
    2. From instance thread_id (if set)
    3. Default fallback: "default"
    """
    # Try to get from LangGraph config via callback manager
    if run_manager and hasattr(run_manager, 'metadata'):
        metadata = run_manager.metadata or {}
        if 'configurable' in metadata:
            thread_id = metadata['configurable'].get('thread_id')
            if thread_id:
                return thread_id

    # Try to get from tags (alternative location)
    if run_manager and hasattr(run_manager, 'tags'):
        tags = run_manager.tags or []
        for tag in tags:
            if tag.startswith('thread_id:'):
                return tag.split(':', 1)[1]

    # Fallback to instance thread_id
    if self.thread_id:
        return self.thread_id

    # Last resort default
    return "default"
```

### 2. Update All Tool Implementations

**Files to update:**
- `execute.py` - ExecutePythonTool
- `file_read.py` - FileReadTool
- `file_write.py` - FileWriteTool
- `file_edit.py` - FileEditTool
- `file_list.py` - FileListTool
- `file_delete.py` - FileDeleteTool
- `file_glob.py` - FileGlobTool
- `file_grep.py` - FileGrepTool

**Pattern to apply - Replace `self.thread_id` with `self._get_thread_id(run_manager)`:**

#### Example 1: `execute.py` (ExecutePythonTool)

**Before:**
```python
async def _arun(
    self,
    code: str,
    run_manager: AsyncCallbackManagerForToolRun | None = None,
) -> str:
    """Execute Python code in sandbox."""

    # Get error history
    error_history = get_error_history(self.thread_id)

    # Create executor
    executor = SandboxExecutor(
        self.db_pool, self.thread_id, allow_net=True, timeout_seconds=60.0
    )
    # ...
    if not result.success and result.stderr:
        add_error_to_history(self.thread_id, code_snippet, result.stderr)
```

**After:**
```python
async def _arun(
    self,
    code: str,
    run_manager: AsyncCallbackManagerForToolRun | None = None,
) -> str:
    """Execute Python code in sandbox."""

    # Get thread_id from context
    thread_id = self._get_thread_id(run_manager)

    # Get error history
    error_history = get_error_history(thread_id)

    # Create executor
    executor = SandboxExecutor(
        self.db_pool, thread_id, allow_net=True, timeout_seconds=60.0
    )
    # ...
    if not result.success and result.stderr:
        add_error_to_history(thread_id, code_snippet, result.stderr)
```

#### Example 2: `file_read.py` (FileReadTool)

**Before:**
```python
async def _arun(
    self,
    file_path: str,
    run_manager: AsyncCallbackManagerForToolRun | None = None,
) -> str:
    conn = await self.db_pool.acquire()
    try:
        row = await conn.fetchrow(
            "SELECT content, content_type FROM sandbox_filesystem WHERE thread_id = $1 AND file_path = $2",
            self.thread_id,
            normalized_path,
        )
```

**After:**
```python
async def _arun(
    self,
    file_path: str,
    run_manager: AsyncCallbackManagerForToolRun | None = None,
) -> str:
    # Get thread_id from context
    thread_id = self._get_thread_id(run_manager)

    conn = await self.db_pool.acquire()
    try:
        row = await conn.fetchrow(
            "SELECT content, content_type FROM sandbox_filesystem WHERE thread_id = $1 AND file_path = $2",
            thread_id,
            normalized_path,
        )
```

#### Example 3: `file_write.py` (FileWriteTool)

**Before:**
```python
async def _arun(
    self,
    file_path: str,
    content: str,
    run_manager: AsyncCallbackManagerForToolRun | None = None,
) -> str:
    await conn.execute(
        """INSERT INTO sandbox_filesystem (thread_id, file_path, content, size, content_type, created_at, modified_at)
           VALUES ($1, $2, $3, $4, $5, NOW(), NOW())""",
        self.thread_id,
        normalized_path,
        content_bytes,
        len(content_bytes),
        content_type,
    )
```

**After:**
```python
async def _arun(
    self,
    file_path: str,
    content: str,
    run_manager: AsyncCallbackManagerForToolRun | None = None,
) -> str:
    # Get thread_id from context
    thread_id = self._get_thread_id(run_manager)

    await conn.execute(
        """INSERT INTO sandbox_filesystem (thread_id, file_path, content, size, content_type, created_at, modified_at)
           VALUES ($1, $2, $3, $4, $5, NOW(), NOW())""",
        thread_id,
        normalized_path,
        content_bytes,
        len(content_bytes),
        content_type,
    )
```

**Apply same pattern to all 8 tools:**
1. Add `thread_id = self._get_thread_id(run_manager)` at start of `_arun()`
2. Replace all `self.thread_id` references with local `thread_id` variable

### 3. Update Factory

**File:** `/home/johann/src/ml/mayflower-sandbox/src/mayflower_sandbox/tools/factory.py`

**Make thread_id optional with default:**

**Before:**
```python
def create_sandbox_tools(
    db_pool: asyncpg.Pool,
    thread_id: str,
    include_tools: list[str] | None = None,
) -> list[SandboxTool]:
    """
    Create a set of sandbox tools for LangGraph.

    Args:
        db_pool: PostgreSQL connection pool
        thread_id: Thread ID for session isolation
        include_tools: List of tool names to include (default: all tools)
```

**After:**
```python
def create_sandbox_tools(
    db_pool: asyncpg.Pool,
    thread_id: str | None = None,
    include_tools: list[str] | None = None,
) -> list[SandboxTool]:
    """
    Create a set of sandbox tools for LangGraph.

    Args:
        db_pool: PostgreSQL connection pool
        thread_id: Thread ID for session isolation. If None, will be read from
                  callback context at runtime (recommended for LangGraph).
        include_tools: List of tool names to include (default: all tools)
```

### 4. Update Tests

**File:** `/home/johann/src/ml/mayflower-sandbox/tests/test_tools.py`

Update tests to verify context-aware behavior:

```python
async def test_thread_id_from_context():
    """Test that tools read thread_id from callback context."""
    from langchain_core.callbacks import AsyncCallbackManagerForToolRun

    # Create tool without thread_id
    tool = ExecutePythonTool(db_pool=db_pool, thread_id=None)

    # Create mock callback manager with metadata
    run_manager = AsyncCallbackManagerForToolRun(
        run_id=uuid.uuid4(),
        metadata={"configurable": {"thread_id": "test-thread-123"}}
    )

    # Tool should use thread_id from context
    thread_id = tool._get_thread_id(run_manager)
    assert thread_id == "test-thread-123"
```

---

## Changes to Maistack

### 1. Update Tool Creation in Agent

**File:** `/data/src/ml/maistack/services/langserve/app/agent.py`

**Create tools WITHOUT thread_id (will use context):**

**Before:**
```python
if db_pool:
    from mayflower_sandbox.tools import create_sandbox_tools

    # Create tools with default thread_id for tool binding
    # Actual instances will be recreated per-request with correct thread_id
    default_sandbox_tools = create_sandbox_tools(db_pool=db_pool, thread_id="default")
    tools = tools + default_sandbox_tools
    logger.info(f"Added {len(default_sandbox_tools)} mayflower sandbox tools")
```

**After:**
```python
if db_pool:
    from mayflower_sandbox.tools import create_sandbox_tools

    # Create tools without thread_id - they'll read from callback context
    sandbox_tools = create_sandbox_tools(db_pool=db_pool, thread_id=None)
    tools = tools + sandbox_tools
    logger.info(f"Added {len(sandbox_tools)} mayflower sandbox tools (context-aware)")
```

### 2. Remove Lazy Recreation Logic

**File:** `/data/src/ml/maistack/services/langserve/app/agent.py`

**DELETE THIS ENTIRE BLOCK from `custom_tool_node` (around lines 283-301):**

```python
# DELETE THIS:
# Create mayflower sandbox tools if not already in tools_by_name
# They need thread_id from config, so we create them per-request
thread_id = config.get("configurable", {}).get("thread_id", "default")

# Mayflower tool names
mayflower_tool_names = {
    "execute_python",
    "read_file",
    "write_file",
    "str_replace",
    "list_files",
    "delete_file",
    "glob_files",
    "grep_files",
}

# Check if any mayflower tool is missing from tools_by_name
if mayflower_tool_names.intersection(
    set(tc["name"] for tc in state["messages"][-1].tool_calls)
):
    if db_pool and not any(name in tools_by_name for name in mayflower_tool_names):
        from mayflower_sandbox.tools import create_sandbox_tools

        sandbox_tools = create_sandbox_tools(db_pool=db_pool, thread_id=thread_id)
        for tool in sandbox_tools:
            tools_by_name[tool.name] = tool
        logger.info(
            f"Created {len(sandbox_tools)} mayflower sandbox tools for thread {thread_id}"
        )
```

**Tools are now created once at graph initialization and reused for all requests.**

### 3. Verify Config Propagation

**Ensure LangGraph passes config through callbacks:**

LangGraph/LangChain should automatically propagate `config` through the callback chain. The `run_manager` should have access to metadata containing the thread_id.

**If config propagation doesn't work automatically**, add explicit config passing in tool invocation (check LangGraph ToolNode implementation).

---

## Alternative Implementation: Use contextvars

If callback metadata approach doesn't work reliably, use Python's `contextvars`:

### In Maistack (`agent.py`):

```python
from contextvars import ContextVar

# Module-level contextvar
_thread_id_context: ContextVar[str] = ContextVar('thread_id', default='default')

async def custom_tool_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    # Set thread_id in contextvar before executing tools
    thread_id = config.get("configurable", {}).get("thread_id", "default")
    _thread_id_context.set(thread_id)

    # Execute tools (they'll read from contextvar)
    # ...
```

### In Mayflower Sandbox (`base.py`):

```python
from contextvars import ContextVar

# Module-level contextvar (shared)
_thread_id_context: ContextVar[str] = ContextVar('thread_id', default='default')

class SandboxTool(BaseTool):
    def _get_thread_id(self, run_manager: AsyncCallbackManagerForToolRun | None = None) -> str:
        # Try contextvar first
        try:
            return _thread_id_context.get()
        except LookupError:
            pass

        # Fallback to instance thread_id or default
        return self.thread_id or "default"
```

**Pros of contextvars:**
- Guaranteed to work (Python built-in)
- No dependency on LangChain callback internals
- Works across async call boundaries

**Cons:**
- Requires manual context management
- More coupling between maistack and mayflower

---

## Testing Strategy

### Unit Tests

Test context-aware behavior:

```python
async def test_tool_reads_thread_id_from_callback():
    """Verify tools read thread_id from callback metadata."""
    # Test with metadata
    # Test with None
    # Test fallback to instance thread_id
```

### Integration Tests

Run existing integration tests:

```bash
# Should all pass with context-aware tools
docker exec maistack-langserve pytest /app/tests/integration/ -v
```

### Thread Isolation Test

Verify different thread_ids see different files:

```python
async def test_thread_isolation():
    """Test that different threads see isolated filesystems."""
    # Create file with thread_id="user1"
    # Try to read with thread_id="user2" (should not see it)
    # Read with thread_id="user1" (should see it)
```

---

## Benefits

✅ **No tool recreation overhead** - Tools created once at graph initialization
✅ **Proper thread isolation** - Each request uses correct thread_id from context
✅ **Cleaner architecture** - Context-aware tools match LangGraph patterns
✅ **Better performance** - No per-request tool instantiation
✅ **Compatible with LangGraph** - Standard callback-based approach

---

## Migration Checklist

### Mayflower Sandbox:
- [ ] Update `SandboxTool` base class (add `_get_thread_id()`, make `thread_id` optional)
- [ ] Update `ExecutePythonTool` (file: `execute.py`)
- [ ] Update `FileReadTool` (file: `file_read.py`)
- [ ] Update `FileWriteTool` (file: `file_write.py`)
- [ ] Update `FileEditTool` (file: `file_edit.py`)
- [ ] Update `FileListTool` (file: `file_list.py`)
- [ ] Update `FileDeleteTool` (file: `file_delete.py`)
- [ ] Update `FileGlobTool` (file: `file_glob.py`)
- [ ] Update `FileGrepTool` (file: `file_grep.py`)
- [ ] Update `create_sandbox_tools()` factory
- [ ] Add tests for context-aware behavior
- [ ] Update README/documentation

### Maistack:
- [ ] Update `create_agent_graph()` to create tools without thread_id
- [ ] Remove lazy recreation logic from `custom_tool_node`
- [ ] Test with Docker setup
- [ ] Run all integration tests
- [ ] Verify thread isolation works

### Final:
- [ ] Commit mayflower-sandbox changes
- [ ] Commit maistack changes
- [ ] Update deployment documentation
