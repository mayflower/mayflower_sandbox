---
name: push-watch
description: Push current branch and monitor CI workflow until completion
---

# Push and Watch CI Workflow

Push the current branch to origin and monitor the triggered GitHub Actions workflow until it completes.

## Steps

1. **Push the current branch**:
   ```bash
   git push origin HEAD
   ```

2. **Get the current branch name**:
   ```bash
   git branch --show-current
   ```

3. **Wait briefly for workflow to trigger** (2-3 seconds), then find the latest workflow run:
   ```bash
   gh run list --branch <branch-name> -L 1 --json databaseId,status,conclusion,url,workflowName
   ```

4. **Watch the workflow run until completion**:
   ```bash
   gh run watch <run-id>
   ```

5. **Report the final status**:
   - If successful: Report success with the workflow URL
   - If failed: Report failure and suggest running `/sonar-status` or `gh run view <id> --log-failed`

## Error Handling

- If push fails due to remote changes, suggest `git pull --rebase`
- If no workflow is triggered within 10 seconds, check if GitHub Actions are enabled
- If workflow fails, provide the URL to view logs

## Output Format

Report concisely:
- Push status
- Workflow name and run ID
- Final status (success/failure)
- URL to the workflow run
