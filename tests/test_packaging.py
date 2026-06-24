"""Guard against shipping a broken package.

The project uses a flat layout: top-level single-file modules are listed
explicitly under ``[tool.setuptools] py-modules`` in pyproject.toml. When a new
root module is added but not listed, the source tree and pytest still work (the
repo root is on sys.path), yet a ``pipx``/wheel install raises ModuleNotFoundError
at runtime. This test catches that omission early.
"""
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _root_module_stems() -> set[str]:
    """Top-level .py files that ship as standalone modules (excludes packages)."""
    return {
        p.stem for p in _ROOT.glob("*.py")
        if p.name != "setup.py"
    }


def _declared_py_modules() -> list[str]:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    return data["tool"]["setuptools"]["py-modules"]


def test_all_root_modules_are_packaged():
    declared = set(_declared_py_modules())
    missing = _root_module_stems() - declared
    assert not missing, (
        f"Root modules missing from [tool.setuptools] py-modules in pyproject.toml: "
        f"{sorted(missing)}. Add them or a pipx/wheel install will fail to import them."
    )


def test_declared_py_modules_all_exist():
    """Every declared module must correspond to a real root .py file."""
    stale = [m for m in _declared_py_modules() if not (_ROOT / f"{m}.py").exists()]
    assert not stale, f"py-modules lists non-existent modules: {stale}"
