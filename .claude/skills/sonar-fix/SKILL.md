---
name: sonar-fix
description: Find and fix SonarQube issues by severity
---

# SonarQube Issue Fixer

Systematically identify and fix SonarQube issues, prioritized by severity.

## Project Key

The SonarQube project key for this repository is: `mayflower-sandbox`

## Steps

1. **Fetch All Open Issues**:
   Use `mcp__sonarqube__search_sonar_issues_in_projects` with:
   - projects: `["mayflower-sandbox"]`
   - Paginate if needed (ps: 100)

2. **Group and Prioritize**:
   Sort issues by severity: BLOCKER → CRITICAL → HIGH → MEDIUM → LOW → INFO

3. **For Each Issue** (starting with highest severity):

   a. **Show Issue Details**:
      - File path and line number
      - Issue message
      - Severity and type

   b. **Get Rule Details**:
      Use `mcp__sonarqube__show_rule` with the rule key (e.g., `python:S1481`)
      This provides:
      - Why this is a problem
      - How to fix it
      - Code examples

   c. **Read the Affected Code**:
      Read the file and show context around the issue

   d. **Apply the Fix**:
      Edit the file to resolve the issue following the rule guidance

   e. **Verify Fix**:
      Run relevant tests or linters to ensure the fix doesn't break anything

4. **Run Tests After Fixes**:
   ```bash
   .venv/bin/pytest tests/ -v --tb=short
   ```

5. **Run Linters**:
   ```bash
   .venv/bin/ruff check src/
   ```

## Interactive Mode

After fixing each issue (or batch of related issues), pause and report:
- What was fixed
- What issues remain
- Ask if user wants to continue or commit current fixes

## Commit Guidance

When the user wants to commit:
- Stage only the files that were fixed
- Use a descriptive commit message like: "Fix SonarQube issues: [brief description]"
- Do NOT push automatically - let user decide

## Handling False Positives

If an issue appears to be a false positive:
1. Explain why it might be a false positive
2. Offer options:
   - Add a `# noqa` or inline suppression comment
   - Use `mcp__sonarqube__change_sonar_issue_status` to mark as `falsepositive` or `accept`
   - Fix it anyway if the suggested change is harmless
