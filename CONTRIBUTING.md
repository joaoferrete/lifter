# Contributing to lifter

Thanks for wanting to contribute! Here's everything you need to know.

## Before you start

- Check existing issues and PRs to avoid duplicate work
- For significant changes, open an issue first to discuss the approach
- Security issues should be reported privately first — see [SECURITY.md](SECURITY.md)
- All contributions are released under the [AGPL-3.0 License](LICENSE)

## Setup

```bash
git clone https://github.com/joaoferrete/lifter.git
cd lifter
pip install -r requirements-dev.txt
cp .env.example .env
# Fill in your API keys in .env
```

`requirements-dev.txt` adds these tools on top of the runtime dependencies:

| Package | Purpose |
|---|---|
| `pytest` + `pytest-cov` | Test runner and coverage reporting |
| `ruff` | Linter and formatter |
| `pip-audit` | Dependency vulnerability scanner |

## Making changes

```bash
# Create a branch
git checkout -b feat/your-feature-name   # new feature
git checkout -b fix/short-description    # bug fix
git checkout -b docs/update-readme       # documentation

# Make your changes, then run the full test suite
pytest tests/ -v

# Run with coverage (matches CI exactly)
pytest tests/ -v --cov=. --cov-report=term-missing

# Run a single test file
pytest tests/test_hevy_sync.py -v

# Run the linter (same flags as CI)
ruff check . --ignore E501,E402,F401 --exclude tests/

# Commit using conventional commits
git commit -m "feat: add support for X"
git commit -m "fix: correct Y when Z"
```

### Commit message format

```
type: short description (under 72 chars)

Optional longer explanation.
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `security`

## CI checks

Every PR must pass all four checks before it can be merged:

| Check | What it runs |
|---|---|
| **Tests** | `pytest` on Python 3.11 and 3.12 |
| **Lint** | `ruff check` (excluding tests/) |
| **Dependency audit** | `pip-audit` against `requirements.txt` |
| **Secret hygiene** | Scans `.env.example`, git-tracked files, and commit history |

Run them locally before pushing to catch issues early.

## Pull requests

1. Push your branch and open a PR against `main`
2. Fill in the PR template that GitHub loads automatically (see below)
3. All four CI checks must pass
4. One approval required before merge

### PR template

When you open a PR on GitHub, the description is pre-filled with this template:

```markdown
## What changed
<!-- 1–3 bullet points. Focus on what, not how. -->

## Why
<!-- Link to an issue (#123) or a brief motivation. -->

## How to test
<!-- Steps a reviewer can follow to verify the change. -->

## Type of change
- [ ] Bug fix
- [ ] New feature / integration
- [ ] Refactor
- [ ] Documentation
- [ ] Security fix

## Checklist
- [ ] `pytest tests/ -v` passes locally
- [ ] `ruff check . --ignore E501,E402,F401 --exclude tests/` passes
- [ ] Tests added or updated for any logic touching the DB or analytics
- [ ] No sensitive files committed
- [ ] No new dependencies added without justification
```

The template is stored at [`.github/pull_request_template.md`](.github/pull_request_template.md) — GitHub loads it automatically for every new PR.

## What we accept

- Bug fixes with a failing test that proves the fix
- New integrations (health apps, AI providers) following the existing pattern
- Analytics improvements with test coverage
- Documentation improvements
- Security fixes (please report privately first — see [SECURITY.md](SECURITY.md))

## What we don't accept (without discussion)

- Breaking changes to the CLI interface
- New dependencies without justification
- AI providers that require self-hosting
- Changes that remove existing functionality

## Code style

- Python 3.11+
- No type annotations required but encouraged for public functions
- No comments that just restate what the code does
- Tests required for any logic that touches the DB or analytics
