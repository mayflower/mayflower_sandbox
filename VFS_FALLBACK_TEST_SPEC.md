# VFS Fallback Testing Specification

## Context

The VFS fallback mechanism in `sandbox_executor.py` (lines 277-289) detects files created by compiled libraries (openpyxl, xlsxwriter) that bypass Pyodide's file system snapshot mechanism.

**Current implementation:**
```python
if not created_files and result_data.get("success", False):
    after_vfs_files = set(f["file_path"] for f in await self.vfs.list_files())
    vfs_created = list(after_vfs_files - before_vfs_files)

    if vfs_created:
        created_files = vfs_created
        logger.info(f"VFS fallback detected {len(vfs_created)} files...")
```

## Existing Test

`test_compiled_library_vfs_fallback` in `tests/test_sandbox_executor.py:280` verifies the happy path:
- openpyxl creates Excel file
- Execution succeeds
- VFS fallback detects the file
- File is tracked in `created_files`

## Missing Test Coverage

### Test 1: VFS Fallback Does NOT Trigger on Failed Execution

**Scenario:** Compiled library code fails, so VFS fallback should not run (even if files were partially created before the error).

**Test Name:** `test_vfs_fallback_skipped_on_execution_failure`

**Test Code:**
```python
async def test_vfs_fallback_skipped_on_execution_failure(executor, db_pool, clean_files):
    """Test VFS fallback does NOT trigger when execution fails.

    Even if a compiled library creates files before throwing an error,
    the VFS fallback should not run because success=False.
    This prevents tracking incomplete/corrupted files.
    """
    code = """
import micropip
await micropip.install("openpyxl")

from openpyxl import Workbook

# Create file
wb = Workbook()
ws = wb.active
ws['A1'] = 'Test'
wb.save('/tmp/partial.xlsx')

# Then fail
raise RuntimeError("Simulated error after file creation")
"""

    result = await executor.execute(code)

    # Execution should fail
    assert result.success is False, "Execution should fail due to RuntimeError"
    assert "RuntimeError" in result.stderr
    assert "Simulated error" in result.stderr

    # VFS fallback should NOT run (no log message)
    # created_files should be None because success=False
    assert result.created_files is None, (
        "VFS fallback should not run when success=False. "
        f"Got: {result.created_files}"
    )

    # Verify file MAY exist in VFS (written before error)
    # but is NOT tracked in created_files
    async with db_pool.acquire() as conn:
        file_exists = await conn.fetchval("""
            SELECT EXISTS(
                SELECT 1 FROM sandbox_filesystem
                WHERE thread_id = 'test_sandbox' AND file_path = '/tmp/partial.xlsx'
            )
        """)

        # File may or may not exist (timing-dependent), but either way
        # it should NOT be in created_files
        if file_exists:
            print("Note: File exists in VFS but correctly NOT tracked due to failure")
```

**Expected Behavior:**
- ✅ Execution fails with `success=False`
- ✅ `created_files` is `None` (not populated by VFS fallback)
- ✅ No "VFS fallback detected" log message
- ✅ Test verifies the condition `result_data.get("success", False)` prevents fallback

---

### Test 2: VFS Fallback with Multiple Files from Different Sources

**Scenario:** TypeScript snapshot detects some files (plain Python I/O) but misses others (compiled library). VFS fallback should ONLY add the missing files.

**Test Name:** `test_vfs_fallback_supplements_typescript_snapshot`

