# Publishing Lifter to PyPI

This is the operator guide for releasing Lifter. Releases are automated:
pushing a `vX.Y.Z` tag runs tests, builds the package, verifies it, and
publishes to PyPI via [Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
(OIDC) — **no API tokens are stored anywhere in CI**.

## Names

| Thing | Name | Why |
|---|---|---|
| PyPI distribution | `lifter-cli` | the name `lifter` is squatted by an unrelated, abandoned 0.1.0 package |
| Installed command | `lifter` | unchanged — `[project.scripts]` in `pyproject.toml` |
| Install method | `pipx install lifter-cli` | the package ships flat top-level modules; pipx's isolated venv avoids any name collisions. Do **not** recommend plain `pip install`. |

## One-time setup (before the first release)

1. **PyPI account** at [pypi.org](https://pypi.org) with **2FA enabled** (mandatory for new projects).
2. **Register the pending publisher** (this reserves the name and wires up OIDC in one step):
   - PyPI → *Your account* → **Publishing** → *Add a new pending publisher*:
     - PyPI project name: `lifter-cli`
     - Owner: `joaoferrete`
     - Repository: `lifter`
     - Workflow name: `release.yml`
     - Environment name: `pypi`
3. **Same on [test.pypi.org](https://test.pypi.org)** (separate account), with environment name `testpypi`.
4. **GitHub environments**: repo → Settings → Environments → create `pypi` and `testpypi`.
   Optionally add *Required reviewers* to `pypi` — this turns every real publish into a
   manual approval gate in the Actions UI.

## Release flow (the normal path)

1. Make sure `main` is green.
2. Bump `version` in `pyproject.toml` (semver: breaking → major, feature → minor, fix → patch).
3. Commit: `git commit -am "chore: release vX.Y.Z"`.
4. Tag and push:
   ```bash
   git tag vX.Y.Z
   git push origin main vX.Y.Z
   ```
5. Watch the **Release** workflow in GitHub Actions. It will:
   - run the test matrix (3.11 / 3.12),
   - fail on purpose if the tag doesn't match the `pyproject.toml` version,
   - build sdist + wheel, verify the wheel contains `locales/*.json`, verify the
     sdist has no sensitive files, run `twine check`,
   - publish to PyPI through the `pypi` environment,
   - create the GitHub Release automatically (auto-generated notes from the
     merged PRs since the last tag, with the published wheel/sdist attached).
     Skipped if a release for the tag already exists.
6. **Verify the release** on a clean machine or venv:
   ```bash
   pipx install lifter-cli
   lifter --version          # must print the new version
   lifter                    # open the app, switch UI language to Português —
                             # working translations prove locales shipped
   ```

## TestPyPI dry run (recommended before big releases)

1. GitHub → Actions → **Release** → *Run workflow* (workflow_dispatch). This
   publishes the current `main` to **TestPyPI** only.
2. Install from TestPyPI (the extra index pulls dependencies from real PyPI):
   ```bash
   pipx install --index-url https://test.pypi.org/simple/ \
     --pip-args="--extra-index-url https://pypi.org/simple/" lifter-cli
   ```
   Note: TestPyPI also refuses re-uploads of the same version — bump to a
   `X.Y.Z.devN` version if you need multiple dry runs.

## Manual fallback (twine, if CI is unavailable)

```bash
make check-dist                       # build + verify locales/sdist + twine check
# create a project-scoped API token on PyPI → account → API tokens
.venv/bin/twine upload dist/*         # prompts for the token (user: __token__)
```

Tokens are only ever needed for this path — the CI never uses them.

## Pre-release checklist

- [ ] `pytest tests/ -q` passes locally
- [ ] `make check-dist` passes (wheel contains `locales/en.json` + `locales/pt_BR.json`; `twine check` OK)
- [ ] `tar tzf dist/*.tar.gz` shows no `.env`, `*.db`, `fit_credentials.json`, `fit_token.json`, `profiles/`
- [ ] `pipx run --spec dist/*.whl lifter --version` prints the version being released
- [ ] Tag matches `pyproject.toml` version (the workflow enforces this too)
- [ ] README install instructions still correct

## After a bad release

- **Never reuse a version number** — PyPI permanently refuses re-uploads of the
  same version, even after deletion. Fix forward: bump the patch version and re-tag.
- To stop new installs of a broken version without breaking existing pins:
  PyPI project page → the release → **Yank**.
