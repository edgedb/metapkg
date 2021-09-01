from __future__ import annotations
from typing import *

import pathlib
import shlex
import sys
import tempfile
import textwrap
import typing

from poetry import semver
from poetry import packages as poetry_pkg
from poetry.packages.constraints import generic_constraint as poetry_genconstr
from poetry.repositories import pypi_repository

from metapkg import tools

from . import base
from . import repository as af_repository
from . import sources as af_sources
from .utils import python_dependency_from_pep_508


platform_constraint = poetry_genconstr.GenericConstraint("=", sys.platform)
python_dependency = poetry_pkg.Dependency(name="python", constraint=">=3.10")
wheel_dependency = poetry_pkg.Dependency(name="pypkg-wheel", constraint="*")


def set_python_runtime_dependency(dep):
    global python_dependency
    python_dependency = dep


class PyPiRepository(pypi_repository.PyPiRepository):
    def __init__(self, io) -> None:
        super().__init__()
        self._io = io
        self._pkg_impls: Dict[str, Type[PythonPackage]] = {}

    def register_package_impl(
        self,
        name: str,
        impl_cls: Type[PythonPackage],
    ) -> None:
        self._pkg_impls[name] = impl_cls

    def find_packages(
        self,
        name: str,
        constraint: typing.Optional[
            typing.Union[semver.VersionConstraint, str]
        ] = None,
        extras: typing.Optional[list] = None,
        allow_prereleases: bool = False,
    ) -> typing.List[poetry_pkg.Package]:

        if name.startswith("pypkg-"):
            name = name[len("pypkg-") :]
        else:
            return []

        packages = super().find_packages(
            name, constraint, extras, allow_prereleases
        )

        for package in packages:
            package._name = f"pypkg-{package._name}"
            package._pretty_name = f"pypkg-{package._pretty_name}"

        return packages

    def package(
        self, name: str, version: str, extras=None
    ) -> poetry_pkg.Package:

        if name.startswith("pypkg-"):
            name = name[len("pypkg-") :]

        try:
            orig_package = super().package(
                name=name, version=version, extras=extras
            )
        except ValueError as e:
            raise af_repository.PackageNotFoundError(
                f"package not found: {name}-{version}"
            ) from e

        pypi_info = self.get_pypi_info(name, version)

        impl_cls = self._pkg_impls.get(name, PythonPackage)

        package = impl_cls(
            f"pypkg-{pypi_info['info']['name']}",
            orig_package.version,
            pretty_version=pypi_info["info"]["version"],
        )

        for attr in (
            "category",
            "optional",
            "python_versions",
            "platform",
            "extras",
            "source_type",
            "source_url",
            "source_reference",
            "description",
            "hashes",
        ):
            v = getattr(orig_package, attr)
            setattr(package, attr, v)

        for dep in orig_package.requires:
            # Some packages like to hard-depend on PyPI version
            # of typing, which is out-of-date at this moment, so
            # filter it out.
            if dep.name == "typing":
                continue
            if not dep.python_constraint.allows_any(
                python_dependency.constraint
            ):
                continue
            if not dep.platform_constraint.matches(platform_constraint):
                continue
            dep._name = f"pypkg-{dep.name}"
            dep._pretty_name = f"pypkg-{dep.pretty_name}"
            package.requires.append(dep)

        package.requires.append(python_dependency)
        package.requires.extend(package.get_requirements())
        package.source = self.get_sdist_source(pypi_info)
        package.source_type = "pypi"
        package.source_url = package.source.url
        package.build_requires = self._get_build_requires(package)

        return package

    def get_package_info(self, name):
        if name.startswith("pypkg-"):
            name = name[len("pypkg-") :]

        return super().get_package_info(name)

    def get_sdist_source(self, pypi_info: dict) -> af_sources.BaseSource:
        sdist_info = self._get_sdist_info(pypi_info)
        source = af_sources.source_for_url(sdist_info["url"])
        md5_digest = sdist_info.get("md5_digest")
        if md5_digest:
            source.add_verification(
                af_sources.HashVerification(
                    algorithm="md5", hash_value=md5_digest
                )
            )
        sha256_digest = sdist_info.get("sha256")
        if sha256_digest:
            source.add_verification(
                af_sources.HashVerification(
                    algorithm="sha256", hash_value=sha256_digest
                )
            )

        return source

    def get_pypi_info(self, name: str, version: str) -> dict:

        if name.startswith("pypkg-"):
            name = name[len("pypkg-") :]
        if self._disable_cache:
            pypi_info = self._get_pypi_info(name, version)
        else:
            pypi_info = self._cache.remember_forever(
                f"{name}:{version}:pypi-info",
                lambda: self._get_pypi_info(name, version),
            )

        return pypi_info

    def _get_pypi_info(self, name: str, version: str) -> dict:
        json_data = self._get(f"pypi/{name}/{version}/json")
        if json_data is None:
            raise af_repository.PackageNotFoundError(
                f"Package [{name}] not found."
            )

        return json_data

    def _get_sdist_info(self, pypi_info: dict) -> dict:

        name = pypi_info["info"]["name"]
        version = pypi_info["info"]["version"]
        sdist_info = None

        try:
            version_info = pypi_info["releases"][version]
        except KeyError:
            version_info = []

        for file_info in version_info:
            if file_info["packagetype"] == "sdist":
                sdist_info = file_info
                break

        if sdist_info is None:
            raise LookupError(f"No sdist URL for {name}")

        return sdist_info

    def _get_build_requires(
        self, package
    ) -> typing.List[poetry_pkg.Dependency]:
        with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as tardir:
            tarball = package.source.tarball(
                package, target_dir=pathlib.Path(tardir), io=self._io
            )

            tmpdir = pathlib.Path(tmpdir)
            af_sources.unpack(tarball, dest=tmpdir, io=self._io)
            return get_build_requires_from_srcdir(package, tmpdir)