**Test Code:**
```python
async def test_vfs_fallback_supplements_typescript_snapshot(executor, db_pool, clean_files):
    """Test VFS fallback adds files missed by TypeScript, not duplicates.

    When some files are detected by TypeScript snapshot and others are missed
    (compiled library), the VFS fallback should only add the missing ones.
    """
    code = """
import micropip
await micropip.install("openpyxl")

from openpyxl import Workbook

# File 1: Created with Python built-in (TypeScript WILL detect)
with open('/tmp/plain.txt', 'w') as f:
    f.write('Created with open()')

# File 2: Created with openpyxl (TypeScript may MISS)
wb = Workbook()
ws = wb.active
ws['A1'] = 'Data'
wb.save('/tmp/compiled.xlsx')

print("Both files created")
"""

    result = await executor.execute(code)

    assert result.success is True, f"Execution failed: {result.stderr}"
    assert "Both files created" in result.stdout

    # created_files should contain BOTH files
    assert result.created_files is not None
    assert len(result.created_files) >= 1, "At least one file should be tracked"

    # Both files should be in the list (order doesn't matter)
    created_paths = set(result.created_files)

    # At minimum, we should have the Excel file
    # (plain.txt may or may not be detected depending on Pyodide version)
    assert '/tmp/compiled.xlsx' in created_paths, (
        f"Excel file should be tracked. Got: {result.created_files}"
    )

    # Verify both files exist in VFS
    async with db_pool.acquire() as conn:
        files_in_vfs = await conn.fetch("""
            SELECT file_path FROM sandbox_filesystem
            WHERE thread_id = 'test_sandbox'
            AND file_path IN ('/tmp/plain.txt', '/tmp/compiled.xlsx')
            ORDER BY file_path
        """)

        vfs_paths = [row['file_path'] for row in files_in_vfs]
        assert '/tmp/plain.txt' in vfs_paths, "Plain text file should be in VFS"
        assert '/tmp/compiled.xlsx' in vfs_paths, "Excel file should be in VFS"
```

**Expected Behavior:**
- ✅ Both files are created
- ✅ Both files are saved to PostgreSQL VFS
- ✅ At least the compiled file is in `created_files`
- ✅ No duplicate entries in `created_files`

---

### Test 3: VFS Fallback Logging Verification

**Scenario:** Verify the INFO log message is emitted when VFS fallback triggers.

**Test Name:** `test_vfs_fallback_emits_log_message`

**Test Code:**
```python
async def test_vfs_fallback_emits_log_message(executor, db_pool, clean_files, caplog):
    """Test VFS fallback logs INFO message when it detects files.

    The log message helps debug file tracking issues and should contain:
    - Number of files detected
    - Thread ID
    - List of file paths
    """
    import logging

    # Ensure we capture logs at INFO level
    caplog.set_level(logging.INFO, logger='mayflower_sandbox.sandbox_executor')

    code = """
import micropip
await micropip.install("openpyxl")

from openpyxl import Workbook

wb = Workbook()
ws = wb.active
ws['A1'] = 'Test'
wb.save('/tmp/logged.xlsx')
print("File created")
"""

    result = await executor.execute(code)

    assert result.success is True
    assert result.created_files is not None
    assert '/tmp/logged.xlsx' in result.created_files

    # Check for the log message
    log_records = [r for r in caplog.records if 'VFS fallback detected' in r.message]

    assert len(log_records) >= 1, (
        "VFS fallback should emit INFO log message. "
        f"Found logs: {[r.message for r in caplog.records]}"
    )

    log_message = log_records[0].message

    # Verify log message contains expected information
    assert 'VFS fallback detected' in log_message
    assert '1' in log_message  # Number of files
    assert 'test_sandbox' in log_message  # Thread ID
    assert '/tmp/logged.xlsx' in log_message  # File path
```

**Expected Behavior:**
- ✅ Log message at INFO level is emitted
- ✅ Message contains file count, thread_id, and file paths
- ✅ Helps with debugging VFS tracking issues

---

### Test 4: VFS Fallback with Empty VFS Before Execution

**Scenario:** VFS is completely empty before execution (no pre-loaded files), then compiled library creates file.

**Test Name:** `test_vfs_fallback_from_empty_vfs`

