## What changed
<!-- 1–3 bullet points. Focus on what, not how. -->
-

## Why
<!-- Link to an issue (#123) or a brief motivation. -->

## How to test
<!-- Steps a reviewer can follow to verify the change. Point to new tests when relevant. -->
1.

## Type of change
- [ ] Bug fix
- [ ] New feature / integration
- [ ] Refactor
- [ ] Documentation
- [ ] Security fix

## Checklist
- [ ] `pytest tests/ -v` passes locally
- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] `mypy .` passes
- [ ] Tests added or updated for any logic touching the DB or analytics
- [ ] User-facing strings go through `i18n._()` with keys in both `locales/en.json` and `locales/pt_BR.json`
- [ ] No sensitive files committed (`.env`, `*.db`, `fit_token.json`, `fit_credentials.json`)
- [ ] No new dependencies added without justification in the PR description
