"""Solver detection.

This is the layer that decides whether a run is possible and with what, so a
mistake here does not produce a wrong number — it produces a run that should
never have started, or a refusal on a machine that was perfectly capable.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lattice_fea import config  # noqa: E402


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("LATTICE_"):
            monkeypatch.delenv(k, raising=False)


def test_switching_code_aster_off_actually_switches_it_off(monkeypatch, tmp_path):
    """LATTICE_ASTER_MODE=none is the one setting whose entire purpose is to
    keep the solver off — on a machine where auto-detection finds something
    that does not work, or where a run must not be allowed to start.

    The guard read `explicitly_set and cfg.mode != "none"`, so the explicit
    "none" failed the second half and fell straight through to auto-detect,
    which turned the solver back on.
    """
    monkeypatch.setenv("LATTICE_ASTER_MODE", "none")
    cfg = config.detect(str(tmp_path))
    assert cfg.mode == "none"
    assert cfg.available() is False
    assert "aster" not in {e["id"] for e in cfg.engines()}
    assert "none" in cfg.detail


def test_an_explicit_mode_is_taken_at_its_word(monkeypatch, tmp_path):
    """Detection must not second-guess a mode the user named, in either
    direction — that is what 'configured' means."""
    monkeypatch.setenv("LATTICE_ASTER_MODE", "wsl")
    monkeypatch.setenv("LATTICE_WSL_DISTRO", "aster")
    cfg = config.detect(str(tmp_path))
    assert cfg.mode == "wsl" and cfg.available()
    assert cfg.detail == "configured: wsl"


def test_a_file_setting_of_none_is_also_honoured(tmp_path):
    (tmp_path / "lattice.toml").write_text('mode = "none"\n')
    cfg = config.detect(str(tmp_path))
    assert cfg.mode == "none" and not cfg.available()


def test_with_nothing_configured_detection_still_runs(tmp_path):
    """No file, no environment: fall through to auto-detect rather than
    refusing. Most people never set any of this."""
    cfg = config.detect(str(tmp_path))
    assert cfg.detail and not cfg.detail.startswith("configured:")


def test_demo_is_recognised_from_the_command(monkeypatch, tmp_path):
    mock = os.path.join(os.path.dirname(config.__file__), "mock_solver.py")
    monkeypatch.setenv("LATTICE_ASTER_MODE", "native")
    monkeypatch.setenv("LATTICE_ASTER_CMD", f"{sys.executable} {mock}")
    assert config.detect(str(tmp_path)).is_demo() is True
    monkeypatch.setenv("LATTICE_ASTER_CMD", "as_run")
    assert config.detect(str(tmp_path)).is_demo() is False
