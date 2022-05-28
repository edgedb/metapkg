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
    def list_provided_packages(self) -> frozenset[str]:
        # A list of packages assumed to be present on the system.
        return frozenset(
            (
                "bison",
                "flex",
                "perl",
            )
        )

    def find_packages(
        self,
        dependency: poetry_dep.Dependency,
    ) -> list[poetry_pkg.Package]:

        if dependency.name in self.list_provided_packages():
            pkg = tgt_pkg.SystemPackage(
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
        return f"Generic POSIX"

    def get_package_repository(self) -> GenericOSRepository:
        return GenericOSRepository()

    def get_install_root(self, build: targets.Build) -> pathlib.Path:
        return pathlib.Path("/opt")

    def get_install_prefix(self, build: targets.Build) -> pathlib.Path:
        return pathlib.Path(build.root_package.name_slot)

    def get_install_path(
        self, build: targets.Build, aspect: str
    ) -> pathlib.Path:
        root = self.get_install_root(build)
        prefix = self.get_install_prefix(build)

        if aspect == "sysconf":
            return root / "etc"
        elif aspect == "userconf":
            return pathlib.Path("$HOME") / ".config"
        elif aspect == "data":
            return root / prefix / "data"
        elif aspect == "legal":
            return root / prefix / "licenses"
        elif aspect == "bin":
            return root / prefix / "bin"
        elif aspect == "systembin":
            if root == pathlib.Path("/"):
                return root / "usr" / "bin"
            else:
                return root / "bin"
        elif aspect == "lib":
            return root / prefix / "lib"
        elif aspect == "include":
            return root / prefix / "include"
        elif aspect == "localstate":
            return root / "var"
        elif aspect == "runstate":
            return root / "var" / "run"
        else:
            raise LookupError(f"aspect: {aspect}")

    def get_builder(self) -> type[genbuild.Build]:
        return genbuild.Build
