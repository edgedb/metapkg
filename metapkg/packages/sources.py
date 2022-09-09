from __future__ import annotations
from typing import (
    TYPE_CHECKING,
    Any,
    Iterable,
    TypedDict,
)

import hashlib
import os
import pathlib
import platform
import re
import shlex
import shutil
import tarfile
import tempfile
import typing
import urllib.parse
import zipfile

import requests

from metapkg import cache
from metapkg import packages as mpkg
from metapkg import targets
from metapkg import tools

from cleo.ui import progress_bar

if TYPE_CHECKING:
    from cleo.io import io as cleo_io


class SourceDeclBase(TypedDict):
    url: str


class SourceDecl(SourceDeclBase, total=False):
    csum: str | None
    csum_url: str | None
    csum_algo: str | None
    extras: SourceExtraDecl


SourceExtraDecl = TypedDict(
    "SourceExtraDecl",
    {
        "exclude_submodules": list[str],
        "clone_depth": int,
        "version": str,
        "vcs_version": str,
        "include_gitdir": bool,
    },
    total=False,
)


class BaseVerification:
    def verify(self, path: pathlib.Path) -> None:
        raise NotImplementedError


class HashVerification(BaseVerification):
    def __init__(
        self,
        algorithm: str,
        *,
        hash_url: str | None = None,
        hash_value: str | None = None,
    ) -> None:
        self.algorithm = algorithm
        self._hash_value: str | None
        if hash_value is not None:
            self._hash_value = hash_value
        elif hash_url is not None:
            self._hash_url = hash_url
            self._hash_value = None
        else:
            raise ValueError(
                "either hash_url or hash_value is required "
                "for HashVerification"
            )

    def verify(self, path: pathlib.Path) -> None:
        if self._hash_value is None:
            self._obtain_hash_value()

        hashfunc = hashlib.new(self.algorithm)
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                hashfunc.update(chunk)

        if hashfunc.hexdigest() != self._hash_value:
            raise ValueError(
                f"{path} does not match expected {self.algorithm} value of "
                f"{self._hash_value}"
            )

    def _obtain_hash_value(self) -> str:
        content = requests.get(self._hash_url).text.strip()
        firstval, _, rest = content.partition(" ")
        self._hash_value = firstval
        return firstval


class BaseSource:
    def __init__(self, url: str, name: str, **extras: Any) -> None:
        self.url = url
        self.verifications: list[BaseVerification] = []
        self.name = name
        self.extras = extras

    def add_verification(self, verification: BaseVerification) -> None:
        self.verifications.append(verification)

    def verify(self, path: pathlib.Path) -> None:
        for verification in self.verifications:
            verification.verify(path)

    def copy(
        self,
        target_dir: pathlib.Path,
        *,
        io: cleo_io.IO,
    ) -> None:
        raise NotImplementedError

    def tarball(
        self,
        pkg: mpkg.BasePackage,
        name_tpl: typing.Optional[str] = None,
        *,
        target_dir: pathlib.Path,
        io: cleo_io.IO,
        build: targets.Build,
    ) -> pathlib.Path:
        raise NotImplementedError


class HttpsSource(BaseSource):
    def download(self, io: cleo_io.IO) -> pathlib.Path:
        destination_dir = cache.cachedir() / "distfiles"
        if not destination_dir.exists():
            destination_dir.mkdir()

        destination = destination_dir / self.name
        if destination.exists():
            try:
                self.verify(destination)
            except Exception:
                io.write_line(
                    f"<warning>Cached {self.name} exists, but does pass "
                    f"verification.  Downloading anew."
                )
            else:
                return destination

        return self._download(destination, io)

    def _download(
        self, destination: pathlib.Path, io: cleo_io.IO
    ) -> pathlib.Path:
        req = requests.get(self.url, stream=True)
        length = int(req.headers.get("content-length", 0))

        progress = progress_bar.ProgressBar(io, max=length)
        io.write_line(f"Downloading <info>{self.url}</>")
        if req.status_code < 200 or req.status_code >= 300:
            raise RuntimeError(f"download failed: {req.status_code}")

        progress.start(length)

        try:
            with open(destination, "wb") as f:
                for chunk in req.iter_content(chunk_size=4096):
                    if chunk:
                        progress.advance(len(chunk))
                        f.write(chunk)
        except BaseException:
            if destination.exists():
                destination.unlink()
        finally:
            progress.finish()
            io.write_line("")

        try:
            self.verify(destination)
        except Exception:
            destination.unlink()
            raise

        return destination

    def _tarball(
        self,
        pkg: typing.Optional[mpkg.BasePackage] = None,
        name_tpl: typing.Optional[str] = None,
        *,
        target_dir: pathlib.Path,
        io: cleo_io.IO,
    ) -> pathlib.Path:
        if name_tpl is None:
            assert pkg is not None
            name_tpl = f"{pkg.unique_name}{{part}}.tar{{comp}}"
        src = self.download(io)
        copy = True
        if src.suffix == ".tgz":
            comp = ".gz"
        elif src.suffix == ".tbz2":
            comp = ".bzip2"
        elif src.suffix != ".tar" and ".tar" in src.suffixes:
            comp = src.suffix
        elif src.suffix == ".zip":
            comp = ".gz"
            target_path = target_dir / name_tpl.format(part="", comp=comp)
            with tempfile.TemporaryDirectory() as tmpdir:
                destdir = pathlib.Path(tmpdir)
                unpack(src, dest=destdir, io=io, strip_top=False)
                subdirs = os.listdir(destdir)
                if len(subdirs) > 1:
                    raise RuntimeError(
                        "multiple top-level directories in source archive"
                    )
                subdir = next(iter(subdirs))
                with tarfile.open(target_path, "w:gz") as tf:
                    tf.add(str(destdir / subdir), arcname=subdir)
            copy = False
        else:
            raise RuntimeError(f"unsupported archive format: {src.suffix}")

        if copy:
            target_path = target_dir / name_tpl.format(part="", comp=comp)
            shutil.copy(src, target_path)

        return target_path

    def tarball(
        self,
        pkg: typing.Optional[mpkg.BasePackage] = None,
        name_tpl: typing.Optional[str] = None,
        *,
        target_dir: pathlib.Path,
        io: cleo_io.IO,
        build: targets.Build,
    ) -> pathlib.Path:
        return self._tarball(pkg, name_tpl, target_dir=target_dir, io=io)

    def copy(
        self,
        target_dir: pathlib.Path,
        *,
        io: cleo_io.IO,
    ) -> None:
        self.download(io)
        with tempfile.TemporaryDirectory() as t:
            tardir = pathlib.Path(t)
            tarball = self._tarball(
                name_tpl="tmp{part}.tar{comp}",
                target_dir=tardir,
                io=io,
            )
            unpack(tarball, dest=target_dir, io=io)


