from __future__ import annotations
from typing import (
    TYPE_CHECKING,
    Any,
    Type,
    TypeVar,
)

import copy
import pathlib
import shlex
import tempfile
import textwrap

from poetry.core.packages import dependency as poetry_dep
from poetry.core.packages import package as poetry_pkg
from poetry.core.semver import version as poetry_version
from poetry.repositories import pypi_repository
from poetry.repositories import exceptions as poetry_repo_exc

import build as pypa_build
import build.env as pypa_build_env
import pep517

import distlib.database

from metapkg import packages as mpkg
from metapkg import targets

from . import base
from . import sources as af_sources
from . import repository
from .utils import python_dependency_from_pep_508

import packaging.utils


if TYPE_CHECKING:
    from cleo.io import io as cleo_io

python_dependency = poetry_dep.Dependency(name="python", constraint=">=3.7")
wheel_dependency = poetry_dep.Dependency(name="pypkg-wheel", constraint="*")


def set_python_runtime_dependency(dep: poetry_dep.Dependency) -> None:
    global python_dependency
    python_dependency = dep


class PyPiRepository(pypi_repository.PyPiRepository):
    def __init__(self, io: cleo_io.IO) -> None:
        super().__init__()
        self._io = io
        self._pkg_impls: dict[str, type[PythonPackage]] = {
            "flit-core": FlitCore,
            "tomli": Tomli,
        }

    def register_package_impl(
        self,
        name: str,
        impl_cls: type[PythonPackage],
    ) -> None:
        self._pkg_impls[name] = impl_cls

    def find_packages(
        self,
        dependency: poetry_dep.Dependency,
    ) -> list[poetry_pkg.Package]:

        if dependency.name.startswith("pypkg-"):
            pep508 = dependency.to_pep_508().replace("pypkg-", "")
            dependency = type(dependency).create_from_pep_508(pep508)
        else:
            return []

        packages = super().find_packages(dependency)

        for package in packages:
            package._name = packaging.utils.canonicalize_name(
                f"pypkg-{package._name}"
            )
            package._pretty_name = f"pypkg-{package._pretty_name}"

        return packages

    def package(
        self,
        name: str,
        version: poetry_version.Version,
        extras: list[str] | None = None,
    ) -> poetry_pkg.Package:

        if name.startswith("pypkg-"):
            name = name[len("pypkg-") :]

        try:
            orig_package = super().package(
                name=name, version=version, extras=extras
            )
        except ValueError as e:
            raise poetry_repo_exc.PackageNotFound(
                f"Package {name} ({version}) not found."
            ) from e

        pypi_info = self.get_pypi_info(name, version)
        source = self.get_sdist_source(pypi_info)

        impl_cls = self._pkg_impls.get(name, PythonPackage)

        package = impl_cls(
            f"pypkg-{pypi_info['info']['name']}",
            orig_package.version,
            pretty_version=pypi_info["info"]["version"],
            source_type="pypi",
            source_url=source.url,
        )

        package.__dict__.update(
            {
                k: copy.deepcopy(v)
                for k, v in orig_package.__dict__.items()
                if k
                not in {"_name", "_pretty_name", "_source_url", "_source_type"}
            }
        )

        for dep in list(package.requires):
            # Some packages like to hard-depend on PyPI version
            # of typing, which is out-of-date at this moment, so
            # filter it out.
            if dep.name == "typing":
                continue
            if not dep.python_constraint.allows_any(
                python_dependency.constraint
            ):
                continue
            dep._name = packaging.utils.canonicalize_name(f"pypkg-{dep.name}")
            dep._pretty_name = f"pypkg-{dep.pretty_name}"
            package.add_dependency(dep)

        package.add_dependency(python_dependency)
        for req in package.get_requirements():
            package.add_dependency(req)
        package.source = self.get_sdist_source(pypi_info)

        if self._disable_cache:
            build_reqs = self._get_build_requires(package)
        else:
            build_reqs = self._cache.remember_forever(
                f"{package.unique_name}:build-requirements",
                lambda: self._get_build_requires(package),
            )

        if package.name == "pypkg-setuptools":
            build_reqs.append(wheel_dependency.to_pep_508())

        repository.set_build_requirements(
            package,
            [
                poetry_dep.Dependency.create_from_pep_508(req)
                for req in build_reqs
            ],
        )

        return package

    def get_package_info(
        self, name: packaging.utils.NormalizedName
    ) -> dict[str, Any]:
        if name.startswith("pypkg-"):
            name = packaging.utils.canonicalize_name(name[len("pypkg-") :])

        return super().get_package_info(name)

    def get_sdist_source(
        self, pypi_info: dict[str, Any]
    ) -> af_sources.BaseSource:
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

    def get_pypi_info(
        self, name: str, version: poetry_version.Version
    ) -> dict[str, Any]:
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

    def _get_pypi_info(
        self, name: str, version: poetry_version.Version
    ) -> dict[str, Any]:
        json_data = self._get(f"pypi/{name}/{version}/json")
        if json_data is None:
            raise poetry_repo_exc.PackageNotFound(
                f"Package {name} ({version}) not found."
            )
        else:
            return json_data

    def _get_sdist_info(self, pypi_info: dict[str, Any]) -> dict[str, Any]:
        name = pypi_info["info"]["name"]
        sdist_info = None

        for file_info in pypi_info["urls"]:
            if file_info["packagetype"] == "sdist":
                sdist_info = file_info
                break

        if sdist_info is None:
            raise LookupError(f"No sdist URL for {name}")

        return sdist_info  # type: ignore

    def _get_build_requires(
        self,
        package: BasePythonPackage,
    ) -> list[str]:
        with tempfile.TemporaryDirectory() as t:
            tmpdir = pathlib.Path(t)
            package.source.copy(tmpdir, io=self._io)
            reqs = get_build_requires_from_srcdir(package, tmpdir)

        return [req.to_pep_508() for req in reqs]


