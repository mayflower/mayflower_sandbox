# Examples

Complete working examples for common use cases.

## Basic Data Analysis

Create CSV file, read it, and calculate statistics:

```python
import asyncpg
from mayflower_sandbox import SandboxExecutor

# Setup
db_pool = await asyncpg.create_pool(
    host="localhost",
    database="mayflower_test",
    user="postgres",
    password="postgres"
)

executor = SandboxExecutor(db_pool, thread_id="analyst_1", allow_net=True)

# Create and analyze data
result = await executor.execute("""
# Create dataset
data = '''name,age,score
Alice,25,95
Bob,30,87
Charlie,22,92
Diana,28,88'''

with open('/tmp/students.csv', 'w') as f:
    f.write(data)

# Analyze
import csv
scores = []
with open('/tmp/students.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        scores.append(float(row['score']))

average = sum(scores) / len(scores)
print(f'Average score: {average:.2f}')
print(f'Highest: {max(scores)}')
print(f'Lowest: {min(scores)}')
""")

print(result.stdout)
# Output:
# Average score: 90.50
# Highest: 95.0
# Lowest: 87.0
```

## LangGraph Agent with Document Processing

Agent that processes Excel files:

```python
from mayflower_sandbox.tools import create_sandbox_tools
from langgraph.prebuilt import create_react_agent
from langchain_anthropic import ChatAnthropic

# Create tools
tools = create_sandbox_tools(db_pool, thread_id="user_123", allow_net=True)

# Create agent
llm = ChatAnthropic(model="claude-sonnet-4.5")
agent = create_react_agent(llm, tools)

# Use agent
result = await agent.ainvoke({
    "messages": [(
        "user",
        """Create an Excel file at /tmp/sales.xlsx with these columns:
        Product, Q1, Q2, Q3, Q4, Total

        Add 3 products with sample quarterly sales data.
        The Total column should have SUM formulas.
        Then read the file and tell me the total sales."""
    )]
})

print(result["messages"][-1].content)
```

## Processing Word Documents

Extract and analyze Word document content:

```python
result = await executor.execute("""
import micropip
await micropip.install('openpyxl')  # May be needed for some helpers

from document.docx_ooxml import (
    docx_extract_text,
    docx_extract_paragraphs,
    docx_read_tables
)

# Read document from VFS
docx_bytes = open('/tmp/report.docx', 'rb').read()

# Extract all text
full_text = docx_extract_text(docx_bytes)
print(f"Document has {len(full_text)} characters")

# Extract paragraphs
paragraphs = docx_extract_paragraphs(docx_bytes)
print(f"Document has {len(paragraphs)} paragraphs")

# Extract tables
tables = docx_read_tables(docx_bytes)
print(f"Document has {len(tables)} tables")

if tables:
    print("\\nFirst table:")
    for row in tables[0]:
        print(row)
""")
```

## Merging PDF Files

Combine multiple PDFs:

```python
result = await executor.execute("""
import micropip
await micropip.install('pypdf')

from document.pdf_manipulation import pdf_merge, pdf_num_pages

# Read PDFs from VFS
pdf1 = open('/tmp/part1.pdf', 'rb').read()
pdf2 = open('/tmp/part2.pdf', 'rb').read()
pdf3 = open('/tmp/part3.pdf', 'rb').read()

# Merge
merged = pdf_merge([pdf1, pdf2, pdf3])

# Save to VFS
with open('/tmp/merged.pdf', 'wb') as f:
    f.write(merged)

# Verify
pages = pdf_num_pages(merged)
print(f'Merged PDF has {pages} pages')
""")
```

## Stateful Data Processing Pipeline

Multi-step pipeline with persistent state:

```python
from mayflower_sandbox.session import StatefulExecutor

executor = StatefulExecutor(db_pool, thread_id="pipeline_1", allow_net=True)

# Step 1: Load and preprocess data
await executor.execute("""
import csv

# Load raw data
raw_data = []
with open('/tmp/raw_sales.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        raw_data.append(row)

# Preprocess
def clean_amount(s):
    return float(s.replace('$', '').replace(',', ''))

processed = []
for row in raw_data:
    processed.append({
        'product': row['product'].strip().title(),
        'amount': clean_amount(row['amount']),
        'date': row['date']
    })

print(f"Loaded {len(processed)} records")
""")

# Step 2: Analyze (state persists!)
result = await executor.execute("""
# processed variable is still available!
from collections import defaultdict

by_product = defaultdict(float)
for record in processed:
    by_product[record['product']] += record['amount']

# Sort by total sales
top_products = sorted(by_product.items(), key=lambda x: x[1], reverse=True)[:5]

print("Top 5 Products:")
for product, total in top_products:
    print(f"  {product}: ${total:,.2f}")

# Store for later use
results = {
    'top_products': top_products,
    'total_sales': sum(by_product.values())
}
""")

# Step 3: Generate report
await executor.execute("""
# results variable still available from previous step!
report = f'''
Sales Analysis Report
=====================

Total Sales: ${results['total_sales']:,.2f}

Top 5 Products:
'''

for i, (product, amount) in enumerate(results['top_products'], 1):
    report += f"{i}. {product}: ${amount:,.2f}\\n"

with open('/tmp/report.txt', 'w') as f:
    f.write(report)

print("Report generated at /tmp/report.txt")
""")
```

