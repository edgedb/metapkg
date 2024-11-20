from __future__ import annotations
from typing import (
    TYPE_CHECKING,
    Any,
)

import os
import pathlib
import subprocess

import urllib3
from dulwich import repo as dulwich_repo

from poetry.core.vcs import git as core_git
from poetry.vcs import git as poetry_git

from . import cmd

if TYPE_CHECKING:
    from dulwich import repo as dulwich_repo


class Git(core_git.Git):
    def run(
        self,
        *args: Any,
        folder: pathlib.Path | None = None,
        **kwargs: Any,
    ) -> str:
        if not folder and self._work_dir and self._work_dir.exists():
            folder = self._work_dir
        result = cmd.cmd("git", *args, cwd=folder, **kwargs)
        result = result.strip(" \n\t")
        return result

    @property
    def work_tree(self) -> pathlib.Path:
        work_tree = self._work_dir
        assert work_tree is not None
        return work_tree


class GitBackend(poetry_git.Git):
    @classmethod
    def _clone_submodules(cls, repo: dulwich_repo.Repo) -> None:
        return


def repodir(repo_url: str) -> pathlib.Path:
    source_root = GitBackend.get_default_source_root()
    name = GitBackend.get_name_from_source_url(url=repo_url)
    return source_root / name


def repo(repo_url: str) -> Git:
    return Git(repodir(repo_url))


def update_repo(
    repo_url: str,
    *,
    exclude_submodules: frozenset[str] | None = None,
    clone_depth: int = 0,
    clean_checkout: bool = False,
    ref: str | None = None,
) -> pathlib.Path:
    if ref == "HEAD":
        ref = None

    if not clean_checkout:
        checkout = (
            GitBackend.get_default_source_root()
            / GitBackend.get_name_from_source_url(repo_url)
        )

        if checkout.exists():
            cache_remote_url = GitBackend.get_remote_url(
                dulwich_repo.Repo(str(checkout)),
            )
            if cache_remote_url != repo_url:
                # Origin URL has changed, perform a full clone.
                clean_checkout = True

    old_keyring_backend = os.environ.get("PYTHON_KEYRING_BACKEND")
    n = 10
    for i in range(n):
        try:
            # Prevent Poetry from trying to read system keyrings and failing
            # (specifically reading Windows keyring from an SSH session fails
            # with "A specified logon session does not exist.")
            os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
            GitBackend.clone(repo_url, revision=ref, clean=clean_checkout)
        except urllib3.exceptions.ProtocolError as e:
            if i == n-1:
                raise e
            print(f"retrying {i}th protocol error: {e}")
            continue
        finally:
            if old_keyring_backend is None:
                os.environ.pop("PYTHON_KEYRING_BACKEND")
            else:
                os.environ["PYTHON_KEYRING_BACKEND"] = old_keyring_backend
        break

    repo_dir = repodir(repo_url)
    repo = Git(repo_dir)
    args: tuple[str | pathlib.Path, ...]

    submodules: set[str] | None = None
    deinit_submodules = set()
    if exclude_submodules:
        try:
            output = repo.run(
                "config",
                "--file",
                ".gitmodules",
                "--name-only",
                "--get-regexp",
                "path",
                errors_are_fatal=False,
            )
        except subprocess.CalledProcessError as e:
            if e.returncode == 1:
                # No .gitmodules file, that's fine
                submodules = set()
            else:
                raise
        else:
            submodules = set()
            submodule_configs = output.strip().split("\n")
            for smc in submodule_configs:
                submodule_path = repo.run(
                    "config", "--file", ".gitmodules", smc
                ).strip()
                if submodule_path not in exclude_submodules:
                    submodules.add(submodule_path)
                else:
                    deinit_submodules.add(submodule_path)

    if submodules != set():
        args = ("submodule", "update", "--init", "--checkout", "--force")
        if clone_depth:
            args += (f"--depth={clone_depth}",)
        if submodules:
            args += tuple(submodules)
        repo.run(*args)

        if deinit_submodules:
            repo.run(*(("submodule", "deinit") + tuple(deinit_submodules)))

    return repo_dir
