# Installation

## Prerequisites

- Python 3.12+
- PostgreSQL 14+
- Deno (for Pyodide execution)

## Install Deno

```bash
curl -fsSL https://deno.land/x/install/install.sh | sh

# Add to PATH (add to ~/.bashrc or ~/.zshrc)
export DENO_INSTALL="$HOME/.deno"
export PATH="$DENO_INSTALL/bin:$PATH"
```

## Install Package

```bash
# Clone repository
git clone <repo-url>
cd mayflower-sandbox

# Install package
pip install -e .

# Install development dependencies (optional)
pip install -e ".[dev]"
```

## Database Setup

### Create Database

```bash
createdb mayflower_test
```

### Apply Schema

```bash
psql -d mayflower_test -f migrations/001_sandbox_schema.sql
```

This creates three tables:
- **sandbox_sessions** - Session tracking
- **sandbox_filesystem** - File storage (20MB limit per file)
- **sandbox_session_bytes** - Stateful execution support

## Environment Variables

Create a `.env` file or export environment variables:

```bash
# PostgreSQL connection
export POSTGRES_HOST=localhost
export POSTGRES_DB=mayflower_test
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=postgres
export POSTGRES_PORT=5432

# Optional: For LangGraph examples
export ANTHROPIC_API_KEY=your_key_here
export OPENAI_API_KEY=your_key_here
```

## Verify Installation

```bash
# Run tests
pytest tests/test_executor.py -v

# Should see: 12/12 tests passing ✅
```

## Next Steps

- [Quick Start Guide](quickstart.md) - Get started with basic usage
- [Tools Reference](tools.md) - Learn about the 5 LangChain tools
- [Helpers Reference](helpers.md) - Document processing helpers
