"""
Example: Using MayflowerSandboxBackend directly.

This example demonstrates the SandboxBackendProtocol interface:
- Execute Python code in a Pyodide sandbox
- Execute shell commands via BusyBox WASM
- Read, write, and list files
- All with persistent PostgreSQL storage

Run this example:
    python examples/langgraph_agent_example.py
"""

import asyncio
import os

import asyncpg

from mayflower_sandbox import MayflowerSandboxBackend


async def main():
    """Run the example."""
    # 1. Setup PostgreSQL connection
    db_pool = await asyncpg.create_pool(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        database=os.getenv("POSTGRES_DB", "mayflower_test"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
    )

    # 2. Create sandbox backend for a specific user/thread
    thread_id = "demo_user_123"
    backend = MayflowerSandboxBackend(
        db_pool,
        thread_id=thread_id,
        allow_net=False,
        stateful=True,
        timeout_seconds=60.0,
    )

    print(f"Created sandbox backend for thread: {thread_id}")

    # 3. Write a Python script via the backend
    print("\n--- Writing a script ---")
    await backend.awrite(
        "/tmp/analysis.py",
        """\
import csv
import io

data = "name,value\\nfoo,42\\nbar,17\\nbaz,99"
reader = csv.DictReader(io.StringIO(data))
rows = list(reader)
total = sum(int(r["value"]) for r in rows)
print(f"Total: {total}")
print(f"Rows: {len(rows)}")
""",
    )
    print("Wrote /tmp/analysis.py")

    # 4. Execute the script
    print("\n--- Executing Python script ---")
    result = await backend.aexecute("python /tmp/analysis.py")
    print(f"Exit code: {result.exit_code}")
    print(f"Output: {result.output}")

    # 5. Run a shell command
    print("\n--- Running shell command ---")
    result = await backend.aexecute("echo 'hello world' | grep hello")
    print(f"Output: {result.output}")

    # 6. List files
    print("\n--- Listing files ---")
    files = await backend.als_info("/tmp")
    for f in files:
        print(f"  {f['path']} ({f['size']} bytes)")

    # 7. Read a file back
    print("\n--- Reading file ---")
    content = await backend.aread("/tmp/analysis.py")
    print(content[:200])

    # 8. Cleanup
    await db_pool.close()
    print("\nDemo complete!")


if __name__ == "__main__":
    asyncio.run(main())
