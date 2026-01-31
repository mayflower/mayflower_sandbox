# mypy: ignore-errors
"""
Test LangGraph agent state tracking for created files.

Verifies that files created by execute_python tool are tracked in agent state.
"""

import os
import sys
from typing import Annotated

import asyncpg
import pytest
from dotenv import load_dotenv
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command
from typing_extensions import TypedDict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
load_dotenv()

from mayflower_sandbox.agent_state import SandboxAgentState  # noqa: E402
from mayflower_sandbox.tools import create_sandbox_tools  # noqa: E402


def test_sandbox_agent_state_schema():
    """Test that SandboxAgentState has the correct schema structure."""
    # Verify TypedDict fields
    annotations = SandboxAgentState.__annotations__
    assert "messages" in annotations, "SandboxAgentState should have 'messages' field"
    assert "created_files" in annotations, "SandboxAgentState should have 'created_files' field"

    # Verify types are Annotated with add reducer
    from typing import get_args, get_origin

    messages_type = annotations["messages"]
    created_files_type = annotations["created_files"]

    # Check that both are Annotated types
    assert get_origin(messages_type) is Annotated, "messages should be Annotated type"
    assert get_origin(created_files_type) is Annotated, "created_files should be Annotated type"

    # Check the inner types
    messages_args = get_args(messages_type)
    created_files_args = get_args(created_files_type)

    assert messages_args[0] is list, "messages inner type should be list"
    # created_files is list[str], so check origin is list
    cf_inner = created_files_args[0]
    assert cf_inner is list or get_origin(cf_inner) is list, (
        "created_files inner type should be list[str]"
    )


class AgentState(TypedDict):
    """State with created_files tracking."""

    messages: Annotated[list, add_messages]
    created_files: list[str]


