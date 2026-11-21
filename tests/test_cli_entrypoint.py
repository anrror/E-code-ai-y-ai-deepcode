"""Top-level ``dfcode`` dispatch."""

from __future__ import annotations

import subprocess
import sys

import pytest

import dfcode


@pytest.mark.parametrize(
    "argv", [["test", "rice"], ["test", "rice", "--fast"], ["test"]]
)
def test_removed_paper_test_command_points_at_paper2code(
    monkeypatch, capsys, tmp_path, argv
):
    """`dfcode test` ran a test_paper.py that never shipped; it must say so
    instead of failing inside a subprocess."""

    monkeypatch.setattr(dfcode, "_bootstrap_logging", lambda: None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["dfcode", *argv])
    launched = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: launched.append(a))

    with pytest.raises(SystemExit) as exit:
        dfcode.main()

    assert exit.value.code == 1
    assert launched == []
    err = capsys.readouterr().err
    assert "Paper2Code" in err
    assert "dfcode desktop" in err and "dfcode web" in err


def test_help_does_not_advertise_the_removed_paper_test(monkeypatch, capsys):
    monkeypatch.setattr(dfcode, "_bootstrap_logging", lambda: None)
    monkeypatch.setattr(sys, "argv", ["dfcode", "--help"])

    dfcode.main()

    assert "dfcode test" not in capsys.readouterr().out
