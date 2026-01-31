---
name: pr-quality
description: Create a pull request with quality gate status and metrics
---

# Quality-Checked Pull Request Creation

Create a pull request that includes SonarQube quality metrics and CI status.

## Prerequisites Check

Before creating the PR, verify:
1. Branch is pushed to origin
2. CI workflow has completed successfully
3. SonarQube quality gate is passing

## Steps

1. **Check Branch Status**:
   ```bash
   git status
   git log origin/main..HEAD --oneline
   ```

2. **Ensure Branch is Pushed**:
   If not pushed or behind:
   ```bash
   git push origin HEAD
   ```

3. **Check CI Status**:
   ```bash
   gh run list --branch $(git branch --show-current) -L 1 --json status,conclusion,workflowName
   ```
   - If running: Wait with `gh run watch`
   - If failed: Warn user and ask if they want to proceed anyway

4. **Get SonarQube Quality Gate**:
   Use `mcp__sonarqube__get_project_quality_gate_status` with projectKey: `mayflower-sandbox`

5. **Get Key Metrics**:
   Use `mcp__sonarqube__get_component_measures` for summary metrics

6. **Create Pull Request**:
   ```bash
   gh pr create --title "<title>" --body "<body>"
   ```

   Use this body template:
   ```markdown
   ## Summary

   [Brief description of changes]

   ## Changes

   - [List of main changes]

   ## Quality Status

   | Check | Status |
   |-------|--------|
   | CI Build | :white_check_mark: Passed |
   | Quality Gate | :white_check_mark: Passed |
   | Coverage | X% |
   | New Issues | X |

   ## Testing

   - [x] Unit tests pass
   - [x] Linting passes
   - [x] SonarQube quality gate passes
   ```

7. **Watch PR Checks**:
   ```bash
   gh pr checks --watch
   ```

## Title Generation

Generate the PR title from:
- The commit messages on the branch
- The nature of the changes (feature/fix/refactor/docs)

Format: `<type>: <brief description>`

## Error Handling

- If quality gate fails: Show which conditions failed and suggest `/sonar-fix`
- If CI fails: Show failure reason and suggest fixing before PR
- If no commits: Abort and explain

## Output

Report:
- PR URL
- PR number
- Status of all checks
- Link to SonarQube dashboard
