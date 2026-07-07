"""Tests for XDG path resolution (paths.resolve_dirs)."""
from pathlib import Path

import paths


def test_defaults_without_env():
    dirs = paths.resolve_dirs({})
    home = Path.home()
    assert dirs.data == home / ".local/share" / "lifter"
    assert dirs.config == home / ".config" / "lifter"
    assert dirs.state == home / ".local/state" / "lifter"


def test_xdg_vars_honored():
    dirs = paths.resolve_dirs({
        "XDG_DATA_HOME": "/srv/data",
        "XDG_CONFIG_HOME": "/srv/config",
        "XDG_STATE_HOME": "/srv/state",
    })
    assert dirs.data == Path("/srv/data/lifter")
    assert dirs.config == Path("/srv/config/lifter")
    assert dirs.state == Path("/srv/state/lifter")


def test_relative_xdg_values_ignored():
    """The XDG spec requires relative values to be treated as unset."""
    dirs = paths.resolve_dirs({"XDG_DATA_HOME": "relative/dir"})
    assert dirs.data == Path.home() / ".local/share" / "lifter"


def test_lifter_home_collapses_all_and_wins():
    dirs = paths.resolve_dirs({
        "LIFTER_HOME": "/opt/lifter-home",
        "XDG_DATA_HOME": "/srv/data",
    })
    assert dirs.data == dirs.config == dirs.state == Path("/opt/lifter-home")


def test_lifter_home_expands_tilde():
    dirs = paths.resolve_dirs({"LIFTER_HOME": "~/my-lifter"})
    assert dirs.data == Path.home() / "my-lifter"
