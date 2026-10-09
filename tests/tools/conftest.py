"""Fixtures for the CI tooling tests."""

import subprocess

import pytest


class GitRepo:
    def __init__(self, path):
        self.path = path
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "user.name", "CI test")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.path, check=True, capture_output=True, text=True
        ).stdout.strip()

    def write(self, relative, text):
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, message="change"):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    return GitRepo(tmp_path)
