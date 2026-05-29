# Contributing to lifter

Thanks for wanting to contribute! Here's everything you need to know.

## Before you start

- Check existing issues and PRs to avoid duplicate work
- For significant changes, open an issue first to discuss the approach
- All contributions are released under the [MIT License](LICENSE)

## Setup

```bash
git clone https://github.com/joaoferrete/lifter.git
cd lifter
pip install -r requirements-dev.txt
cp .env.example .env
# Fill in your API keys in .env
```

## Making changes

```bash
# Create a branch
git checkout -b feat/your-feature-name   # new feature
git checkout -b fix/short-description    # bug fix

# Make your changes, then run tests
pytest tests/ -v

# Run the linter
ruff check . --ignore E501,E402,F401

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

## Pull requests

1. Push your branch and open a PR against `main`
2. Fill in the PR template (what changed, why, how to test)
3. All CI checks must pass (tests, security scan, lint)
4. One approval required before merge

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
