"""
Test LangGraph agent state tracking for created files.

Verifies that files created by execute_python tool are tracked in agent state.
"""

import os
import re
import sys
from typing import Annotated

import asyncpg
import pytest
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command
from typing_extensions import TypedDict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
load_dotenv()

from mayflower_sandbox.tools import create_sandbox_tools  # noqa: E402


class AgentState(TypedDict):
    """State with created_files tracking."""

    messages: Annotated[list, add_messages]
    pending_content_map: dict[str, str]
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
    llm = ChatOpenAI(model="gpt-5-mini", temperature=0, api_key=os.getenv("OPENAI_API_KEY"))
    llm_with_tools = llm.bind_tools(tools)

    tools_by_name = {tool.name: tool for tool in tools}

    def agent_node(state: AgentState) -> dict:
        """Agent node - calls LLM with tools."""
        messages = state.get("messages", [])
        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    async def custom_tool_node(state: AgentState, config: RunnableConfig) -> dict:
        """Custom tool node that extracts content and tracks state."""
        messages = state.get("messages", [])
        last_message = messages[-1]

        if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
            return {"messages": []}

        # Initialize pending_content_map from state
        pending_content_map = state.get("pending_content_map", {}).copy()
        tool_state = {}
        msgs = []

        # Content-based tools that need extraction
        content_extraction_tools = {"python_run_prepared", "file_write"}

        # Extract content from AI message
        if isinstance(last_message, AIMessage) and last_message.content:
            content = last_message.content

            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        text_parts.append(block)
                content = "\n".join(text_parts)

            # Extract from markdown block
            content_match = re.search(r"```(?:\w+)?\n(.*?)\n```", content, re.DOTALL)
            if content_match:
                extracted_content = content_match.group(1)
                # Store content in map using tool_call_id as key
                for tool_call in last_message.tool_calls:
                    if tool_call["name"] in content_extraction_tools:
                        tool_call_id = tool_call.get("id", "")
                        if tool_call_id:
                            pending_content_map[tool_call_id] = extracted_content

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
                kwargs = tool_call.get("args", {})

                # Inject state for content-based tools
                if tool_name in content_extraction_tools:
                    kwargs["_state"] = {"pending_content_map": pending_content_map}
                    kwargs["tool_call_id"] = tool_call.get("id", "")

                if hasattr(tool, "ainvoke"):
                    result = await tool.ainvoke(kwargs, config)
                else:
                    result = tool.invoke(kwargs, config)

                # Handle Command return type
                if isinstance(result, Command):
                    update_dict = result.update
                    for key, value in update_dict.items():
                        if key == "pending_content_map":
                            # Update our local map with changes from tool
                            pending_content_map = value
                        elif key != "messages":
                            # Merge created_files instead of replacing
                            if key == "created_files":
                                existing = tool_state.get(key, []) or state.get(key, [])
                                tool_state[key] = list(set(existing + value))
                            else:
                                tool_state[key] = value

                    result_str = result.resume if result.resume else "Tool executed successfully"
                    msgs.append(ToolMessage(result_str, tool_call_id=tool_call.get("id", "")))
                else:
                    msgs.append(ToolMessage(result, tool_call_id=tool_call.get("id", "")))

            except Exception as e:
                msgs.append(
                    ToolMessage(
                        f"Error executing {tool_name}: {e}",
                        tool_call_id=tool_call.get("id", ""),
                    )
                )

        # Update state
        update = {
            "messages": msgs,
            "pending_content_map": pending_content_map,
        }
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
    assert "created_files" in result.update, "created_files not in Command update"
    assert "/tmp/test.txt" in result.update["created_files"]
    assert result.resume == "Successfully wrote 12 bytes to /tmp/test.txt"


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
    assert "created_files" in result.update, "created_files not in Command update"
    assert "/tmp/edit_test.txt" in result.update["created_files"]
    assert "Successfully edited" in result.resume


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
    assert "created_files" in result.update, "created_files not in Command update"
    assert "/tmp/python_test.txt" in result.update["created_files"]
    assert "File created" in result.resume


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
                    "Generate the text 'Hello State!' and then use the file_write tool "
                    "to save it to /tmp/test.txt. Put the text content in a markdown code block.",
                )
            ],
            "pending_content_map": {},
            "created_files": [],
        },
        config={"configurable": {"thread_id": "test-state-tracking"}, "recursion_limit": 50},
    )

    # Check that created_files is in the state
    assert "created_files" in result, "created_files not found in agent state"
    assert isinstance(result["created_files"], list), "created_files should be a list"
    assert "/tmp/test.txt" in result["created_files"], "Created file not tracked in state"

    # Verify file was actually created in database
    async with db_pool.acquire() as conn:
        file_data = await conn.fetchrow(
            """
            SELECT content FROM sandbox_filesystem
            WHERE thread_id = 'agent_state_test' AND file_path = '/tmp/test.txt'
        """
        )
        assert file_data is not None, "File not found in database"
        assert b"Hello State!" in file_data["content"]


async def test_agent_state_tracks_multiple_files(db_pool, clean_files):
    """Test that multiple created files are all tracked in state."""
    load_dotenv()  # Ensure API key is loaded
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set - skipping LLM test")

    app = create_agent_graph(db_pool, "agent_state_test")

    # Create multiple files - use python_run to create multiple files at once
    result = await app.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Write Python code to create three text files:\n"
                    "1. /tmp/file1.txt with content 'File One'\n"
                    "2. /tmp/file2.txt with content 'File Two'\n"
                    "3. /tmp/file3.txt with content 'File Three'\n"
                    "Then print 'All files created'. Use python_run_prepared to execute.",
                )
            ],
            "pending_content_map": {},
            "created_files": [],
        },
        config={"configurable": {"thread_id": "test-multi-files"}, "recursion_limit": 50},
    )

    # Check that all files are tracked
    assert "created_files" in result, "created_files not found in agent state"
    assert isinstance(result["created_files"], list)

    # Should have all three files
    created_paths = result["created_files"]
    assert any("/tmp/file1.txt" in path for path in created_paths)
    assert any("/tmp/file2.txt" in path for path in created_paths)
    assert any("/tmp/file3.txt" in path for path in created_paths)


async def test_agent_can_reference_created_files(db_pool, clean_files):
    """Test that agent can reference files from state in subsequent actions."""
    load_dotenv()  # Ensure API key is loaded
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set - skipping LLM test")

    app = create_agent_graph(db_pool, "agent_state_test")

    config = {"configurable": {"thread_id": "test-file-reference"}, "recursion_limit": 50}

    # First: create a file
    result1 = await app.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Write Python code to create a file /tmp/data.txt with the text 'important data'. "
                    "Print 'File created'. Use python_run_prepared.",
                )
            ],
            "pending_content_map": {},
            "created_files": [],
        },
        config=config,
    )

    assert "created_files" in result1
    assert any("/tmp/data.txt" in path for path in result1["created_files"])

    # Second: ask agent to read the file it created
    result2 = await app.ainvoke(
        {"messages": [("user", "Use the file_read tool to read the contents of /tmp/data.txt")]},
        config=config,
    )

    # Agent should be able to access the file
    last_message = result2["messages"][-1]
    assert "important data" in last_message.content.lower()