def get_dist(
    srcdir: pathlib.Path,
) -> distlib.database.InstalledDistribution:
    builder = pypa_build.ProjectBuilder(
        srcdir,
        runner=pep517.default_subprocess_runner,
    )

    with pypa_build_env.IsolatedEnvBuilder() as env:
        builder.python_executable = env.executable
        builder.scripts_dir = env.scripts_dir
        env.install(builder.build_system_requires)
        env.install(builder.get_requires_for_build("wheel"))
        with tempfile.TemporaryDirectory() as tmpdir:
            distinfo = builder.metadata_path(tmpdir)
            return distlib.database.InstalledDistribution(distinfo)


def get_build_requires_from_srcdir(
    package: mpkg.BasePackage,
    path: pathlib.Path,
) -> list[poetry_dep.Dependency]:
    builder = pypa_build.ProjectBuilder(
        path,
        runner=pep517.quiet_subprocess_runner,
    )

    with pypa_build_env.IsolatedEnvBuilder() as env:
        builder.python_executable = env.executable
        builder.scripts_dir = env.scripts_dir
        sys_reqs = builder.build_system_requires
        env.install(sys_reqs)
        pkg_reqs = builder.get_requires_for_build("wheel")

    deps = []
    for req in sys_reqs | pkg_reqs:
        dep = python_dependency_from_pep_508(req)
        # Make sure "wheel" is not a dependency of itself and
        # also elide setuptools, because it is always installed.
        if dep.name == "pypkg-setuptools" or (
            package.name in {"pypkg-wheel", "pypkg-setuptools"}
            and dep.name == "pypkg-wheel"
        ):
            dep.deactivate()

        if dep.is_activated():
            deps.append(dep)

    deps.extend(package.get_build_requirements())

    return deps