def get_build_requires_from_srcdir(package, path):
    build_requires = []

    setup_py = path / "setup.py"
    pyproject_toml = path / "pyproject.toml"

    if pyproject_toml.exists():
        breqs = tools.python.get_build_requires_from_pyproject_toml(
            pyproject_toml
        )

        if not breqs and setup_py.exists():
            breqs = tools.python.get_build_requires_from_setup_py(setup_py)
    elif setup_py.exists():
        breqs = tools.python.get_build_requires_from_setup_py(setup_py)
    else:
        raise LookupError(
            f"No setup.py or pyproject.toml in {package.name} tarball"
        )

    if not breqs and package.name not in {
        "pypkg-wheel",
        "pypkg-setuptools",
    }:
        breqs = ["setuptools >= 40.8.0", "wheel"]

    for breq in breqs:
        dep = python_dependency_from_pep_508(breq)
        build_requires.append(dep)

    build_requires.extend(package.get_build_requirements())

    return build_requires


class BasePythonPackage(base.BasePackage):
    def get_configure_script(self, build) -> str:
        return ""

    def get_build_wheel_env(self, build) -> Dict[str, str]:
        return {}

    def get_build_script(self, build) -> str:
        sdir = build.get_source_dir(self, relative_to="pkgbuild")
        src_python = build.sh_get_command(
            "python", package=self, relative_to="pkgsource"
        )
        build_python = build.sh_get_command("python")
        dest = build.get_temp_root(
            relative_to="pkgbuild"
        ) / build.get_full_install_prefix().relative_to("/")

        sitescript = (
            f"import site; " f'print(site.getsitepackages(["{dest}"])[0])'
        )

        wheeldir_script = 'import pathlib; print(pathlib.Path(".").resolve())'

        pkgname = getattr(self, "dist_name", None)
        if pkgname is None:
            pkgname = self.name
            if pkgname.startswith("pypkg-"):
                pkgname = pkgname[len("pypkg-") :]

        env = {
            "SETUPTOOLS_SCM_PRETEND_VERSION": self.pretty_version,
        }

        if pkgname == "wheel":
            build_command = f'"{src_python}" setup.py sdist -d ${{_wheeldir}}'
            binary = False
        else:
            args = [
                src_python,
                "-m",
                "pip",
                "wheel",
                "--verbose",
                "--wheel-dir",
                "${_wheeldir}",
                "--no-binary=:all:",
                "--no-build-isolation",
                "--use-feature=in-tree-build",
                "--no-deps",
                ".",
            ]
            build_command = " ".join(
                shlex.quote(c) if c[0] != "$" else c for c in args
            )
            env.update(self.get_build_wheel_env(build))

            cflags = build.sh_get_bundled_shlibs_cflags(
                build.get_packages(dep.name for dep in self.build_requires),
                relative_to="pkgsource",
            )

            if cflags:
                if "CFLAGS" in env:
                    env["CFLAGS"] = f"!{cflags}' '{env['CFLAGS']}"
                else:
                    env["CFLAGS"] = f"!{cflags}"

            ldflags = build.sh_get_bundled_shlibs_ldflags(
                build.get_packages(dep.name for dep in self.build_requires),
                relative_to="pkgsource",
            )

            if ldflags:
                if "LDFLAGS" in env:
                    env["LDFLAGS"] = f"!{ldflags}' '{env['CFLAGS']}"
                else:
                    env["LDFLAGS"] = f"!{ldflags}"

            binary = True

        env_str = build.sh_format_command("env", env, force_args_eq=True)

        return textwrap.dedent(
            f"""\
            _wheeldir=$("{build_python}" -c '{wheeldir_script}')
            _target=$("{build_python}" -c '{sitescript}')
            (cd "{sdir}"; \\
             {env_str} \\
                 {build_command})
            "{build_python}" -m pip install \\
                --no-build-isolation \\
                --no-warn-script-location \\
                --no-index \\
                --no-deps \\
                --upgrade \\
                -f "file://${{_wheeldir}}" \\
                {'--only-binary' if binary else '--no-binary'} :all: \\
                --target "${{_target}}" \\
                "{pkgname}"
        """
        )

    def get_build_install_script(self, build) -> str:
        common_script = super().get_build_install_script(build)

        python = build.sh_get_command("python", package=self)
        root = build.get_install_dir(self, relative_to="pkgbuild")
        wheeldir_script = 'import pathlib; print(pathlib.Path(".").resolve())'

        pkgname = getattr(self, "dist_name", None)
        if pkgname is None:
            pkgname = self.name
            if pkgname.startswith("pypkg-"):
                pkgname = pkgname[len("pypkg-") :]

        if pkgname == "wheel":
            binary = False
        else:
            binary = True

        wheel_install = textwrap.dedent(
            f"""\
            _wheeldir=$("{python}" -c '{wheeldir_script}')
            "{python}" -m pip install \\
                --no-build-isolation \\
                --ignore-installed \\
                --no-index \\
                --no-deps \\
                --upgrade \\
                --force-reinstall \\
                --no-warn-script-location -f "file://${{_wheeldir}}" \\
                {'--only-binary' if binary else '--no-binary'} :all: \\
                --root "{root}" \\
                "{pkgname}"
        """
        )

        if common_script:
            return f"{common_script}\n{wheel_install}"
        else:
            return wheel_install

    def get_install_script(self, build) -> str:
        return ""

    def get_install_list_script(self, build) -> str:
        common_script = super().get_install_list_script(build)

        prefix = build.get_full_install_prefix()
        dest = build.get_install_dir(self, relative_to="pkgbuild")

        pkgname = getattr(self, "dist_name", None)
        if pkgname is None:
            pkgname = self.pretty_name
            if pkgname.startswith("pypkg-"):
                pkgname = pkgname[len("pypkg-") :]
        dist_name = pkgname.replace("-", "_")

        pyscript = textwrap.dedent(
            f"""\
            import pathlib
            import site

            sitepackages = pathlib.Path(site.getsitepackages(["{prefix}"])[0])
            abs_sitepackages = (
                pathlib.Path("{dest}") /
                sitepackages.relative_to('/')
            )

            record = (
                abs_sitepackages /
                f'{dist_name}-{self.pretty_version}.dist-info' /
                'RECORD'
            )

            if not record.exists():
                raise RuntimeError(f'no wheel RECORD for {pkgname}')

            with open(record) as f:
                for entry in f:
                    filename = entry.split(',')[0]
                    install_path = (sitepackages / filename).resolve()
                    print(install_path.relative_to('/'))
        """
        )

        scriptfile_name = f"_gen_install_list_from_wheel_{self.unique_name}.py"

        wheel_files = build.sh_write_python_helper(
            scriptfile_name, pyscript, relative_to="pkgbuild"
        )

        if common_script:
            return f"{common_script}\n{wheel_files}"
        else:
            return wheel_files


