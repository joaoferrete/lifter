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
pip install -e '.[dev]'
cp .env.example .env
# Fill in your API keys in .env
```

The `dev` extra (declared in `pyproject.toml`, the single source of truth for
dependencies) adds these tools on top of the runtime dependencies:

| Package | Purpose |
|---|---|
| `pytest` + `pytest-cov` | Test runner and coverage reporting |
| `ruff` | Linter **and** formatter (`ruff check` / `ruff format`) |
| `mypy` | Static type checker |
| `pip-audit` | Dependency vulnerability scanner |
| `build` + `twine` | Packaging and distribution checks |

## Project architecture

```
lifter/
├── hevy/
│   ├── client.py        API wrapper (all Hevy endpoints + payload sanitization)
│   └── sync.py          Full + incremental sync via /v1/workouts/events
├── db/
│   ├── store.py         SQLite schema, connection lifecycle, upsert helpers
│   ├── goals.py         Goal CRUD, typed preferences, token accounting
│   ├── memories.py      Chat memory persistence
│   └── export.py        Data export / import (backup & restore)
├── fit/
│   ├── auth.py          Google OAuth (InstalledAppFlow, token persistence)
│   ├── client.py        Google Fit REST API (aggregate + sessions)
│   ├── sync.py          Sync sleep and daily stats
│   └── analytics.py     Recovery score, sleep summary, activity summary
├── analytics/
│   ├── common.py        Shared per-week denominator + DataFrame helpers
│   ├── e1rm.py          Canonical estimated-1RM (Python + SQL fragment)
│   ├── volume.py        Weekly tonnage per muscle group
│   ├── progression.py   e1RM progression and plateau detection
│   ├── frequency.py     Workout cadence and session duration
│   ├── records.py       Personal records, body measurement trends, BMI helpers
│   └── goal_progress.py Goal progress computation (goals domain service)
├── ai/
│   ├── provider.py      Unified ChatSession abstraction (Gemini, Claude, OpenRouter, Groq, GitHub Models, Bedrock)
│   ├── coach.py         Facade: one-shot coaching report + public AI surface
│   ├── context.py       Prompt-context assembly from stored data
│   ├── prompts.py       Prompt copy and tool schemas (English by design)
│   ├── tools.py         Tool-call handlers + confirmation UI
│   ├── chat.py          Interactive chat loop
│   ├── memory.py        End-of-chat memory extraction
│   ├── errors.py        Provider-exception → friendly message mapping
│   ├── routine_schema.py Validation gate for AI-generated routine args
│   └── sanitize.py      Input sanitization and prompt-injection defence
├── ui/                  Console/style, bar widgets, prompts, value formatting
├── commands/            One module per menu domain (sync, stats, body, goals,
│                        coach, fit, settings, profiles, startup)
├── cli.py               Entry point: menu loop, ACTIONS dispatch, header
├── config.py            .env loader, runtime overrides, .env writer
├── http_retry.py        Shared HTTP timeout + retry/backoff helper
├── paths.py             XDG path resolution + legacy-layout migration
├── profile_mgr.py       Multi-profile management (create, activate, switch, delete)
├── render_cache.py      In-process memo cache for derived render data
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

# Lint, format and type-check (exactly what CI runs; config lives in pyproject.toml)
ruff check .
ruff format .
mypy .

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

Every PR must pass all checks before it can be merged:

| Check | What it runs |
|---|---|
| **Tests** | `pytest` with coverage on Python 3.11 and 3.12 |
| **Lint & format** | `ruff check .` and `ruff format --check .` (tests included) |
| **Type check** | `mypy .` |
| **Build** | `python -m build` + `twine check` + wheel/sdist content verification |
| **Dependency audit** | `pip-audit` against the project dependencies |
| **Secret hygiene** | Scans `.env.example`, git-tracked files, and commit history |

Run them locally before pushing to catch issues early (`make lint typecheck test`).

## Pull requests

1. Push your branch and open a PR against `main`
2. Fill in the PR template that GitHub loads automatically from
   [`.github/pull_request_template.md`](.github/pull_request_template.md)
