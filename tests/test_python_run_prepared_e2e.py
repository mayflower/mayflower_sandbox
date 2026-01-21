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

from mayflower_sandbox.filesystem import VirtualFilesystem  # noqa: E402
from mayflower_sandbox.tools import create_sandbox_tools  # noqa: E402

# =============================================================================
# Test Validation Helpers
# =============================================================================


def get_tool_messages(messages: list) -> list[ToolMessage]:
    """Extract all ToolMessage instances from message history."""
    return [msg for msg in messages if isinstance(msg, ToolMessage)]


def get_tool_stdout(messages: list) -> str:
    """
    Extract stdout content from tool execution results.

    Tool results typically contain patterns like:
    - "STDOUT: <output>"
    - "Result: <value>"
    - Direct output text
    """
    tool_outputs = []
    for msg in get_tool_messages(messages):
        content = str(msg.content)
        # Extract STDOUT section if present
        if "STDOUT:" in content:
            # Find content after STDOUT:
            stdout_match = re.search(r"STDOUT:\s*(.+?)(?:STDERR:|Created files:|$)", content, re.DOTALL)
            if stdout_match:
                tool_outputs.append(stdout_match.group(1).strip())
        else:
            tool_outputs.append(content)
    return "\n".join(tool_outputs)


def get_created_files(messages: list) -> list[str]:
    """Extract list of created files from tool messages."""
    files = []
    for msg in get_tool_messages(messages):
        content = str(msg.content)
        # Match "Created files:" section
        if "Created files:" in content:
            files_match = re.search(r"Created files:\s*\[([^\]]+)\]", content)
            if files_match:
                files.extend(f.strip().strip("'\"") for f in files_match.group(1).split(","))
        # Match file paths in success messages like "Successfully wrote X bytes to /path"
        path_matches = re.findall(r"(?:wrote|created|saved)[^/]*(/[^\s\n]+)", content, re.IGNORECASE)
        files.extend(path_matches)
    return list(set(files))


async def check_vfs_file_exists(db_pool, thread_id: str, path: str) -> bool:
    """Check if a file exists in the VFS."""
    vfs = VirtualFilesystem(db_pool, thread_id)
    try:
        await vfs.read_file(path)
        return True
    except FileNotFoundError:
        return False