class LocalSource(BaseSource):
    def tarball(
        self,
        pkg: mpkg.BasePackage,
        name_tpl: typing.Optional[str] = None,
        *,
        target_dir: pathlib.Path,
        io: cleo_io.IO,
        build: targets.Build,
    ) -> pathlib.Path:
        comp = ".gz"
        if name_tpl is None:
            name_tpl = f"{pkg.unique_name}{{part}}.tar{{comp}}"
        target_path = target_dir / name_tpl.format(part="", comp=comp)

        tar = shlex.split(build.sh_get_command("tar"))
        tools.cmd(
            *tar,
            *[
                f"--directory={self.url}",
                "--exclude-vcs",
                "--exclude-vcs-ignores",
                "--create",
                "--gzip",
                f"--transform=flags=r;s|^\\./|{pkg.unique_name}/|",
                f"--file={target_path}",
                ".",
            ],
        )

        return target_path

    def copy(
        self,
        target_dir: pathlib.Path,
        *,
        io: cleo_io.IO,
    ) -> None:
        shutil.copytree(self.url, target_dir)


class GitSource(BaseSource):
    def __init__(
        self,
        url: str,
        name: str,
        *,
        vcs_version: str | None = None,
        exclude_submodules: Iterable[str] | None = None,
        clone_depth: int = 0,
        include_gitdir: bool = False,
    ) -> None:
        super().__init__(url, name)
        self.ref = vcs_version
        if exclude_submodules is not None:
            self.exclude_submodules = frozenset(exclude_submodules)
        else:
            self.exclude_submodules = frozenset()
        self.clone_depth = clone_depth
        self.include_gitdir = include_gitdir

    def download(self, io: cleo_io.IO) -> pathlib.Path:
        return tools.git.update_repo(
            self.url,
            exclude_submodules=self.exclude_submodules,
            clone_depth=self.clone_depth,
            ref=self.ref,
        )

    def copy(
        self,
        target_dir: pathlib.Path,
        *,
        io: cleo_io.IO,
    ) -> None:
        self.download(io)
        repo = tools.git.repo(self.url)
        repo.run(
            "checkout-index",
            "-a",
            "-f",
            f"--prefix={target_dir}",
        )

    def tarball(
        self,
        pkg: mpkg.BasePackage,
        name_tpl: typing.Optional[str] = None,
        *,
        target_dir: pathlib.Path,
        io: cleo_io.IO,
        build: targets.Build,
    ) -> pathlib.Path:
        self.download(io)
        repo = tools.git.repo(self.url)
        if name_tpl is None:
            name_tpl = f"{pkg.unique_name}{{part}}.tar{{comp}}"
        target_path = target_dir / name_tpl.format(part="", comp="")

        repo.run(
            "archive",
            f"--output={target_path}",
            "--format=tar",
            f"--prefix={pkg.unique_name}/",
            "HEAD",
        )

        submodules = repo.run("submodule", "foreach", "--recursive").strip(
            "\n"
        )
        if submodules:
            for submodule in submodules.split("\n"):
                path_m = re.match("Entering '([^']+)'", submodule)
                if not path_m:
                    raise ValueError(
                        "cannot parse git submodule foreach output"
                    )
                path = path_m.group(1)
                module_repo = tools.git.Git(repo.work_tree / path)

                with tempfile.NamedTemporaryFile() as f:
                    module_repo.run(
                        "archive",
                        "--format=tar",
                        f"--output={f.name}",
                        f"--prefix={pkg.unique_name}/{path}/",
                        "HEAD",
                    )

                    self._tar_append(pathlib.Path(f.name), target_path)

        if self.include_gitdir:
            repo_dir = tools.git.repodir(self.url)
            repo_gitdir = repo_dir / ".git"
            prefix = f"{pkg.unique_name}/.git/"
            with tarfile.open(target_path, "a") as tf:
                tf.add(repo_gitdir, prefix)

        tools.cmd("gzip", target_path, cwd=target_dir)
        return pathlib.Path(f"{target_path}.gz")

    def _tar_append(
        self,
        source_tarball: pathlib.Path,
        target_tarball: pathlib.Path,
    ) -> None:
        if platform.system() == "Darwin":
            with tarfile.open(source_tarball) as modf, tarfile.open(
                target_tarball, "a"
            ) as tf:
                for m in modf.getmembers():
                    if m.issym():
                        # Skip broken symlinks.
                        target = os.path.normpath(
                            "/".join(
                                filter(
                                    None,
                                    (
                                        os.path.dirname(m.name),
                                        m.linkname,
                                    ),
                                )
                            )
                        )
                        try:
                            modf.getmember(target)
                        except KeyError:
                            continue
                    tf.addfile(m, modf.extractfile(m))

        else:
            tools.cmd(
                "tar",
                "--concatenate",
                "--file",
                target_tarball,
                source_tarball,
            )