class PythonPackage(BasePythonPackage):
    def get_sources(self) -> typing.List[af_sources.BaseSource]:
        if getattr(self, "source", None) is None:
            raise RuntimeError(f"no source information for {self!r}")

        return [self.source]

    def __repr__(self):
        return "<PythonPackage {}>".format(self.unique_name)


class BundledPythonPackage(BasePythonPackage, base.BundledPackage):
    @classmethod
    def get_package_repository(cls, target, io):
        return PyPiRepository(io=io)

    @classmethod
    def resolve(cls, io, *, ref=None, version=None) -> "BundledPythonPackage":
        repo_dir = cls.resolve_vcs_source(io, ref=ref)
        setup_py = repo_dir / "setup.py"

        if not setup_py.exists():
            raise RuntimeError(f"{repo_dir}/setup.py does not exist")

        dist = tools.python.get_dist(repo_dir)

        requires = []
        for req in dist.metadata.run_requires:
            dep = python_dependency_from_pep_508(req)
            requires.append(dep)

        if version is None:
            version = dist.version

        package = cls(version, requires=requires, source_version=ref or "HEAD")
        package.dist_name = dist.name
        package.build_requires = get_build_requires_from_srcdir(
            package, repo_dir
        )

        return package

    def get_requirements(self) -> typing.List[poetry_pkg.Dependency]:
        reqs = super().get_requirements()
        reqs.append(python_dependency)
        return reqs

    def get_build_requirements(self) -> typing.List[poetry_pkg.Dependency]:
        reqs = super().get_build_requirements()
        reqs.append(python_dependency)
        return reqs

    def get_install_list_script(self, build) -> str:
        static_list = base.BundledPackage.get_install_list_script(self, build)
        wheel_list = super().get_install_list_script(build)

        if static_list:
            return f"{static_list}\n{wheel_list}"
        else:
            return wheel_list

    def clone(self):
        clone = super().clone()
        dist_name = getattr(self, "dist_name", None)
        if dist_name:
            clone.dist_name = dist_name
        return clone
