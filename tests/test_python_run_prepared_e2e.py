"""
E2E tests for python_run_prepared with LangGraph state-based code extraction.

These tests validate the complete workflow used in maistack:
1. LLM generates Python code in markdown block
2. Custom node extracts code and stores in state["pending_code"]
3. LLM calls python_run_prepared tool
4. Tool executes code from state
5. Results are returned to agent
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
from langgraph.prebuilt import ToolNode
from langgraph.types import Command
from typing_extensions import TypedDict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
load_dotenv()

from mayflower_sandbox.tools import create_sandbox_tools  # noqa: E402


class AgentState(TypedDict):
    """State matching maistack usage."""

    messages: Annotated[list, add_messages]
    pending_code: str  # Code extracted from AI message for python_run_prepared


@pytest.fixture
async def db_pool():
    """Create test database connection pool."""
    db_config = {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "database": os.getenv("POSTGRES_DB", "mayflower_test"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "min_size": 10,
        "max_size": 50,
        "command_timeout": 60,
    }

    pool = await asyncpg.create_pool(**db_config)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('e2e_prepared_test', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_files(db_pool):
    """Clean files before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM sandbox_filesystem WHERE thread_id = 'e2e_prepared_test'"
        )
    yield


def create_agent_graph(db_pool):
    """Create LangGraph agent with custom node for code extraction (matching maistack)."""
    # Create tools - only include python_run_prepared
    tools = create_sandbox_tools(
        db_pool,
        thread_id="e2e_prepared_test",
        include_tools=["python_run_prepared"],
    )

    llm = ChatOpenAI(model="gpt-5-mini", temperature=0)
    llm_with_tools = llm.bind_tools(tools)

    # Create tools by name mapping for custom node
    tools_by_name = {tool.name: tool for tool in tools}

    def agent_node(state: AgentState) -> dict:
        """Agent node - calls LLM with tools."""
        messages = state.get("messages", [])
        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    async def custom_tool_node(state: AgentState, config: RunnableConfig) -> dict:
        """Custom tool node that extracts code before calling tools (matches maistack)."""
        messages = state.get("messages", [])
        last_message = messages[-1]

        if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
            return {"messages": []}

        tool_state = {}
        msgs = []

        # Extract code from AI message if python_run_prepared is being called
        # This matches the maistack pattern exactly
        tool_names = [tc["name"] for tc in last_message.tool_calls]
        if "python_run_prepared" in tool_names:
            if isinstance(last_message, AIMessage) and last_message.content:
                content = last_message.content

                # Handle case where content is a list of blocks
                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif isinstance(block, str):
                            text_parts.append(block)
                    content = "\n".join(text_parts)

                # Extract Python code from markdown block
                code_match = re.search(r"```python\n(.*?)\n```", content, re.DOTALL)
                if not code_match:
                    # Try without language specifier
                    code_match = re.search(r"```\n(.*?)\n```", content, re.DOTALL)

                if code_match:
                    extracted_code = code_match.group(1)
                    tool_state["pending_code"] = extracted_code

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
                # Prepare arguments
                kwargs = tool_call.get("args", {})

                # Inject state for python_run_prepared
                if tool_name == "python_run_prepared":
                    # Only pass pending_code, not full messages (avoids serialization issues)
                    serializable_state = {
                        "pending_code": state.get("pending_code", "")
                        or tool_state.get("pending_code", ""),
                    }
                    kwargs["_state"] = serializable_state
                    kwargs["tool_call_id"] = tool_call.get("id", "")

                # Call tool (use ainvoke for async)
                if hasattr(tool, "ainvoke"):
                    result = await tool.ainvoke(kwargs)
                else:
                    result = tool.invoke(kwargs)

                # Handle Command return type (LangGraph state update)
                if isinstance(result, Command):
                    # Extract state updates (matching maistack pattern)
                    update_dict = result.update
                    for key, value in update_dict.items():
                        if key != "messages":
                            tool_state[key] = value

                    # Extract the actual result string from Command.resume
                    result_str = result.resume if result.resume else "Tool executed successfully"
                    msgs.append(
                        ToolMessage(result_str, tool_call_id=tool_call.get("id", ""))
                    )
                else:
                    msgs.append(ToolMessage(result, tool_call_id=tool_call.get("id", "")))

            except Exception as e:
                msgs.append(
                    ToolMessage(
                        f"Error executing {tool_name}: {e}",
                        tool_call_id=tool_call.get("id", ""),
                    )
                )

        # Update state with extracted code and messages
        update = {"messages": msgs}
        if tool_state.get("pending_code") is not None:
            update["pending_code"] = tool_state["pending_code"]

        return update

    def should_continue(state: AgentState) -> str:
        """Decide whether to continue or end."""
        messages = state.get("messages", [])
        last_message = messages[-1]

        # If LLM makes a tool call, continue to tools
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"

        # Otherwise, end
        return END

    # Build graph
    workflow = StateGraph(AgentState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", custom_tool_node)

    workflow.set_entry_point("agent")
    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    workflow.add_edge("tools", "agent")

    # Compile with checkpointer
    return workflow.compile(checkpointer=MemorySaver())


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_python_run_prepared_with_code_extraction(db_pool, clean_files):
    """Test that python_run_prepared works with LLM-generated code extraction."""
    app = create_agent_graph(db_pool)

    # Ask agent to write code that calculates something
    result = await app.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Write Python code to calculate the factorial of 5 and print the result. "
                    "Use python_run_prepared to execute it.",
                )
            ],
            "pending_code": "",
        },
        config={"configurable": {"thread_id": "test-factorial"}},
    )

    # Get the final response
    messages = result["messages"]
    final_message = messages[-1]

    # Verify the agent executed the code and got the result (120)
    assert "120" in str(final_message.content)


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_python_run_prepared_with_file_creation(db_pool, clean_files):
    """Test python_run_prepared creates files that persist."""
    app = create_agent_graph(db_pool)

    result = await app.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Write Python code that creates a file /tmp/data.txt containing the text "
                    "'Test data from e2e'. Use python_run_prepared to execute it.",
                )
            ],
            "pending_code": "",
        },
        config={"configurable": {"thread_id": "test-file-creation"}},
    )

    messages = result["messages"]
    final_message = messages[-1]

    # Verify file was created (check that execution succeeded)
    assert "Created files:" in str(final_message.content) or "data.txt" in str(
        final_message.content
    )


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_python_run_prepared_with_computation(db_pool, clean_files):
    """Test python_run_prepared with multi-step computation."""
    app = create_agent_graph(db_pool)

    result = await app.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Write Python code to calculate the first 10 prime numbers and print them. "
                    "Use python_run_prepared to execute it.",
                )
            ],
            "pending_code": "",
        },
        config={"configurable": {"thread_id": "test-primes"}},
    )

    messages = result["messages"]
    final_message = messages[-1]

    # Verify it calculated primes (should mention 2, 3, 5, 7, etc.)
    content = str(final_message.content)
    assert "2" in content
    assert "3" in content
    assert "5" in content
    assert "7" in content


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_python_run_prepared_state_clearing(db_pool, clean_files):
    """Test that pending_code is cleared after execution."""
    app = create_agent_graph(db_pool)

    result = await app.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Write Python code to print 'Hello from state test'. "
                    "Use python_run_prepared.",
                )
            ],
            "pending_code": "",
        },
        config={"configurable": {"thread_id": "test-state-clear"}},
    )

    # Check that pending_code was cleared (should be empty string after execution)
    # Note: This verifies the Command pattern properly updates state
    assert result.get("pending_code", "NOTSET") == "" or result.get("pending_code") == "NOTSET"