def source_for_url(
    url: str,
    extras: SourceExtraDecl | None = None,
) -> BaseSource:
    parts = urllib.parse.urlparse(url)
    path_parts = parts.path.split("/")
    name = path_parts[-1]
    if extras is None:
        extras = {}
    if parts.scheme == "https" or parts.scheme == "http":
        return HttpsSource(url, name=name, **extras)
    elif parts.scheme.startswith("git+"):
        extras_dict = dict(extras)
        version = extras_dict.pop("version", None)
        if "vcs_version" not in extras and version is not None:
            extras_dict["vcs_version"] = version
        return GitSource(url[4:], name=name, **extras_dict)  # type: ignore
    elif parts.scheme == "file":
        return LocalSource(parts.path, name, **extras)
    else:
        raise ValueError(f"unsupported source URL scheme: {parts.scheme}")


def unpack(
    archive: pathlib.Path,
    dest: pathlib.Path,
    io: cleo_io.IO,
    *,
    strip_top: bool = True,
) -> None:
    parts = archive.name.split(".")
    if len(parts) == 1:
        raise ValueError(f"{archive.name} is not a supported archive")

    if not dest.exists():
        dest.mkdir()

    ext = parts[-1]

    if parts[-2] == "tar" or ext in {"tgz", "tbz2", "tar"}:
        unpack_tar(archive, dest, strip_top=strip_top)
    elif parts[-1] == "zip":
        unpack_zip(archive, dest, strip_top=strip_top)
    else:
        raise ValueError(f"{archive.name} is not a supported archive")


def unpack_tar(
    archive: pathlib.Path, dest: pathlib.Path, *, strip_top: bool
) -> None:
    ext = archive.suffix

    if ext in (".gz", ".tgz"):
        compression = "gz"
    elif ext in (".bz2", ".tbz2"):
        compression = "bz2"
    elif ext == ".xz":
        compression = "xz"
    else:
        raise ValueError(f"{archive.name} is not a supported archive")

    tf = tarfile.open(archive, f"r:{compression}")
    try:
        for member in tf.getmembers():
            if strip_top:
                member_parts = pathlib.Path(member.name).parts
                if len(member_parts) < 2:
                    continue

                path = pathlib.Path(member_parts[1]).joinpath(
                    *member_parts[2:]
                )
                member.name = str(path)
            tf.extract(member, path=dest)
    finally:
        tf.close()


def unpack_zip(
    archive: pathlib.Path, dest: pathlib.Path, *, strip_top: bool
) -> None:

    zf = zipfile.ZipFile(archive)

    try:
        for member in zf.infolist():
            if strip_top:
                member_parts = pathlib.Path(member.filename).parts
                if len(member_parts) == 1:
                    continue

                relpath = pathlib.Path(member_parts[1]).joinpath(
                    *member_parts[2:]
                )
            else:
                relpath = pathlib.Path(member.filename)
            targetpath = dest / relpath
            if member.is_dir():
                targetpath.mkdir(parents=True, exist_ok=True)
            else:
                dirname = targetpath.parent
                if not dirname.exists():
                    dirname.mkdir(parents=True)
                with open(targetpath, "wb") as df, zf.open(member) as sf:
                    shutil.copyfileobj(sf, df)
    finally:
        zf.close()
