from __future__ import annotations
from typing import (
    TYPE_CHECKING,
)

import pathlib

from poetry.repositories import repository as poetry_repo

from metapkg.targets import base as targets
from metapkg.targets import package as tgt_pkg

from . import build as genbuild

if TYPE_CHECKING:
    from poetry.core.packages import package as poetry_pkg
    from poetry.core.packages import dependency as poetry_dep


Build = genbuild.Build


class GenericOSRepository(poetry_repo.Repository):
    def __init__(
        self, name: str, packages: list[poetry_pkg.Package] | None = None
    ) -> None:
        super().__init__(name, packages)
        self._pkg_impls: dict[str, type[tgt_pkg.SystemPackage]] = {}

    def list_provided_packages(self) -> frozenset[str]:
        # A list of packages assumed to be present on the system.
        return frozenset(
            (
                "bison",
                "flex",
                "perl",
            )
        )

    def register_package_impl(
        self,
        name: str,
        impl_cls: type[tgt_pkg.SystemPackage],
    ) -> None:
        self._pkg_impls[name] = impl_cls

    def find_packages(
        self,
        dependency: poetry_dep.Dependency,
    ) -> list[poetry_pkg.Package]:
        if dependency.name in self.list_provided_packages():
            impl_cls = self._pkg_impls.get(
                dependency.name, tgt_pkg.SystemPackage
            )
            pkg = impl_cls(
                dependency.name,
                version="999.0",
                pretty_version="999.0",
                system_name=dependency.name,
            )
            self.add_package(pkg)

            return [pkg]
        else:
            return []


class GenericTarget(targets.FHSTarget):
    @property
    def name(self) -> str:
        return "Generic POSIX"

    def get_package_repository(self) -> GenericOSRepository:
        return GenericOSRepository("generic")

    def get_bundle_install_root(self, build: targets.Build) -> pathlib.Path:
        return pathlib.Path("/opt")

    def get_bundle_install_subdir(self, build: targets.Build) -> pathlib.Path:
        return build.root_package.get_root_install_subdir(build)

    def get_install_path(
        self,
        build: targets.Build,
        root: pathlib.Path,
        root_subdir: pathlib.Path,
        prefix: pathlib.Path,
        aspect: targets.InstallAspect,
    ) -> pathlib.Path:
        if aspect == "sysconf":
            return prefix / "etc"
        elif aspect == "userconf":
            return pathlib.Path("$HOME") / ".config"
        elif aspect == "data":
            return prefix / "share"
        elif aspect == "legal":
            return prefix / "licenses"
        elif aspect == "doc":
            return prefix / "share" / "doc" / root_subdir
        elif aspect == "info":
            return prefix / "share" / "info"
        elif aspect == "man":
            return prefix / "share" / "man"
        elif aspect == "bin":
            return prefix / "bin"
        elif aspect == "systembin":
            if root == pathlib.Path("/"):
                return root / "usr" / "bin"
            else:
                return root / "bin"
        elif aspect == "lib":
            return prefix / "lib"
        elif aspect == "include":
            return prefix / "include"
        elif aspect == "localstate":
            return root / "var"
        elif aspect == "runstate":
            return root / "var" / "run"
        else:
            raise LookupError(f"aspect: {aspect}")

    def get_builder(self) -> type[genbuild.Build]:
        return genbuild.Build
