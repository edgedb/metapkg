import os.path
import pathlib
import shutil
import urllib.parse

from poetry import vcs

from metapkg import cache

from . import cmd


class Git(vcs.Git):

    def run(self, *args):
        if self._work_dir and self._work_dir.exists():
            wd = self._work_dir.as_posix()
        else:
            wd = None
        return cmd.cmd('git', *args, cwd=wd)


def _repodir(repo_url):
    u = urllib.parse.urlparse(repo_url)
    base = os.path.basename(u.path)
    name, _ = os.path.splitext(base)
    return pathlib.Path(name)


def repodir(repo_url):
    return cache.cachedir() / _repodir(repo_url)


def repo(repo_url):
    return Git(repodir(repo_url))


def update_repo(repo_url, io) -> str:
    repo_dir = repodir(repo_url)
    repo_gitdir = repo_dir / '.git'

    git = Git(repo_dir)

    if repo_gitdir.exists():
        git.run('fetch')
        status = git.run('status', '-b', '--porcelain').strip().split(' ')
        tracking = status[1]
        local, _, remote = tracking.partition('...')
        if not remote:
            remote_name = git.run('config', f'branch.{local}.remote').strip()
            remote_ref = git.run('config', f'branch.{local}.merge').strip()
            remote_ref = remote_ref[len('refs/heads/'):]
            remote = f'{remote_name}/{remote_ref}'

        git.run('reset', '--hard', remote)
    else:
        if repo_gitdir.exists():
            # Repo dir exists for some reason, remove it.
            shutil.rmtree(repo_dir)

        git.clone(repo_url, repo_dir)

    return repo_dir