3. All CI checks must pass
4. One approval required before merge

## Releases

Publishing to PyPI (distribution name `lifter-cli`) is automated via GitHub
Actions and Trusted Publishing — see [docs/PUBLISHING.md](docs/PUBLISHING.md) for the
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

3. **Add a display name** to `_UI_LANGUAGES` in `commands/_shared.py`:

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

All lint/format/type configuration lives in `pyproject.toml` — CI runs the
tools with no extra flags, so what passes locally passes in CI.

### Formatting & linting

- Python 3.11+, formatted with `ruff format` (line length 120). Run it before
  committing; CI rejects unformatted code.
- `ruff check .` must pass. The rule set includes bugbear (`B`), pyupgrade
  (`UP`), simplify (`SIM`), comprehensions (`C4`), import sorting (`I`) and
  the pylint `PLC`/`PLE`/`PLW` groups. Tests are linted too.
- Lazy in-function imports are allowed only to keep CLI startup fast or to
  break an import cycle — prefer top-level imports everywhere else.
- No comments that just restate what the code does.

### Type checking

- `mypy .` must pass. The baseline checks untyped functions
  (`check_untyped_defs`); modules listed as strict in `pyproject.toml`
  (`disallow_untyped_defs`) must stay strict, and **new modules are born
  strict** — add them to the strict list in the same PR.
- Annotate public functions. `dict`/`list` annotations should carry value
  types where practical (`dict[str, Any]` over bare `dict`).

### i18n

- **Every user-facing string goes through `i18n._()`** — no hardcoded English
  in prompts, panels, or menu output. Add each new key to **both**
  `locales/en.json` and `locales/pt_BR.json`; `tests/test_i18n_parity.py`
  fails the build on asymmetry.
- AI prompt copy (system prompts, tool descriptions) is intentionally English
  and does not go through `_()`; the model is told the answer language
  separately.

### Layering

Dependencies point downward only:

```
cli / ui / commands   →   ai, analytics, hevy, fit, db, config, i18n
ai                    →   analytics, db, hevy, fit, config
analytics             →   db, config
hevy / fit            →   db (store only), config
db                    →   config, paths
(infra utils — usable from any layer: debug_log, render_cache)
```

- `db/` must not import `ai/` or `analytics/` — prompt formatting lives in
  `ai/context.py`, goal-progress math in `analytics/goal_progress.py`.
- `analytics/` modules must not read user preferences directly (pass values in
  as parameters); the exception is `analytics/goal_progress.py`, the goals
  domain service, which uses the `db.goals` CRUD.
- UI concerns (Rich markup, questionary prompts, i18n strings) belong in the
  CLI layer, never in `db/`, `analytics/`, `hevy/`, or `fit/`.

### Error handling

- Never `except Exception: pass` silently — if an exception is deliberately
  swallowed, record a breadcrumb with `debug_log.error(...)` (or `log(...)`
  for expected noise) so failures are diagnosable.
- Tests required for any logic that touches the DB or analytics.

### Future ideas

New-feature ideas discovered while working on something else go into the
local, untracked `FUTURE_IDEAS.md` (gitignored) — not into the PR.

## Error handling conventions

- **`RuntimeError` carries a user-facing message.** The Hevy/Fit clients and the
  AI provider layer raise `RuntimeError` with a ready-to-display message; the
  main-loop safety net (`_run_action` in `cli.py`) prints it in red. Don't
  wrap these in custom exception classes.
- **Anything else is a bug.** Unexpected exceptions are caught by the safety
  net, shown as a generic panel, and logged with a full traceback.
- **`debug_log.error()` always writes** (with traceback when given `exc=`),
  regardless of the `debug_logging` pref. `debug_log.log()` stays gated behind
  the pref — use it for chatty diagnostics only.
- **Validate AI tool arguments at the boundary.** Routine tool calls go through
  `ai/routine_schema.validate_routine_args()` before anything is rendered,
  confirmed, or persisted; garbage args are rejected with an error the model
  can act on. Truncated responses (`ChatResponse.stop_reason == "max_tokens"`)
  must never be dispatched as tool calls.
