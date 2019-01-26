import os.path
import pathlib
import subprocess
import urllib.parse

from poetry import vcs

from metapkg import cache

from . import cmd


class Git(vcs.Git):

    def run(self, *args, **kwargs):
        if self._work_dir and self._work_dir.exists():
            wd = self._work_dir.as_posix()
        else:
            wd = None
        return cmd.cmd('git', *args, cwd=wd, **kwargs)


def _repodir(repo_url):
    u = urllib.parse.urlparse(repo_url)
    base = os.path.basename(u.path)
    name, _ = os.path.splitext(base)
    return pathlib.Path(name)


def repodir(repo_url):
    return cache.cachedir() / _repodir(repo_url)


def repo(repo_url):
    return Git(repodir(repo_url))


def update_repo(repo_url, *, exclude_submodules=None,
                clone_depth=50, branch=None, io) -> str:
    repo_dir = repodir(repo_url)
    repo_gitdir = repo_dir / '.git'

    git = Git(repo_dir)

    if repo_gitdir.exists():
        args = ('fetch',)
        if clone_depth:
            args += (f'--depth={clone_depth}',)
        git.run(*args)
        status = git.run('status', '-b', '--porcelain').strip().split(' ')
        tracking = status[1]
        local, _, remote = tracking.partition('...')
        if not remote:
            remote_name = git.run('config', f'branch.{local}.remote').strip()
            if branch:
                remote_ref = branch
            else:
                remote_ref = git.run('config', f'branch.{local}.merge').strip()
                remote_ref = remote_ref[len('refs/heads/'):]
                remote = f'{remote_name}/{remote_ref}'

        git.run('reset', '--hard', remote)
    else:
        args = (repo_url, repo_dir)
        if branch:
            args = ('-b', branch) + args

        if clone_depth:
            args += (f'--depth={clone_depth}',)

        git.run('clone', *args)

    submodules = None
    deinit_submodules = set()
    if exclude_submodules:
        try:
            output = git.run(
                'config', '--file', '.gitmodules', '--name-only',
                '--get-regexp', 'path', errors_are_fatal=False)
        except subprocess.CalledProcessError as e:
            if e.returncode == 1:
                # No .gitmodules file, that's fine
                submodules = set()
            else:
                raise
        else:
            submodules = set()
            submodule_configs = output.strip().split('\n')
            for smc in submodule_configs:
                submodule_path = git.run(
                    'config', '--file', '.gitmodules', smc).strip()
                if submodule_path not in exclude_submodules:
                    submodules.add(submodule_path)
                else:
                    deinit_submodules.add(submodule_path)

    if submodules != set():
        args = ('submodule', 'update', '--init')
        if clone_depth:
            args += (f'--depth={clone_depth}',)
        if submodules:
            args += tuple(submodules)
        git.run(*args)

        if deinit_submodules:
            git.run(*(('submodule', 'deinit') + tuple(deinit_submodules)))

    return repo_dir
