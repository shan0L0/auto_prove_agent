from pathlib import Path

from agent.lean_runner import LeanRunner


def test_lean_runner_disables_timeout_by_default():
    runner = LeanRunner(Path("lean_workspace"))
    assert runner.timeout_seconds is None


def test_lean_runner_finds_elan_binaries():
    runner = LeanRunner(Path("lean_workspace"))
    command = runner.command(Path("Attempt.lean"))
    assert command[0].endswith(("lake", "lean"))
