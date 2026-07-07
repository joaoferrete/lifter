# Contributing to lifter

Thanks for wanting to contribute! Here's everything you need to know.

## Reporting bugs

Good bug reports make issues faster to fix. Please include:

1. **Steps to reproduce** — the exact sequence of menu choices or commands that trigger the issue
2. **Expected vs actual behaviour** — what you expected to happen and what happened instead
3. **Screenshot or terminal recording** — paste the console output or attach a screenshot so maintainers can see the exact error message and context
4. **Debug log** — enable debug logging (**Settings → Developer → Debug logging → on**), reproduce the issue, then attach the relevant lines from `logs/debug-YYYY-MM-DD.log`. This captures sync counts, AI errors (with status codes and provider details), and profile events that are not visible in the normal output
5. **Environment** — OS, Python version (`python --version`), and how lifter is installed (editable / pipx / direct)

> **Tip:** You can copy just the lines around the error rather than the full log file — a few seconds of context before and after the failure is usually enough.

Security issues should be reported privately — see [SECURITY.md](SECURITY.md).

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

## Project architecture

```
lifter/
├── hevy/
│   ├── client.py        API wrapper (all Hevy endpoints + payload sanitization)
│   └── sync.py          Full + incremental sync via /v1/workouts/events
├── db/
│   ├── store.py         SQLite schema + upsert helpers
│   ├── goals.py         Goal CRUD, progress computation, user preferences
│   └── memories.py      Chat memory: save/load/context
├── fit/
│   ├── auth.py          Google OAuth (InstalledAppFlow, token persistence)
│   ├── client.py        Google Fit REST API (aggregate + sessions)
│   ├── sync.py          Sync sleep and daily stats
│   └── analytics.py     Recovery score, sleep summary, activity summary
├── analytics/
│   ├── volume.py        Weekly tonnage per muscle group
│   ├── progression.py   e1RM progression and plateau detection
│   ├── frequency.py     Workout cadence and session duration
│   └── records.py       Personal records, body measurement trends, BMI helpers
├── ai/
│   ├── provider.py      Unified ChatSession abstraction (Gemini, Claude, OpenRouter, Groq, GitHub Models, Bedrock)
│   ├── coach.py         Coaching report (with scores + distribution), chat loop, goal tools, memory extraction
│   └── sanitize.py      Input sanitization and prompt-injection defence
├── cli.py               Interactive menu (questionary + Rich)
├── config.py            .env loader, runtime overrides, .env writer
├── paths.py             XDG path resolution + legacy-layout migration
├── profile_mgr.py       Multi-profile management (create, activate, switch, delete)
├── debug_log.py         Structured debug logging (toggled via Settings → Developer)
├── i18n.py              Translation layer (reads locales/*.json)
└── locales/             UI translations (shipped in the wheel as package data)
```

User data does NOT live in the repo — it resolves via `paths.py` (XDG dirs, or
`LIFTER_HOME` to force a single directory):

| Location | Contents |
|---|---|
| `~/.local/share/lifter/` | `profiles.json` + `profiles/{slug}/` (hevy.db, profile.json, fit_token.json, exports/) |
| `~/.config/lifter/` | `.env` (API keys), `fit_credentials.json` |
| `~/.local/state/lifter/` | `logs/`, chat history |

## Database tables

| Table | Contents |
|---|---|
| `workouts` | Workout metadata |
| `workout_exercises` | Exercises per workout |
| `workout_sets` | Sets per exercise (weight, reps, type) |
| `exercise_templates` | Exercise library with muscle group tags |
| `body_measurements` | Weight, fat %, body measurements by date |
| `fit_sleep` | Sleep session duration by date |
| `fit_daily` | Steps, calories, avg/min HR, active minutes by date |
| `routines` | Workout routines (created by AI or synced from Hevy) |
| `routine_exercises` | Exercises within routines |
| `routine_sets` | Sets within routine exercises |
| `user_goals` | Active and achieved training goals |
| `user_preferences` | Display name, units, height, auto-sync, default windows, cached scores, and other settings |
| `chat_memories` | Insights extracted from past conversations |
| `sync_state` | Last sync timestamps and other state keys |

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

The test suite uses an in-memory SQLite database per test — no real `hevy.db` is touched — and all AI provider calls and external HTTP requests are mocked.

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

## Releases

Publishing to PyPI (distribution name `lifter-cli`) is automated via GitHub
Actions and Trusted Publishing — see [PUBLISHING.md](PUBLISHING.md) for the
one-time setup and the tag-based release flow.

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

## Adding a translation

The UI translation layer lives in `i18n.py` and reads JSON files from `locales/`. To add a new language:

1. **Copy the English locale file and translate it:**

   ```bash
   cp locales/en.json locales/fr.json
   # edit locales/fr.json — translate every value, keep keys and {placeholders} intact
   ```

   Rules for translators:
   - **Keys** (`"menu.sync"`) — never translate, only values.
   - **`{placeholders}`** — keep them verbatim; they are filled at runtime (e.g. `{retry_after}`, `{name}`).
   - **Rich markup** (`[bold]`, `[red]...[/red]`, `[dim]`) — preserve tags exactly; they control terminal formatting.
   - Empty string values fall back to English automatically.
   - **`chat.quit_words`** — a comma-separated list of words that exit the coach chat. Translate it to natural exit words in your language (e.g. pt_BR uses `"sair, voltar, menu"`; French might use `"quitter, sortir, retour"`). The English words `quit`, `exit`, `q` and `bye` always work regardless of language. Also make sure the exit word you highlight in `chat.hint` matches one of them.

2. **Register the language code** in `i18n.py`:

   ```python
   _SUPPORTED: set = {"en", "pt_BR", "fr"}   # add your code here
   ```

3. **Add a display name** to `_UI_LANGUAGES` in `cli.py`:

   ```python
   _UI_LANGUAGES = [
       ("en",    "English"),
       ("pt_BR", "Português (Brasil)"),
       ("fr",    "Français"),           # add this line
   ]
   ```

4. **Verify** the new locale loads:

   ```bash
   python -c "from i18n import _; import i18n; i18n.init('fr'); print(_('menu.sync'))"
   ```

   If a key is missing from your locale file, Lifter falls back to English automatically — no crash.

## Code style

- Python 3.11+
- No type annotations required but encouraged for public functions
- No comments that just restate what the code does
- Tests required for any logic that touches the DB or analytics
