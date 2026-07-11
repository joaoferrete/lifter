.PHONY: install uninstall dev lint typecheck test build check-dist publish-test clean-dist help

help:
	@echo "install       Install lifter system-wide from this checkout (editable)"
	@echo "uninstall     Remove the lifter command"
	@echo "dev           Install with dev dependencies"
	@echo "lint          Run ruff check + format check"
	@echo "typecheck     Run mypy"
	@echo "test          Run the test suite"
	@echo "build         Build sdist + wheel into dist/"
	@echo "check-dist    Build and verify the distributions (locales present, twine check)"
	@echo "publish-test  Upload dist/ to TestPyPI (manual fallback; CI uses Trusted Publishing)"
	@echo "clean-dist    Remove build artifacts"

install:
	pipx install --force --editable .

uninstall:
	pipx uninstall lifter-cli

dev:
	.venv/bin/pip install -e '.[dev]'

lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .

typecheck:
	.venv/bin/mypy .

test:
	.venv/bin/pytest

clean-dist:
	rm -rf dist build *.egg-info

build: clean-dist
	.venv/bin/python -m build

check-dist: build
	@unzip -l dist/*.whl | grep -q "locales/en.json" || (echo "ERROR: locales/en.json missing from wheel" && exit 1)
	@unzip -l dist/*.whl | grep -q "locales/pt_BR.json" || (echo "ERROR: locales/pt_BR.json missing from wheel" && exit 1)
	@tar tzf dist/*.tar.gz | grep -qE "\.env$$|\.db$$|fit_credentials" && (echo "ERROR: sensitive files leaked into sdist" && exit 1) || true
	.venv/bin/twine check dist/*
	@echo "✓ dist/ looks good"

publish-test: check-dist
	.venv/bin/twine upload --repository testpypi dist/*
