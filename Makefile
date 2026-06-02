.PHONY: install uninstall dev help

help:
	@echo "install    Install lifter system-wide (editable, code stays here)"
	@echo "uninstall  Remove the lifter command"
	@echo "dev        Install with dev dependencies"

install:
	pipx install --editable .

uninstall:
	pipx uninstall lifter

dev:
	.venv/bin/pip install -r requirements-dev.txt
