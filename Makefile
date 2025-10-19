.PHONY: help install test lint format typecheck quality clean

help:
	@echo "Mayflower Sandbox - Development Commands"
	@echo ""
	@echo "Available commands:"
	@echo "  make install    - Install package and dependencies"
	@echo "  make test       - Run all tests"
	@echo "  make lint       - Run ruff linter"
	@echo "  make format     - Format code with ruff"
	@echo "  make typecheck  - Run mypy type checker"
	@echo "  make quality    - Run all quality checks"
	@echo "  make clean      - Clean build artifacts"

install:
	pip install -e ".[dev]"

test:
	pytest -v

test-fast:
	pytest tests/test_executor.py tests/test_filesystem.py tests/test_manager.py -v

test-helpers:
	pytest tests/test_*_helpers.py -v

lint:
	ruff check src/

lint-fix:
	ruff check src/ --fix

format:
	ruff format src/ tests/

format-check:
	ruff format --check src/ tests/

typecheck:
	mypy src/mayflower_sandbox

quality:
	@echo "Running all quality checks..."
	@ruff check src/
	@ruff format --check src/
	@mypy src/mayflower_sandbox
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