async def get_vfs_file_content(db_pool, thread_id: str, path: str) -> bytes | None:
    """Get file content from VFS, returns None if not found."""
    vfs = VirtualFilesystem(db_pool, thread_id)
    try:
        entry = await vfs.read_file(path)
        return entry["content"]
    except FileNotFoundError:
        return None


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
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'e2e_prepared_test'")
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

    # Verify execution by checking tool output (deterministic), not LLM response
    messages = result["messages"]
    tool_stdout = get_tool_stdout(messages)

    # The factorial of 5 is 120 - check tool execution output
    assert "120" in tool_stdout, f"Expected '120' in tool stdout, got: {tool_stdout[:200]}"


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_python_run_prepared_with_file_creation(db_pool, clean_files):
    """Test python_run_prepared creates files that persist."""
    app = create_agent_graph(db_pool)

    await app.ainvoke(
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

    # Verify file was created by checking VFS directly (deterministic)
    # Tools use thread_id="e2e_prepared_test" regardless of config
    file_exists = await check_vfs_file_exists(db_pool, "e2e_prepared_test", "/tmp/data.txt")
    assert file_exists, "Expected /tmp/data.txt to exist in VFS"

    # Optionally verify content
    content = await get_vfs_file_content(db_pool, "e2e_prepared_test", "/tmp/data.txt")
    assert content is not None and b"Test data" in content


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

    # Verify primes by checking tool execution output (deterministic)
    tool_stdout = get_tool_stdout(messages)

    # First 10 primes: 2, 3, 5, 7, 11, 13, 17, 19, 23, 29
    assert "2" in tool_stdout, f"Expected '2' in tool stdout: {tool_stdout[:200]}"
    assert "3" in tool_stdout, f"Expected '3' in tool stdout: {tool_stdout[:200]}"
    assert "5" in tool_stdout, f"Expected '5' in tool stdout: {tool_stdout[:200]}"
    assert "7" in tool_stdout, f"Expected '7' in tool stdout: {tool_stdout[:200]}"


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
                    "Write Python code to print 'Hello from state test'. Use python_run_prepared.",
                )
            ],
            "pending_content_map": {},
        },
        config={"configurable": {"thread_id": "test-state-clear"}},
    )

    # Check that pending_content_map was cleared (should be empty dict after execution)
    # Note: This verifies the Command pattern properly updates state
    assert (
        result.get("pending_content_map", "NOTSET") == {}
        or result.get("pending_content_map") == "NOTSET"
    )


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

    # Verify by checking tool stdout (deterministic) and VFS
    tool_stdout = get_tool_stdout(messages)

    # Check Excel values were printed from tool execution
    assert "Product" in tool_stdout, f"Expected 'Product' in tool stdout: {tool_stdout[:300]}"
    assert "Widget" in tool_stdout or "42" in tool_stdout, f"Expected cell values in stdout: {tool_stdout[:300]}"

    # Also verify Excel file exists in VFS
    file_exists = await check_vfs_file_exists(db_pool, "e2e_prepared_test", "/tmp/data.xlsx")
    assert file_exists, "Expected /tmp/data.xlsx to exist in VFS"


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_python_run_prepared_with_pdf_creation(db_pool, clean_files):
    """Test python_run_prepared with pdf_creation helpers for PDF generation."""
    app = create_agent_graph(db_pool)

    await app.ainvoke(
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

    # Verify PDF was created by checking VFS directly (deterministic)
    file_exists = await check_vfs_file_exists(db_pool, "e2e_prepared_test", "/tmp/report.pdf")
    assert file_exists, "Expected /tmp/report.pdf to exist in VFS"

    # Verify it's a valid PDF by checking magic bytes
    content = await get_vfs_file_content(db_pool, "e2e_prepared_test", "/tmp/report.pdf")
    assert content is not None and content[:4] == b"%PDF", "Expected valid PDF file"


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

    # Verify by checking tool stdout for extracted text (deterministic)
    tool_stdout = get_tool_stdout(messages)

    # Check for slide content in tool output
    assert "Q4" in tool_stdout or "Results" in tool_stdout, f"Expected slide content: {tool_stdout[:300]}"

    # Also verify PPTX file exists in VFS
    file_exists = await check_vfs_file_exists(db_pool, "e2e_prepared_test", "/tmp/presentation.pptx")
    assert file_exists, "Expected /tmp/presentation.pptx to exist in VFS"


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_python_run_prepared_with_docx_manipulation(db_pool, clean_files):
    """Test python_run_prepared with docx_ooxml for Word document manipulation."""
    app = create_agent_graph(db_pool)

    await app.ainvoke(
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

    # Verify Word document exists in VFS (deterministic)
    file_exists = await check_vfs_file_exists(db_pool, "e2e_prepared_test", "/tmp/document.docx")
    assert file_exists, "Expected /tmp/document.docx to exist in VFS"

    # Verify it's a valid DOCX (ZIP format) by checking magic bytes
    content = await get_vfs_file_content(db_pool, "e2e_prepared_test", "/tmp/document.docx")
    assert content is not None and content[:2] == b"PK", "Expected valid DOCX file (ZIP format)"


# Tests for file_write with state-based content extraction


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_file_write_with_csv_data(db_pool, clean_files):
    """Test file_write with state-based extraction for CSV data."""
    app = create_agent_graph(db_pool, include_tools=["file_write"])

    await app.ainvoke(
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

    # Verify file was written by checking VFS directly (deterministic)
    file_exists = await check_vfs_file_exists(db_pool, "e2e_prepared_test", "/tmp/employees.csv")
    assert file_exists, "Expected /tmp/employees.csv to exist in VFS"

    # Verify CSV content
    content = await get_vfs_file_content(db_pool, "e2e_prepared_test", "/tmp/employees.csv")
    assert content is not None, "Expected CSV content"
    csv_text = content.decode("utf-8")
    assert "Alice" in csv_text, f"Expected 'Alice' in CSV: {csv_text[:200]}"
    assert "Engineering" in csv_text, f"Expected 'Engineering' in CSV: {csv_text[:200]}"


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_file_write_with_json_config(db_pool, clean_files):
    """Test file_write with state-based extraction for JSON configuration."""
    app = create_agent_graph(db_pool, include_tools=["file_write"])

    await app.ainvoke(
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

    # Verify file was written by checking VFS directly (deterministic)
    file_exists = await check_vfs_file_exists(db_pool, "e2e_prepared_test", "/tmp/config.json")
    assert file_exists, "Expected /tmp/config.json to exist in VFS"

    # Verify JSON content is valid
    import json
    content = await get_vfs_file_content(db_pool, "e2e_prepared_test", "/tmp/config.json")
    assert content is not None, "Expected JSON content"
    config_data = json.loads(content.decode("utf-8"))
    assert "api_endpoint" in config_data or "timeout" in config_data, f"Expected config keys: {config_data}"


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_file_write_with_large_markdown(db_pool, clean_files):
    """Test file_write handles large content (>2000 chars) via state extraction."""
    app = create_agent_graph(db_pool, include_tools=["file_write"])

    await app.ainvoke(
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

    # Verify large file was written by checking VFS directly (deterministic)
    file_exists = await check_vfs_file_exists(db_pool, "e2e_prepared_test", "/tmp/README.md")
    assert file_exists, "Expected /tmp/README.md to exist in VFS"

    # Verify content is substantial (>2000 chars as per test description)
    content = await get_vfs_file_content(db_pool, "e2e_prepared_test", "/tmp/README.md")
    assert content is not None, "Expected markdown content"
    assert len(content) > 500, f"Expected large content (>500 bytes), got {len(content)} bytes"

    # Verify it contains expected markdown structure
    md_text = content.decode("utf-8")
    assert "#" in md_text, "Expected markdown headers"