## Creating Excel Charts with openpyxl

Generate Excel file with charts:

```python
result = await executor.execute("""
import micropip
await micropip.install('openpyxl')

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference

# Create workbook
wb = Workbook()
ws = wb.active
ws.title = "Sales Data"

# Add headers
ws['A1'] = 'Month'
ws['B1'] = 'Revenue'

# Add data
data = [
    ['Jan', 15000],
    ['Feb', 18000],
    ['Mar', 22000],
    ['Apr', 19000],
    ['May', 25000],
    ['Jun', 28000]
]

for i, (month, revenue) in enumerate(data, start=2):
    ws[f'A{i}'] = month
    ws[f'B{i}'] = revenue

# Create chart
chart = BarChart()
chart.title = "Monthly Revenue"
chart.x_axis.title = "Month"
chart.y_axis.title = "Revenue ($)"

# Set data range
data_ref = Reference(ws, min_col=2, min_row=1, max_row=len(data) + 1)
categories = Reference(ws, min_col=1, min_row=2, max_row=len(data) + 1)

chart.add_data(data_ref, titles_from_data=True)
chart.set_categories(categories)

ws.add_chart(chart, "D2")

# Save
wb.save('/tmp/revenue_chart.xlsx')
print('Created Excel file with chart at /tmp/revenue_chart.xlsx')
""")
```

## File Server with Downloads

Serve files via HTTP:

```python
from mayflower_sandbox.server import FileServer
import asyncio

async def setup_and_serve():
    # Setup database
    db_pool = await asyncpg.create_pool(
        host="localhost",
        database="mayflower_test",
        user="postgres",
        password="postgres"
    )

    # Create some files
    executor = SandboxExecutor(db_pool, thread_id="public", allow_net=False)
    await executor.execute("""
with open('/tmp/hello.txt', 'w') as f:
    f.write('Hello, World!')

with open('/tmp/data.csv', 'w') as f:
    f.write('name,value\\nAlice,100\\nBob,200')
""")

    # Start server
    server = FileServer(db_pool, host="0.0.0.0", port=8080)
    print("Server running on http://localhost:8080")
    print("Try: curl http://localhost:8080/files/public/tmp/hello.txt")
    server.run()

# Run
asyncio.run(setup_and_serve())
```

## Cleanup Job with Custom Schedule

Automatic cleanup every 30 minutes:

```python
from mayflower_sandbox.cleanup import CleanupJob
import asyncio

async def run_cleanup():
    db_pool = await asyncpg.create_pool(
        host="localhost",
        database="mayflower_test",
        user="postgres",
        password="postgres"
    )

    # Create cleanup job (runs every 30 minutes)
    cleanup = CleanupJob(db_pool, interval_seconds=1800)

    # Start periodic cleanup
    cleanup.start()
    print("Cleanup job started (runs every 30 minutes)")

    # Keep running
    try:
        while True:
            await asyncio.sleep(60)
    except KeyboardInterrupt:
        cleanup.stop()
        print("Cleanup job stopped")

asyncio.run(run_cleanup())
```

## Processing Multiple Documents

Batch process multiple Word documents:

```python
result = await executor.execute("""
from document.docx_ooxml import docx_extract_text
import os

# Get all Word documents
doc_files = [f for f in os.listdir('/tmp/documents') if f.endswith('.docx')]

results = {}
for filename in doc_files:
    path = f'/tmp/documents/{filename}'
    docx_bytes = open(path, 'rb').read()
    text = docx_extract_text(docx_bytes)

    # Analyze
    word_count = len(text.split())
    char_count = len(text)

    results[filename] = {
        'words': word_count,
        'characters': char_count
    }

# Summary
print("Document Analysis:")
print("-" * 50)
for filename, stats in results.items():
    print(f"{filename}:")
    print(f"  Words: {stats['words']}")
    print(f"  Characters: {stats['characters']}")
    print()

total_words = sum(r['words'] for r in results.values())
print(f"Total words across all documents: {total_words}")
""")
```

## Related Documentation

- [Quick Start](../getting-started/quickstart.md) - Get started quickly
- [Tools Reference](tools.md) - The 12 LangChain tools
- [Helpers Reference](helpers.md) - Document processing helpers
- [Advanced Features](../advanced/stateful-execution.md) - More advanced use cases
