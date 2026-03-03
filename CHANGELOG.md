# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Security-relevant changes are tagged with `[SECURITY]`.

## [Unreleased]

### Added

- `SECURITY.md` with vulnerability disclosure policy and safe harbor clause
- `SUPPORT.md` with support period and update channels
- `CONTRIBUTING.md` with secure development policy and CI gate documentation
- `CHANGELOG.md` (this file)
- SBOM generation (CycloneDX) in CI release workflow
- SBOM validation step in CI quality checks
- Dependabot configuration for automated dependency updates (pip, github-actions)
- GitHub Release automation workflow with SBOM artifacts
- Security and SBOM badges in README

## [0.2.0] - 2025-01-31

### Added

- DeepAgents `SandboxBackendProtocol` adapter (`MayflowerSandboxBackend`)
- `PostgresBackend` implementing `BackendProtocol` for file storage
- Integration tests for `PostgresBackend` and `MayflowerSandboxBackend`
- BusyBox WASM shell executor with Worker-based pipe support
- Worker pool for Pyodide execution (70-95% latency reduction)
- MCP (Model Context Protocol) integration with code mode
- Skill installation tool (`skill_install`)
- Human-in-the-Loop (HITL) approval for destructive operations
- SonarQube integration with SARIF, Bandit, ESLint, and coverage reports
- Bandit security scanning in CI pipeline
- LLM-based error analysis for Pyodide execution failures
- Glob and Grep tools for file operations
- String replace tool (`FileEditTool`) for file editing

### Fixed

- [SECURITY] Fix ReDoS vulnerabilities in shell command parsing
- Worker pool initialization reset on failure
- Async deepagents backend execution
- Shell executor unified Worker-based execution
- Stateful execution micropip output suppression
- Session recovery metadata JSON serialization for asyncpg

### Changed

- Migrate from LangChain tools API to DeepAgents `SandboxBackendProtocol`
- Upgrade to Deno v2.x
- Upgrade session serialization library for Pyodide persistence

## [0.1.0] - 2024-10-24

### Added

- Initial release
- Pyodide WebAssembly sandbox for secure Python execution
- PostgreSQL-backed virtual filesystem with 20MB file limit
- Thread isolation via `thread_id`
- LangChain tool integration (12 tools extending `BaseTool`)
- Document processing helpers (Word, Excel, PowerPoint, PDF)
- HTTP file server for downloads
- Session management with automatic cleanup (180 days default)
- GitHub Actions CI workflow with quality checks and testing
