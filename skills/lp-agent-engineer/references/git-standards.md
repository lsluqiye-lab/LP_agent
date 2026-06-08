# LP-Agent Git & Traceability Standards

## 1. Commit Message Convention
Always use structured commit messages:
- `feat`: A new feature
- `fix`: A bug fix
- `docs`: Documentation only changes
- `refactor`: A code change that neither fixes a bug nor adds a feature
- `test`: Adding missing tests or correcting existing tests

Example: `feat(risk): add crash detection for SPY intraday drawdown`

## 2. Versioning
- Major (X.0.0): Significant architectural changes (e.g., v4.0 separation of engines).
- Minor (0.X.0): New features or strategy updates (e.g., v4.1 crash detection).
- Patch (0.0.X): Bug fixes or token updates.

## 3. Changelog Maintenance
Every time a version is bumped in `config.py` or `main.py`, a corresponding entry must be added to `CHANGELOG.md`.
- **Added**: for new features.
- **Changed**: for changes in existing functionality.
- **Deprecated**: for soon-to-be removed features.
- **Removed**: for now removed features.
- **Fixed**: for any bug fixes.
- **Security**: in case of vulnerabilities.
