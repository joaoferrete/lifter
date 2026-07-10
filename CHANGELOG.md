# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

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
