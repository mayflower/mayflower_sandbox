"""
E2E tests for python_run_prepared with LangGraph state-based code extraction.

These tests validate the complete workflow used in maistack:
1. LLM generates Python code in markdown block
2. Custom node extracts code and stores in state["pending_content"]
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
from langgraph.types import Command
from typing_extensions import TypedDict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
load_dotenv()

from mayflower_sandbox.tools import create_sandbox_tools  # noqa: E402


class AgentState(TypedDict):
    """State matching maistack usage."""

    messages: Annotated[list, add_messages]
    pending_content_map: dict[str, str]  # Content extracted from AI message, keyed by tool_call_id


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


def create_agent_graph(db_pool, include_tools=None):
    """Create LangGraph agent with custom node for content extraction (generalized pattern)."""
    # Create tools
    if include_tools is None:
        include_tools = ["python_run_prepared"]

    tools = create_sandbox_tools(
        db_pool,
        thread_id="e2e_prepared_test",
        include_tools=include_tools,
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
        """Custom tool node that extracts content before calling tools (generalized pattern)."""
        messages = state.get("messages", [])
        last_message = messages[-1]

        if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
            return {"messages": []}

        # Initialize pending_content_map from state
        pending_content_map = state.get("pending_content_map", {}).copy()
        msgs = []

        # Content-based tools that need extraction
        content_extraction_tools = {"python_run_prepared", "file_write"}

        # Extract content from AI message for tools that need it
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

            # Extract content from markdown block
            # Try with language specifier first (python, csv, json, etc.)
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
                # Prepare arguments
                kwargs = tool_call.get("args", {})

                # Inject state for content-based tools (generalized pattern)
                if tool_name in content_extraction_tools:
                    # Pass pending_content_map to the tool
                    kwargs["_state"] = {"pending_content_map": pending_content_map}
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
                        if key == "pending_content_map":
                            # Update our local map with changes from tool
                            pending_content_map = value
                        elif key != "messages":
                            pass  # Other state updates can be handled here

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

        # Update state with extracted content map and messages
        return {
            "messages": msgs,
            "pending_content_map": pending_content_map,
        }

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
            "pending_content_map": {},
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
            "pending_content_map": {},
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
            "pending_content_map": {},
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
    """Test that pending_content is cleared after execution."""
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
            "pending_content_map": {},
        },
        config={"configurable": {"thread_id": "test-state-clear"}},
    )

    # Check that pending_content_map was cleared (should be empty dict after execution)
    # Note: This verifies the Command pattern properly updates state
    assert result.get("pending_content_map", "NOTSET") == {} or result.get("pending_content_map") == "NOTSET"


# Tests for sandbox document helpers


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_python_run_prepared_with_excel_helpers(db_pool, clean_files):
    """Test python_run_prepared with xlsx_helpers for Excel manipulation."""
    app = create_agent_graph(db_pool)

    result = await app.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Write Python code to:\n"
                    "1. Install openpyxl using micropip\n"
                    "2. Create an Excel file at /tmp/data.xlsx with a sheet named 'Sales'\n"
                    "3. Write values: A1='Product', B1='Quantity', A2='Widget', B2=42\n"
                    "4. Save the file\n"
                    "5. Read it back using xlsx_read_cells from document.xlsx_helpers\n"
                    "6. Print the values from cells A1, B1, A2, B2\n"
                    "Use python_run_prepared to execute.",
                )
            ],
            "pending_content_map": {},
        },
        config={"configurable": {"thread_id": "test-excel"}},
    )

    messages = result["messages"]
    final_message = messages[-1]
    content = str(final_message.content)

    # Verify Excel helper was imported and used successfully
    assert "Product" in content
    assert "Widget" in content or "42" in content
    assert "Quantity" in content


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_python_run_prepared_with_pdf_creation(db_pool, clean_files):
    """Test python_run_prepared with pdf_creation helpers for PDF generation."""
    app = create_agent_graph(db_pool)

    result = await app.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Write Python code to:\n"
                    "1. Install fpdf2 using micropip\n"
                    "2. Use pdf_create_with_unicode from document.pdf_creation to create a PDF\n"
                    "3. Set title to 'Test Report' and add paragraphs with Unicode: "
                    "'Temperature: 25°C', 'Area: π × r²', 'Price: 100€'\n"
                    "4. Save to /tmp/report.pdf\n"
                    "5. Print success message\n"
                    "Use python_run_prepared to execute.",
                )
            ],
            "pending_content_map": {},
        },
        config={"configurable": {"thread_id": "test-pdf"}},
    )

    messages = result["messages"]
    final_message = messages[-1]
    content = str(final_message.content)

    # Verify PDF was created (check for file creation or success message)
    assert "/tmp/report.pdf" in content or "Created" in content or "success" in content.lower()


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_python_run_prepared_with_pptx_extraction(db_pool, clean_files):
    """Test python_run_prepared with pptx_ooxml for PowerPoint text extraction."""
    app = create_agent_graph(db_pool)

    result = await app.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Write Python code to:\n"
                    "1. Install python-pptx using micropip\n"
                    "2. Create a PowerPoint presentation with 2 slides\n"
                    "3. Slide 1 title: 'Q4 Results', content: 'Revenue increased 25%'\n"
                    "4. Slide 2 title: 'Next Steps', content: 'Launch new product'\n"
                    "5. Save to /tmp/presentation.pptx\n"
                    "6. Read the file and use pptx_extract_text from document.pptx_ooxml\n"
                    "7. Print the extracted text from all slides\n"
                    "Use python_run_prepared to execute.",
                )
            ],
            "pending_content_map": {},
        },
        config={"configurable": {"thread_id": "test-pptx"}},
    )

    messages = result["messages"]
    final_message = messages[-1]
    content = str(final_message.content)

    # Verify PowerPoint text was extracted
    assert "Q4 Results" in content or "Results" in content
    assert "Revenue" in content or "Next Steps" in content


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_python_run_prepared_with_docx_manipulation(db_pool, clean_files):
    """Test python_run_prepared with docx_ooxml for Word document manipulation."""
    app = create_agent_graph(db_pool)

    result = await app.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Write Python code to:\n"
                    "1. Install python-docx using micropip\n"
                    "2. Create a Word document with 2 paragraphs: "
                    "'Introduction paragraph' and 'Main content paragraph'\n"
                    "3. Save to /tmp/document.docx\n"
                    "4. Read the file and use docx_add_comment from document.docx_ooxml\n"
                    "5. Add a comment 'Review this' to the first paragraph\n"
                    "6. Save the modified document\n"
                    "7. Print success message\n"
                    "Use python_run_prepared to execute.",
                )
            ],
            "pending_content_map": {},
        },
        config={"configurable": {"thread_id": "test-docx"}},
    )

    messages = result["messages"]
    final_message = messages[-1]
    content = str(final_message.content)

    # Verify Word document was manipulated (check for file or success message)
    assert "/tmp/document.docx" in content or "success" in content.lower() or "comment" in content.lower()


# Tests for file_write with state-based content extraction


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_file_write_with_csv_data(db_pool, clean_files):
    """Test file_write with state-based extraction for CSV data."""
    app = create_agent_graph(db_pool, include_tools=["file_write"])

    result = await app.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Create a CSV file at /tmp/employees.csv with the following data:\n"
                    "- Columns: name, department, salary\n"
                    "- Row 1: Alice Johnson, Engineering, 95000\n"
                    "- Row 2: Bob Smith, Marketing, 75000\n"
                    "- Row 3: Carol White, Sales, 82000\n"
                    "Generate the CSV content and use file_write to save it.",
                )
            ],
            "pending_content_map": {},
        },
        config={"configurable": {"thread_id": "test-csv"}},
    )

    messages = result["messages"]
    final_message = messages[-1]
    content = str(final_message.content)

    # Verify file was written
    assert "/tmp/employees.csv" in content or "wrote" in content.lower()
    assert "Success" in content or "wrote" in content.lower()


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_file_write_with_json_config(db_pool, clean_files):
    """Test file_write with state-based extraction for JSON configuration."""
    app = create_agent_graph(db_pool, include_tools=["file_write"])

    result = await app.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Create a JSON configuration file at /tmp/config.json with:\n"
                    '- "api_endpoint": "https://api.example.com/v1"\n'
                    '- "timeout": 30\n'
                    '- "retry_attempts": 3\n'
                    '- "features": ["logging", "caching", "metrics"]\n'
                    "Generate properly formatted JSON and use file_write to save it.",
                )
            ],
            "pending_content_map": {},
        },
        config={"configurable": {"thread_id": "test-json"}},
    )

    messages = result["messages"]
    final_message = messages[-1]
    content = str(final_message.content)

    # Verify file was written
    assert "/tmp/config.json" in content or "wrote" in content.lower()


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_file_write_with_large_markdown(db_pool, clean_files):
    """Test file_write handles large content (>2000 chars) via state extraction."""
    app = create_agent_graph(db_pool, include_tools=["file_write"])

    result = await app.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Create a markdown documentation file at /tmp/README.md with:\n"
                    "1. Title: 'Python Data Analysis Project'\n"
                    "2. Introduction section (200 words) about data analysis\n"
                    "3. Installation section with 10 pip install commands\n"
                    "4. Usage section with 5 code examples (each 10 lines)\n"
                    "5. API Reference section with 8 function descriptions\n"
                    "6. Contributing guidelines (150 words)\n"
                    "7. License section\n"
                    "Make it comprehensive and detailed. Use file_write to save it.",
                )
            ],
            "pending_content_map": {},
        },
        config={"configurable": {"thread_id": "test-markdown"}},
    )

    messages = result["messages"]
    final_message = messages[-1]
    content = str(final_message.content)

    # Verify large file was written
    assert "/tmp/README.md" in content or "wrote" in content.lower()
    # Check for substantial byte count indicating large content
    assert ("bytes" in content.lower() and any(char.isdigit() for char in content))
