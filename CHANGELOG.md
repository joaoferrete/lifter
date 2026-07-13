# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Connecting Google Fit from a second profile no longer dead-ends: the
  Connect wizard was silently skipping the credentials step when a global
  `fit_credentials.json` already existed, and a Google account not listed
  as a Test user of the project hit "Error 403: access_denied" in the
  browser — with the CLI hanging forever since Google never redirects back.
  The OAuth flow now times out after 5 minutes with a message explaining
  the Test-user requirement.
- OAuth client-secrets JSON of type "Web application" is now rejected up
  front with instructions to recreate it as a Desktop app client (it can
  never complete the local sign-in flow).
- When the saved token expires (Testing-mode projects expire refresh tokens
  every ~7 days), Connect now falls through to the browser flow instead of
  failing with a cryptic `invalid_grant`; sync error messages explain the
  7-day cause.

### Changed

- The Google Fit Connect wizard is transparent about which OAuth client is
  in use: it shows the client ID, project and file path, and offers to
  reuse it (signing in with any Test-user account) or supply a different
  OAuth JSON for the current profile only.
- Google Fit credentials can now be set per profile: an optional
  `profiles/{slug}/fit_credentials.json` takes precedence over the shared
  global file (`GOOGLE_CREDENTIALS_FILE` still wins over both).
- OAuth failures are mapped to actionable messages (access denied → add the
  account as a Test user; timeout; web-type client; expired token) in both
  English and pt_BR, and the setup instructions now stress adding every
  connecting Google account as a Test user and the weekly Testing-mode
  expiry.

## [0.4.2] - 2026-07-10

### Fixed

- pt_BR: the interface no longer mixes in English — goal descriptions,
  "press any key" prompts, header stats, sync/streak lines, plateau lines,
  Fit dashboard units and every remaining hardcoded string are localized.
  Goal text is rebuilt from the goal's structured fields at display time,
  so goals created before this release are localized too.
- Goal wizards no longer crash on inputs like "1.2.3"; a cancelled name
  prompt no longer greets "None"; stats no longer shows "None%" when body
  fat was never recorded; "Auto-sync disabled" no longer shows a green
  checkmark.
- Cancelling a Gemini chat turn (Ctrl+C) no longer leaves the cancelled
  message in the conversation history.
- Bulk deletions (clear memories, clear goals) now ask for a double
  confirmation, consistent with the full data wipe.
- Sleep sessions from Google Fit on the same day are now summed (a nap no
  longer overwrites the night's sleep), and each session counts on the day
  the athlete woke up — a 23:30–07:00 night belongs to the morning's date.
- Hevy/Google Fit API calls now retry transient failures (429/5xx and
  connection errors) with backoff instead of aborting the whole sync; the
  Hevy events feed also no longer stops after the first page when the
  response carries no page counter.
- A profile that has never synced now gets the startup sync prompt (it was
  treated as "fresh"); profiles without a Hevy API key stay silent.
- e1RM for 1-rep sets is now the raw weight everywhere. The SQL paths (goal
  progress, workout cards, wizard) applied the Epley multiplier to true
  singles, inflating them ~3.3% versus the analytics views.
- Sets/week, sessions/week and weekly tonnage averages now divide by the
  requested window (clamped to training age) instead of weeks-with-data —
  sparse training was sharply inflated in stats, goal progress and the data
  fed to the AI coach.
- SQLite connections are now closed after every operation (previously one
  connection leaked per query).
- Google auth token refresh now runs with a real 20 s timeout (assigning
  `Session.timeout` was a no-op).

### Changed

- Internal architecture: `cli.py` (3,500 lines) is decomposed into `ui/` and
  `commands/` packages; `ai/coach.py` into focused context/prompts/tools/
  chat/memory modules; duplicated provider sessions share a base class.
  No behavior change intended beyond the fixes above.
- Tooling: modern ruff rule set (bugbear, pyupgrade, simplify, isort, pylint
  groups) with `ruff format` enforcement, gradual mypy type checking, and CI
  jobs for formatting, type checking and packaging verification.
- `pyproject.toml` is now the single source of truth for dependencies;
  `pip install -e '.[dev]'` replaces the old requirements files, and a
  `bedrock` extra installs the optional boto3 path.
- Docs: README/SECURITY paths updated to the XDG layout, one canonical
  upgrade instruction, `EXPORT_DIR`/`LOGS_DIR` documented, and the default
  model names aligned with the code.

## [0.4.1] and earlier

See the [GitHub releases](https://github.com/joaoferrete/lifter/releases) for
the history prior to this changelog.
