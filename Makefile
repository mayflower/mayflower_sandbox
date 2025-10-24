.PHONY: help install test lint format typecheck quality clean db-up db-down db-setup

# Use commands from current environment
PYTHON := python3
PYTEST := pytest
RUFF := ruff
MYPY := mypy

help:
	@echo "Mayflower Sandbox - Development Commands"
	@echo ""
	@echo "Database:"
	@echo "  make db-setup   - Setup PostgreSQL in Docker and run migrations"
	@echo "  make db-up      - Start PostgreSQL container"
	@echo "  make db-down    - Stop PostgreSQL container"
	@echo ""
	@echo "Development:"
	@echo "  make install    - Install package and dependencies"
	@echo "  make test       - Run all tests"
	@echo "  make lint       - Run ruff linter"
	@echo "  make format     - Format code with ruff"
	@echo "  make typecheck  - Run mypy type checker"
	@echo "  make quality    - Run all quality checks"
	@echo "  make clean      - Clean build artifacts"

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTEST) -v

test-fast:
	$(PYTEST) tests/test_executor.py tests/test_filesystem.py tests/test_manager.py -v

test-helpers:
	$(PYTEST) tests/test_*_helpers.py -v

lint:
	$(RUFF) check src/

lint-fix:
	$(RUFF) check src/ --fix

format:
	$(RUFF) format src/ tests/

format-check:
	$(RUFF) format --check src/ tests/

typecheck:
	$(MYPY) src/mayflower_sandbox

quality:
	@echo "Running all quality checks..."
	@$(RUFF) check src/
	@$(RUFF) format --check src/
	@$(MYPY) src/mayflower_sandbox
	@echo "✓ All quality checks passed!"

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
	find . -type f -name '*.pyo' -delete
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +

# Database commands
db-setup:
	@bash scripts/setup-test-db.sh

db-up:
	@docker compose up -d postgres
	@echo "✓ PostgreSQL started on localhost:5432"

db-down:
	@docker compose down
	@echo "✓ PostgreSQL stopped"