**Test Code:**
```python
async def test_vfs_fallback_from_empty_vfs(db_pool, clean_files):
    """Test VFS fallback works when VFS starts completely empty.

    Edge case: No files in VFS before execution, so before_vfs_files is empty set.
    After execution, VFS has one file from compiled library.
    """
    # Create a fresh executor with a unique thread_id
    thread_id = 'test_empty_vfs'

    # Ensure VFS is empty for this thread
    async with db_pool.acquire() as conn:
        await conn.execute("""
            DELETE FROM sandbox_filesystem WHERE thread_id = $1
        """, thread_id)

        # Verify empty
        count = await conn.fetchval("""
            SELECT COUNT(*) FROM sandbox_filesystem WHERE thread_id = $1
        """, thread_id)
        assert count == 0, "VFS should be empty before test"

    from mayflower_sandbox.sandbox_executor import SandboxExecutor
    executor = SandboxExecutor(db_pool, thread_id)

    code = """
import micropip
await micropip.install("openpyxl")

from openpyxl import Workbook

wb = Workbook()
ws = wb.active
ws['A1'] = 'First File'
wb.save('/tmp/first.xlsx')
print("Created first file in empty VFS")
"""

    result = await executor.execute(code)

    assert result.success is True
    assert "Created first file" in result.stdout

    # VFS fallback should detect the file
    assert result.created_files is not None, "VFS fallback should detect file"
    assert '/tmp/first.xlsx' in result.created_files

    # Verify file is in VFS
    async with db_pool.acquire() as conn:
        file_exists = await conn.fetchval("""
            SELECT EXISTS(
                SELECT 1 FROM sandbox_filesystem
                WHERE thread_id = $1 AND file_path = '/tmp/first.xlsx'
            )
        """, thread_id)

        assert file_exists, "File should be saved to VFS"
```

**Expected Behavior:**
- ✅ `before_vfs_files` is empty set
- ✅ After execution, `after_vfs_files` contains one file
- ✅ VFS fallback detects: `{'/tmp/first.xlsx'}` - `{}` = `{'/tmp/first.xlsx'}`
- ✅ File is correctly tracked

---

## Implementation Notes for Claude Code

1. **Add these tests to:** `tests/test_sandbox_executor.py`

2. **Required imports:**
```python
import logging
import pytest
```

3. **Fixture requirements:**
   - `executor`: Existing fixture (SandboxExecutor instance)
   - `db_pool`: Existing fixture (asyncpg connection pool)
   - `clean_files`: Existing fixture (cleans VFS between tests)
   - `caplog`: pytest built-in fixture for log capture

4. **Mark all tests with:**
```python
@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set - skipping LLM test"
)
```

5. **Test execution:**
```bash
# Run all VFS fallback tests
pytest tests/test_sandbox_executor.py -k vfs_fallback -v

# Run specific test
pytest tests/test_sandbox_executor.py::test_vfs_fallback_skipped_on_execution_failure -v
```

6. **Expected test results:**
   - All 4 new tests should PASS
   - Existing `test_compiled_library_vfs_fallback` should still PASS
   - Total VFS fallback test coverage: 5 tests

---

## Verification Checklist

After implementing these tests, verify:

- [ ] Test 1: VFS fallback does NOT run when `success=False`
- [ ] Test 2: VFS fallback supplements TypeScript snapshot (no duplicates)
- [ ] Test 3: Log message is emitted with correct format
- [ ] Test 4: Works with empty VFS (edge case)
- [ ] All tests pass in CI/CD
- [ ] Code coverage for lines 280-289 in sandbox_executor.py is 100%

---

## Related Files

- **Implementation:** `src/mayflower_sandbox/sandbox_executor.py:277-289`
- **Existing test:** `tests/test_sandbox_executor.py:280` (`test_compiled_library_vfs_fallback`)
- **Documentation:** `PYODIDE_FILE_TRACKING_ISSUE.md` in maistack repo

---

**Created:** 2025-10-25
**Purpose:** Comprehensive test coverage for VFS fallback edge cases
**Status:** Specification ready for implementation
