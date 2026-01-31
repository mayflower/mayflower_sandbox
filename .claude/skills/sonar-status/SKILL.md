---
name: sonar-status
description: Check SonarQube quality gate status and project metrics
---

# SonarQube Status Check

Get a comprehensive overview of the project's code quality status from SonarQube.

## Project Key

The SonarQube project key for this repository is: `mayflower-sandbox`

## Steps

1. **Get Quality Gate Status**:
   Use `mcp__sonarqube__get_project_quality_gate_status` with projectKey: `mayflower-sandbox`

2. **Get Key Metrics**:
   Use `mcp__sonarqube__get_component_measures` with:
   - projectKey: `mayflower-sandbox`
   - metricKeys: `["bugs", "vulnerabilities", "code_smells", "coverage", "duplicated_lines_density", "ncloc", "sqale_rating", "reliability_rating", "security_rating"]`

3. **Get Open Issues Summary**:
   Use `mcp__sonarqube__search_sonar_issues_in_projects` with:
   - projects: `["mayflower-sandbox"]`
   - Only fetch first page to get counts

## Output Format

Display a formatted summary:

```
## SonarQube Status: mayflower-sandbox

### Quality Gate: [PASSED/FAILED]

### Metrics
| Metric | Value |
|--------|-------|
| Lines of Code | X |
| Coverage | X% |
| Bugs | X |
| Vulnerabilities | X |
| Code Smells | X |
| Duplications | X% |

### Ratings
- Reliability: A/B/C/D/E
- Security: A/B/C/D/E
- Maintainability: A/B/C/D/E

### Open Issues: X total
- BLOCKER: X
- CRITICAL: X
- MAJOR: X
- MINOR: X
```

## Follow-up Suggestions

- If quality gate failed: Suggest running `/sonar-fix`
- If there are open issues: Suggest running `/sonar-fix` with severity filter
- If all clean: Confirm ready for PR