class BasePythonPackage(base.BasePackage):
    source: af_sources.BaseSource

    def get_configure_script(self, build: targets.Build) -> str:
        return ""

    def sh_get_build_wheel_env(
        self, build: targets.Build, *, site_packages_var: str
    ) -> dict[str, str]:
        return {}

    def get_build_script(self, build: targets.Build) -> str:
        sdir = build.get_source_dir(self, relative_to="pkgbuild")
        src_python = build.sh_get_command(
            "python", package=self, relative_to="pkgsource"
        )
        build_python = build.sh_get_command("python")
        dest = build.get_temp_root(
            relative_to="pkgbuild"
        ) / build.get_full_install_prefix().relative_to("/")

        sitescript = f'import site; print(site.getsitepackages(["{dest}"])[0])'

        src_dest = build.get_temp_root(
            relative_to="pkgsource"
        ) / build.get_full_install_prefix().relative_to("/")

        src_sitescript = (
            f'import site; print(site.getsitepackages(["{src_dest}"])[0])'
        )

        wheeldir_script = 'import pathlib; print(pathlib.Path(".").resolve())'

        abspath = (
            "import pathlib, sys; print(pathlib.Path(sys.argv[1]).resolve())"
        )

        pkgname = getattr(self, "dist_name", None)
        if pkgname is None:
            pkgname = self.name
            if pkgname.startswith("pypkg-"):
                pkgname = pkgname[len("pypkg-") :]

        env = build.sh_append_global_flags(
            {
                "SETUPTOOLS_SCM_PRETEND_VERSION": self.pretty_version,
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            }
        )

        dep_names = [dep.name for dep in base.get_build_requirements(self)]
        build_deps = build.get_packages(dep_names)

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
                "--no-deps",
                ".",
            ]
            build_command = " ".join(
                shlex.quote(c) if c[0] != "$" else c for c in args
            )
            env.update(
                self.sh_get_build_wheel_env(
                    build, site_packages_var="${_sitepkg_from_src}"
                )
            )

            cflags = build.sh_get_bundled_shlibs_cflags(
                build_deps,
                relative_to="pkgsource",
            )

            if cflags:
                build.sh_append_flags(env, "CFLAGS", cflags)

            ldflags = build.sh_get_bundled_shlibs_ldflags(
                build_deps,
                relative_to="pkgsource",
            )

            if ldflags:
                build.sh_append_flags(env, "LDFLAGS", ldflags)

            binary = True

        all_build_deps = build.get_packages(dep_names, recursive=True)
        env_str = build.sh_format_command("env", env, force_args_eq=True)
        env_str += " " + " ".join(build.get_ld_env(all_build_deps, "${_wd}"))

        build_cmds = [build_command]
        build_cmds.extend(self.get_extra_python_build_commands(build))

        build_command = "\n".join(
            f"{env_str} \\\n{textwrap.indent(cmd, ' ' * 4)}"
            for cmd in build_cmds
        )

        return textwrap.dedent(
            f"""\
            _wheeldir=$("{build_python}" -c '{wheeldir_script}')
            _target=$("{build_python}" -c '{sitescript}')
            _sitepkg_from_src=$("{build_python}" -c '{src_sitescript}')
            _wd=$("{build_python}" -c '{abspath}' "$(pwd)")
            (
                cd "{sdir}"
                {textwrap.indent(build_command, ' ' * 16)}
            )
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

    def get_extra_python_build_commands(
        self,
        build: targets.Build,
    ) -> list[str]:
        return []

    def get_build_install_script(self, build: targets.Build) -> str:
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

        env = {
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        }

        env_str = build.sh_format_command("env", env, force_args_eq=True)

        wheel_install = textwrap.dedent(
            f"""\
            _wheeldir=$("{python}" -c '{wheeldir_script}')
            {env_str} \\
            "{python}" -m pip install \\
                --no-build-isolation \\
                --ignore-installed \\
                --no-index \\
                --no-deps \\
                --upgrade \\
                --force-reinstall \\
                --no-warn-script-location -f "file://${{_wheeldir}}" \\
                {'--only-binary' if binary else '--no-binary'} :all: \\
                --root "$(pwd -P)/{root}" \\
                "{pkgname}"
        """
        )

        if common_script:
            return f"{common_script}\n{wheel_install}"
        else:
            return wheel_install

    def get_install_list_script(self, build: targets.Build) -> str:
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
    def get_sources(self) -> list[af_sources.BaseSource]:
        if getattr(self, "source", None) is None:
            raise RuntimeError(f"no source information for {self!r}")

        return [self.source]

    def get_cyclic_runtime_deps(self) -> frozenset[str]:
        return frozenset()

    def __repr__(self) -> str:
        return "<PythonPackage {}>".format(self.unique_name)


BundledPythonPackage_T = TypeVar(
    "BundledPythonPackage_T", bound="BundledPythonPackage"
)


class BundledPythonPackage(BasePythonPackage, base.BundledPackage):
    dist_name: str

    @classmethod
    def get_package_repository(
        cls, target: targets.Target, io: cleo_io.IO
    ) -> PyPiRepository:
        return PyPiRepository(io=io)

    @classmethod
    def resolve(
        cls: Type[BundledPythonPackage_T],
        io: cleo_io.IO,
        *,
        version: str | None = None,
        revision: str | None = None,
        is_release: bool = False,
        target: targets.Target,
    ) -> BundledPythonPackage_T:
        repo = cls.resolve_vcs_repo(io, version)
        repo_dir = repo.work_tree
        dist = get_dist(repo_dir)

        requires = []
        for req in dist.metadata.run_requires:
            dep = python_dependency_from_pep_508(req)
            requires.append(dep)

        pretty_version = dist.version
        ver = cls.canonicalize_version(
            io,
            version=poetry_version.Version.parse(pretty_version),
            revision=revision,
            is_release=is_release,
            target=target,
        )

        package = cls(
            ver,
            pretty_version=pretty_version,
            requires=requires,
            source_version=cls.resolve_vcs_version(io, repo),
        )
        package.dist_name = dist.name
        repository.set_build_requirements(
            package,
            get_build_requires_from_srcdir(package, repo_dir),
        )

        return package

    @classmethod
    def canonicalize_version(
        cls,
        io: cleo_io.IO,
        version: poetry_version.Version,
        *,
        revision: str | None = None,
        is_release: bool = False,
        target: targets.Target,
    ) -> poetry_version.Version:
        return version

    def get_requirements(self) -> list[poetry_dep.Dependency]:
        reqs = super().get_requirements()
        reqs.append(python_dependency)
        return reqs

    def get_build_requirements(self) -> list[poetry_dep.Dependency]:
        reqs = super().get_build_requirements()
        reqs.append(python_dependency)
        return reqs

    def get_install_list_script(self, build: targets.Build) -> str:
        static_list = base.BundledPackage.get_install_list_script(self, build)
        wheel_list = super().get_install_list_script(build)

        if static_list:
            return f"{static_list}\n{wheel_list}"
        else:
            return wheel_list


class FlitCore(PythonPackage):
    def get_cyclic_runtime_deps(self) -> frozenset[str]:
        return frozenset({"pypkg-tomli"})


class Tomli(PythonPackage):
    def sh_get_build_wheel_env(
        self, build: targets.Build, *, site_packages_var: str
    ) -> dict[str, str]:
        env = dict(
            super().sh_get_build_wheel_env(
                build, site_packages_var=site_packages_var
            )
        )
        sdir = build.get_source_dir(self, relative_to="pkgbuild")
        env["EXTRA_PYTHONPATH"] = str(sdir)
        return env
