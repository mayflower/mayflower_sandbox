"""
Complete end-to-end example: LangGraph agent with Mayflower Sandbox tools.

This example demonstrates how to create a LangGraph ReAct agent that can:
- Execute Python code in a sandbox
- Read and write files
- List and delete files
- All with persistent PostgreSQL storage

Run this example:
    python examples/langgraph_agent_example.py
"""

import asyncio
import asyncpg
import os
from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

from mayflower_sandbox.tools import create_sandbox_tools


async def main():
    """Run the example agent."""
    # 1. Setup PostgreSQL connection
    db_pool = await asyncpg.create_pool(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        database=os.getenv("POSTGRES_DB", "mayflower_test"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
    )

    # 2. Create sandbox tools for a specific user/thread
    thread_id = "demo_user_123"
    tools = create_sandbox_tools(db_pool, thread_id=thread_id)

    print(f"Created {len(tools)} tools for thread: {thread_id}")
    for tool in tools:
        print(f"  - {tool.name}: {tool.description.split(chr(10))[0]}")

    # 3. Create LangGraph ReAct agent
    llm = ChatAnthropic(model="claude-3-5-sonnet-20241022", temperature=0)
    agent = create_react_agent(llm, tools)

    # 4. Example interactions
    examples = [
        {
            "name": "Data Analysis",
            "query": """Create a CSV file with sample sales data and analyze it:
1. Write a file /tmp/sales.csv with columns: date, product, quantity, price
2. Add 5 rows of sample data
3. Read the file back and calculate total revenue
4. Save the analysis to /tmp/analysis.txt""",
        },
        {
            "name": "List Files",
            "query": "List all files in the /tmp directory",
        },
        {
            "name": "File Operations",
            "query": "Read the analysis file and then create a summary in /tmp/summary.txt",
        },
        {
            "name": "Cleanup",
            "query": "Delete all files in /tmp/",
        },
    ]

    for i, example in enumerate(examples, 1):
        print(f"\n{'=' * 70}")
        print(f"Example {i}: {example['name']}")
        print(f"{'=' * 70}")
        print(f"Query: {example['query']}\n")

        # Run the agent
        result = agent.invoke({"messages": [("user", example["query"])]})

        # Print response
        last_message = result["messages"][-1]
        print(f"Response: {last_message.content}\n")

        # Optional: pause between examples
        if i < len(examples):
            input("Press Enter to continue to next example...")

    # 5. Cleanup
    await db_pool.close()
    print("\nDemo complete!")


if __name__ == "__main__":
    asyncio.run(main())
