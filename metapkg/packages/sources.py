import hashlib
import pathlib
import re
import requests
import shutil
import tarfile
import tempfile
import typing
import urllib.parse
import zipfile

from metapkg import cache
from metapkg import tools


class BaseVerification:
    pass


class HashVerification(BaseVerification):

    def __init__(self, algorithm: str, *, hash_url=None, hash_value=None):
        self.algorithm = algorithm
        if hash_value is not None:
            self._hash_value = hash_value
        elif hash_url is not None:
            self._hash_url = hash_url
            self._hash_value = None
        else:
            raise ValueError('either hash_url or hash_value is required '
                             'for HashVerification')

    def verify(self, path: str):
        if self._hash_value is None:
            self._obtain_hash_value()

        hashfunc = hashlib.new(self.algorithm)
        with open(path, 'rb') as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                hashfunc.update(chunk)

        if hashfunc.hexdigest() != self._hash_value:
            raise ValueError(
                f'{path} does not match expected {self.algorithm} value of '
                f'{self._hash_value}')

    def _obtain_hash_value(self):
        content = requests.get(self._hash_url).text.strip()
        firstval, _, rest = content.partition(' ')
        self._hash_value = firstval
        return self._hash_value


class BaseSource:
    def __init__(self, url: str, name: str):
        self.url = url
        self.verifications = []
        self.name = name

    def add_verification(self, verification):
        self.verifications.append(verification)

    def verify(self, path):
        for verification in self.verifications:
            verification.verify(path)


class HttpsSource(BaseSource):
    def download(self, io) -> str:
        destination_dir = cache.cachedir() / 'distfiles'
        if not destination_dir.exists():
            destination_dir.mkdir()

        destination = destination_dir / self.name
        if destination.exists():
            try:
                self.verify(destination)
            except Exception:
                io.writeln(
                    f'<warning>Cached {self.name} exists, but does pass '
                    f'verification.  Downloading anew.')
            else:
                return destination

        return self._download(destination, io)

    def _download(self, destination, io) -> str:
        req = requests.get(self.url, stream=True)
        length = int(req.headers.get('content-length', 0))

        progress = io.create_progress_bar(length)
        io.writeln(f'Downloading <info>{self.url}</>')
        progress.start(length)

        try:
            with open(destination, 'wb') as f:
                for chunk in req.iter_content(chunk_size=4096):
                    if chunk:
                        progress.advance(len(chunk))
                        f.write(chunk)
        except BaseException:
            if destination.exists():
                destination.unlink()
        finally:
            progress.finish()
            io.writeln('')

        try:
            self.verify(destination)
        except Exception:
            destination.unlink()
            raise

        return destination

    def tarball(
            self, pkg, name_tpl: typing.Optional[str]=None, *, io) \
            -> pathlib.Path:
        src = self.download(io)
        if src.suffix == '.tgz':
            comp = '.gz'
        elif src.suffix == '.tbz2':
            comp = '.bzip2'
        else:
            comp = src.suffix
        if name_tpl is None:
            name_tpl = f'{pkg.unique_name}{{part}}.tar{{comp}}'
        name = name_tpl.format(part='', comp=comp)
        shutil.copy(src, name)

        return pathlib.Path(name)


class GitSource(BaseSource):

    def __init__(self, url: str, name: str, *, branch=None,
                 exclude_submodules=None, clone_depth=50):
        super().__init__(url, name)
        self.branch = branch
        self.exclude_submodules = exclude_submodules
        self.clone_depth = clone_depth

    def download(self, io) -> str:
        return tools.git.update_repo(
            self.url, exclude_submodules=self.exclude_submodules,
            clone_depth=self.clone_depth, branch=self.branch, io=io)

    def tarball(
            self, pkg, name_tpl: typing.Optional[str]=None, *, io) \
            -> pathlib.Path:
        self.download(io)
        repo = tools.git.repo(self.url)
        if name_tpl is None:
            name_tpl = f'{pkg.unique_name}{{part}}.tar{{comp}}'
        name = name_tpl.format(part='', comp='')
        repo.run(
            'archive', f'--output={name}', '--format=tar',
            f'--prefix={pkg.unique_name}/', 'HEAD')

        submodules = repo.run(
            'submodule', 'foreach', '--recursive').strip('\n')
        if submodules:
            submodules = submodules.split('\n')
            for submodule in submodules:
                path_m = re.match("Entering '([^']+)'", submodule)
                if not path_m:
                    raise ValueError(
                        'cannot parse git submodule foreach output')
                path = path_m.group(1)
                module_repo = tools.git.Git(repo._work_dir / path)

                with tempfile.NamedTemporaryFile() as f:
                    module_repo.run(
                        'archive', '--format=tar', f'--output={f.name}',
                        f'--prefix={pkg.unique_name}/{path}/', 'HEAD'
                    )

                    tools.cmd(
                        'tar', '--concatenate', '--file', name, f.name)

        final_name = name_tpl.format(part='', comp='.gz')
        tools.cmd('gzip', name)

        return pathlib.Path(final_name)


def source_for_url(url: str,
                   extras: typing.Optional[dict] = None) -> BaseSource:
    parts = urllib.parse.urlparse(url)
    path_parts = parts.path.split('/')
    name = path_parts[-1]
    if extras is None:
        extras = {}
    if parts.scheme == 'https':
        return HttpsSource(url, name=name, **extras)
    elif parts.scheme.startswith('git+'):
        return GitSource(url[4:], name=name, **extras)
    else:
        raise ValueError(f'unsupported source URL scheme: {parts.scheme}')


def unpack(archive: pathlib.Path, dest: pathlib.Path, io) -> None:
    parts = archive.name.split('.')
    if len(parts) == 1:
        raise ValueError(f'{archive.name} is not a supported archive')

    zf = None

    if not dest.exists():
        dest.mkdir()

    if parts[-2] == 'tar' or parts[-1] in {'tgz', 'tbz2', 'tar'}:
        if parts[-1] in ('gz', 'tgz'):
            compression = 'gz'
        elif parts[-1] in ('bz2', 'tbz2'):
            compression = 'bz2'
        elif parts[-1] == 'xz':
            compression = 'xz'
        else:
            raise ValueError(f'{archive.name} is not a supported archive')

        zf = tarfile.open(archive, f'r:{compression}')
        try:
            for member in zf.getmembers():
                parts = pathlib.Path(member.name).parts
                if len(parts) == 1:
                    continue

                path = pathlib.Path(parts[1]).joinpath(*parts[2:])
                member.name = str(path)
                zf.extract(member, path=dest)
        finally:
            zf.close()

    elif parts[-1] == 'zip':
        zf = zipfile.open(archive)

        try:
            for member in zf.infolist():
                parts = pathlib.Path(member.name).parts
                if len(parts) == 1:
                    continue

                path = pathlib.Path(parts[1]).joinpath(*parts[2:])
                member.filename = str(path)
                zf.extract(member, path=dest)
        finally:
            zf.close()

    else:
        raise ValueError(f'{archive.name} is not a supported archive')