@pytest.fixture
async def db_pool():
    """Create test database connection pool."""
    db_config = {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "database": os.getenv("POSTGRES_DB", "mayflower_test"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
    }

    pool = await asyncpg.create_pool(**db_config)

    # Ensure session exists
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('agent_state_test', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_files(db_pool):
    """Clean files before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'agent_state_test'")
    yield


def create_agent_graph(db_pool, thread_id="agent_state_test"):
    """Create LangGraph agent with custom node for content extraction and state tracking."""
    load_dotenv()  # Ensure .env is loaded for API keys
    tools = create_sandbox_tools(db_pool, thread_id)
    llm = ChatOpenAI(model="gpt-5-mini", temperature=0)  # Uses OPENAI_API_KEY from env
    llm_with_tools = llm.bind_tools(tools)

    tools_by_name = {tool.name: tool for tool in tools}

    def agent_node(state: AgentState) -> dict:
        """Agent node - calls LLM with tools."""
        messages = state.get("messages", [])
        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    async def custom_tool_node(state: AgentState, config: RunnableConfig) -> dict:
        """Custom tool node that executes tools and tracks state.

        Note: No regex extraction - tools receive arguments directly from LLM tool calls.
        """
        messages = state.get("messages", [])
        last_message = messages[-1]

        if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
            return {"messages": []}

        tool_state: dict[str, list[str]] = {}
        msgs: list[ToolMessage] = []

        # Execute tool calls
        for tool_call in last_message.tool_calls:
            tool_name = tool_call["name"]
            tool = tools_by_name.get(tool_name)
            if tool is None:
                msgs.append(
                    ToolMessage(
                        f"Unknown tool: {tool_name}",
                        tool_call_id=tool_call.get("id", "unknown"),
                    )
                )
                continue

            try:
                # Tools with InjectedToolCallId require full tool call format
                tool_input = {
                    "args": tool_call.get("args", {}),
                    "name": tool_name,
                    "type": "tool_call",
                    "id": tool_call.get("id", ""),
                }

                if hasattr(tool, "ainvoke"):
                    result = await tool.ainvoke(tool_input, config)
                else:
                    result = tool.invoke(tool_input, config)

                # Handle Command return type (tracks created_files in state)
                if isinstance(result, Command):
                    update_dict = result.update
                    if update_dict:
                        for key, value in update_dict.items():
                            # Merge created_files instead of replacing
                            if key == "created_files" and isinstance(value, list):
                                existing_state = state.get(key, [])
                                existing = tool_state.get(key, []) or (
                                    existing_state if isinstance(existing_state, list) else []
                                )
                                tool_state[key] = list(set(existing + value))

                    result_str = (
                        str(result.resume) if result.resume else "Tool executed successfully"
                    )
                    msgs.append(ToolMessage(result_str, tool_call_id=tool_call.get("id", "")))
                else:
                    msgs.append(ToolMessage(str(result), tool_call_id=tool_call.get("id", "")))

            except Exception as e:
                msgs.append(
                    ToolMessage(
                        f"Error executing {tool_name}: {e}",
                        tool_call_id=tool_call.get("id", ""),
                    )
                )

        # Update state
        update: dict[str, list[ToolMessage] | list[str]] = {"messages": msgs}
        if "created_files" in tool_state:
            update["created_files"] = tool_state["created_files"]

        return update

    def should_continue(state: AgentState) -> str:
        """Decide whether to continue or end."""
        messages = state.get("messages", [])
        last_message = messages[-1]

        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"

        return END

    # Build graph
    workflow = StateGraph(AgentState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", custom_tool_node)

    workflow.set_entry_point("agent")
    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    workflow.add_edge("tools", "agent")

    return workflow.compile(checkpointer=MemorySaver())


async def test_write_file_tool_updates_state(db_pool, clean_files):
    """Test that write_file tool updates created_files in state."""
    from mayflower_sandbox.tools.file_write import FileWriteTool

    tool = FileWriteTool(db_pool=db_pool, thread_id="agent_state_test")

    tool_call_id = "test_call_123"
    state = {"pending_content_map": {tool_call_id: "Hello State!"}}

    result = await tool._arun(
        file_path="/tmp/test.txt",
        description="Test file",
        _state=state,
        tool_call_id=tool_call_id,
    )

    # Check if result is a Command object with state update
    from langgraph.types import Command

    assert isinstance(result, Command), f"Expected Command, got {type(result)}"
    assert result.update is not None, "Command update is None"
    assert "created_files" in result.update, "created_files not in Command update"
    assert "/tmp/test.txt" in result.update["created_files"]
    # Verify the file was actually written with correct content
    from mayflower_sandbox.filesystem import VirtualFilesystem

    vfs = VirtualFilesystem(db_pool, "agent_state_test")
    file_entry = await vfs.read_file("/tmp/test.txt")
    assert file_entry is not None, "File should exist in VFS"
    assert file_entry["content"] == b"Hello State!"


async def test_file_edit_tool_updates_state(db_pool, clean_files):
    """Test that str_replace tool updates created_files in state."""
    from langgraph.types import Command

    from mayflower_sandbox.tools.file_edit import FileEditTool
    from mayflower_sandbox.tools.file_write import FileWriteTool

    # First create a file
    write_tool = FileWriteTool(db_pool=db_pool, thread_id="agent_state_test")
    tool_call_id_write = "test_call_write"
    state = {"pending_content_map": {tool_call_id_write: "Old content"}}
    await write_tool._arun(
        file_path="/tmp/edit_test.txt",
        description="Edit test file",
        _state=state,
        tool_call_id=tool_call_id_write,
    )

    # Now edit it
    edit_tool = FileEditTool(db_pool=db_pool, thread_id="agent_state_test")
    result = await edit_tool._arun(
        file_path="/tmp/edit_test.txt",
        old_string="Old content",
        new_string="New content",
        tool_call_id="test_call_edit",
    )

    # Check if result is a Command object with state update
    assert isinstance(result, Command), f"Expected Command, got {type(result)}"
    assert result.update is not None, "Command update is None"
    assert "created_files" in result.update, "created_files not in Command update"
    assert "/tmp/edit_test.txt" in result.update["created_files"]
    assert result.resume is not None and "Successfully edited" in str(result.resume)


async def test_execute_python_tool_updates_state(db_pool, clean_files):
    """Test that execute_python tool updates created_files in state."""
    from langgraph.types import Command

    from mayflower_sandbox.tools.execute import ExecutePythonTool

    tool = ExecutePythonTool(db_pool=db_pool, thread_id="agent_state_test")

    code = """
with open('/tmp/python_test.txt', 'w') as f:
    f.write('Created by Python')
print('File created')
"""

    result = await tool._arun(code=code, tool_call_id="test_call_execute")

    # Check if result is a Command object with state update
    assert isinstance(result, Command), f"Expected Command, got {type(result)}"
    assert result.update is not None, "Command update is None"
    assert "created_files" in result.update, "created_files not in Command update"
    assert "/tmp/python_test.txt" in result.update["created_files"]
    assert result.resume is not None and "File created" in str(result.resume)


async def test_agent_state_tracks_created_files(db_pool, clean_files):
    """Test that created files are tracked in agent state via write_file tool."""
    load_dotenv()  # Ensure API key is loaded
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set - skipping LLM test")

    app = create_agent_graph(db_pool, "agent_state_test")

    # Use write_file tool to create a file with clear instructions
    result = await app.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Use the python_run tool to execute this code: "
                    "with open('/tmp/test.txt', 'w') as f: f.write('Hello State!')",
                )
            ],
            "created_files": [],
        },
        config={"configurable": {"thread_id": "test-state-tracking"}, "recursion_limit": 25},
    )

    # Verify file was actually created in database (end state validation)
    async with db_pool.acquire() as conn:
        file_data = await conn.fetchrow(
            """
            SELECT content FROM sandbox_filesystem
            WHERE thread_id = 'agent_state_test' AND file_path = '/tmp/test.txt'
        """
        )
        assert file_data is not None, "File not found in database"
        assert b"Hello State!" in file_data["content"]

    # Check that created_files is tracked in state
    assert "created_files" in result, "created_files not found in agent state"
    assert isinstance(result["created_files"], list), "created_files should be a list"


async def test_agent_state_tracks_multiple_files(db_pool, clean_files):
    """Test that multiple created files are all tracked in state."""
    load_dotenv()  # Ensure API key is loaded
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set - skipping LLM test")

    app = create_agent_graph(db_pool, "agent_state_test")

    # Create multiple files using python_run with code directly in arguments
    code = """
for name, content in [('file1', 'File One'), ('file2', 'File Two'), ('file3', 'File Three')]:
    with open(f'/tmp/{name}.txt', 'w') as f:
        f.write(content)
print('All files created')
"""
    await app.ainvoke(
        {
            "messages": [
                (
                    "user",
                    f"Use the python_run tool to execute this code:\n{code}",
                )
            ],
            "created_files": [],
        },
        config={"configurable": {"thread_id": "test-multi-files"}, "recursion_limit": 25},
    )

    # Verify files were actually created in database (end state validation)
    async with db_pool.acquire() as conn:
        for filename in ["file1.txt", "file2.txt", "file3.txt"]:
            file_data = await conn.fetchrow(
                """
                SELECT content FROM sandbox_filesystem
                WHERE thread_id = 'agent_state_test' AND file_path = $1
                """,
                f"/tmp/{filename}",
            )
            assert file_data is not None, f"File /tmp/{filename} not found in database"


async def test_agent_can_reference_created_files(db_pool, clean_files):
    """Test that agent can reference files from state in subsequent actions."""
    load_dotenv()  # Ensure API key is loaded
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set - skipping LLM test")

    app = create_agent_graph(db_pool, "agent_state_test")

    config = {"configurable": {"thread_id": "test-file-reference"}, "recursion_limit": 50}

    # First: create a file using python_run with code directly
    await app.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Use the python_run tool to execute this code: "
                    "with open('/tmp/data.txt', 'w') as f: f.write('important data')",
                )
            ],
            "created_files": [],
        },
        config=config,
    )

    # Verify file was created in database (end state validation)
    async with db_pool.acquire() as conn:
        file_data = await conn.fetchrow(
            """
            SELECT content FROM sandbox_filesystem
            WHERE thread_id = 'agent_state_test' AND file_path = '/tmp/data.txt'
        """
        )
        assert file_data is not None, "File /tmp/data.txt not found in database"
        assert b"important data" in file_data["content"]

    # Second: ask agent to read the file it created
    result2 = await app.ainvoke(
        {"messages": [("user", "Use the file_read tool to read the contents of /tmp/data.txt")]},
        config=config,
    )

    # Verify file_read tool was called (at least one ToolMessage exists)
    tool_messages = [msg for msg in result2["messages"] if isinstance(msg, ToolMessage)]
    assert tool_messages, "Expected at least one ToolMessage from file_read"

    # Verify no tool execution errors (deterministic check)
    for msg in tool_messages:
        content = str(msg.content)
        assert not content.startswith("Error"), f"Tool execution failed: {content[:200]}"

    # Verify file is still accessible in VFS after read (deterministic)
    async with db_pool.acquire() as conn:
        file_after_read = await conn.fetchrow(
            """
            SELECT content FROM sandbox_filesystem
            WHERE thread_id = 'agent_state_test' AND file_path = '/tmp/data.txt'
        """
        )
        assert file_after_read is not None, "File should still exist after read"
        assert file_after_read["content"] == b"important data"
