#!/bin/bash
# Quality assurance checks for Mayflower Sandbox

set -e

echo "========================================"
echo "  Mayflower Sandbox - Quality Checks"
echo "========================================"
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Track overall status
FAILED=0

# Function to run check
run_check() {
    local name=$1
    local cmd=$2

    echo -e "${YELLOW}Running $name...${NC}"
    if eval $cmd; then
        echo -e "${GREEN}✓ $name passed${NC}"
        echo ""
    else
        echo -e "${RED}✗ $name failed${NC}"
        echo ""
        FAILED=1
    fi
}

# Check 1: Ruff linting
run_check "Ruff linting" "ruff check src/"

# Check 2: Mypy type checking
run_check "Mypy type checking" "mypy src/mayflower_sandbox"

# Check 3: Ruff formatting check
run_check "Ruff formatting" "ruff format --check src/"

# Check 4: Run tests
run_check "Pytest" "pytest tests/test_executor.py tests/test_filesystem.py tests/test_manager.py -v --tb=short"

# Check 5: Test helper modules
run_check "Helper tests" "pytest tests/test_pptx_helpers.py tests/test_xlsx_helpers.py tests/test_word_helpers.py tests/test_pdf_helpers.py -v --tb=short"

echo "========================================"
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All quality checks passed!${NC}"
    echo "========================================"
    exit 0
else
    echo -e "${RED}✗ Some quality checks failed${NC}"
    echo "========================================"
    exit 1
fi
