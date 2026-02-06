# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.2.x   | :white_check_mark: |
| < 0.2   | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability in Mayflower Sandbox, please report it
responsibly. **Do not open a public GitHub issue for security vulnerabilities.**

### Contact

- **Email:** security@mayflower.de
- **Subject line:** `[SECURITY] mayflower-sandbox: <brief description>`

### Response Timeline

| Action                          | SLA                |
|---------------------------------|--------------------|
| Acknowledge receipt             | 24 hours           |
| Initial assessment              | 5 business days    |
| Fix or mitigation plan          | 30 calendar days   |
| Public disclosure (coordinated) | 90 calendar days   |

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Affected versions
- Potential impact assessment
- Any suggested fix (optional)

### Safe Harbor

We consider security research conducted in good faith to be authorized.
We will not pursue legal action against researchers who:

- Make a good-faith effort to avoid privacy violations, data destruction,
  or interruption of service
- Provide us reasonable time to resolve the issue before disclosure
- Do not exploit the vulnerability beyond what is necessary to demonstrate it

### Encrypted Communication (PGP)

If you prefer encrypted communication, our PGP key is available at:
https://mayflower.de/.well-known/pgp-key.txt

Fingerprint: `[TO BE ADDED]`

## Security Practices

This project uses the following security measures:

- **Static Analysis:** Bandit, Ruff (flake8-bandit S rules), SonarQube
- **Type Checking:** mypy, ty
- **Dependency Scanning:** Dependabot (weekly)
- **SBOM Generation:** CycloneDX (attached to every GitHub Release)
- **Code Review:** All changes require pull request review
- **WebAssembly Sandboxing:** Code execution is isolated via Pyodide (WASM) and Deno

## Vulnerability Disclosure Process

When a vulnerability is confirmed and fixed:

1. A CVE is requested if severity warrants it
2. The fix is tagged with `[SECURITY]` in [CHANGELOG.md](CHANGELOG.md)
3. An advisory is published via [GitHub Security Advisories](https://github.com/mayflower/mayflower_sandbox/security/advisories)
4. Affected users are notified via PyPI release notes
