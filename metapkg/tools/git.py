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
        result = cmd.cmd('git', *args, cwd=wd, **kwargs)
        if isinstance(result, str):
            result = result.strip(' \n\t')
        return result


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
                clone_depth=50, ref=None, io) -> str:
    repo_dir = repodir(repo_url)
    repo_gitdir = repo_dir / '.git'

    git = Git(repo_dir)

    if repo_gitdir.exists():
        args = ('fetch', '-u')
        if ref is not None:
            args += ('origin', f'{ref}',)
        if clone_depth:
            args += (f'--depth={clone_depth}',)
        git.run(*args)
        status = git.run('status', '-b', '--porcelain').split('\n')[0].split()
        tracking = status[1]

        if ref:
            remote = 'FETCH_HEAD'
        else:
            local, _, remote = tracking.partition('...')
            if not remote:
                remote_name = git.run('config', f'branch.{local}.remote')
                remote_ref = git.run('config', f'branch.{local}.merge')
                remote_ref = remote_ref[len('refs/heads/'):]
                remote = f'{remote_name}/{remote_ref}'

        git.run('reset', '--hard', remote)
    else:
        args = (repo_url, repo_dir)
        if ref:
            args = ('-b', ref) + args

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
        args = ('submodule', 'update', '--init', '--checkout', '--force')
        if clone_depth:
            args += (f'--depth={clone_depth}',)
        if submodules:
            args += tuple(submodules)
        git.run(*args)

        if deinit_submodules:
            git.run(*(('submodule', 'deinit') + tuple(deinit_submodules)))

    return repo_dir
