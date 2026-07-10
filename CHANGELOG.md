# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

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

- Tooling: modern ruff rule set (bugbear, pyupgrade, simplify, isort, pylint
  groups) with `ruff format` enforcement, gradual mypy type checking, and CI
  jobs for formatting, type checking and packaging verification.
- `pyproject.toml` is now the single source of truth for dependencies;
  `pip install -e '.[dev]'` replaces the old requirements files, and a
  `bedrock` extra installs the optional boto3 path.

## [0.4.2] and earlier

See the [GitHub releases](https://github.com/joaoferrete/lifter/releases) for
the history prior to this changelog.
