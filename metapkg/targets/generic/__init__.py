from __future__ import annotations
from typing import *

import pathlib

from metapkg.packages import repository
from metapkg.targets import base as targets
from metapkg.targets.package import SystemPackage

from . import build as genbuild

if TYPE_CHECKING:
    from poetry.core.packages import dependency as poetry_dep
    from poetry.core.packages import package as poetry_pkg


Build = genbuild.Build


PACKAGE_WHITELIST = [
    "bison",
    "flex",
    "pam",
    "pam-dev",
    "perl",
]


class GenericRepository(repository.Repository):
    def find_packages(
        self,
        dependency: poetry_dep.Dependency,
    ) -> list[poetry_pkg.Package]:

        if dependency.name in PACKAGE_WHITELIST:
            pkg = SystemPackage(
                dependency.name,
                version="1.0",
                pretty_version="1.0",
                system_name=dependency.name,
            )
            self.add_package(pkg)

            return [pkg]
        else:
            return []


class GenericTarget(targets.FHSTarget):
    def __init__(self) -> None:
        pass

    @property
    def name(self) -> str:
        return f"Generic POSIX"

    def get_package_repository(self) -> GenericRepository:
        return GenericRepository()

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


class GenericLinuxTarget(GenericTarget, targets.LinuxTarget):
    @property
    def name(self) -> str:
        return f"Generic Linux"

    def get_global_cflags(self, build: targets.Build) -> list[str]:
        flags = super().get_global_cflags(build)
        return flags + [
            "-g",
            "-O2",
            "-D_FORTIFY_SOURCE=2",
            "-fstack-protector-strong",
            "-Wdate-time",
            "-Wformat",
            "-Werror=format-security",
        ]

    def get_global_ldflags(self, build: targets.Build) -> list[str]:
        flags = super().get_global_ldflags(build)
        return flags + [
            "-static-libgcc",
            "-static-libstdc++",
            "-Wl,--as-needed,-zorigin,--disable-new-dtags,-zrelro,-znow",
        ]

    def get_builder(self) -> type[genbuild.GenericLinuxBuild]:
        return genbuild.GenericLinuxBuild

    def is_portable(self) -> bool:
        return True


class GenericMuslLinuxTarget(GenericLinuxTarget):
    @property
    def name(self) -> str:
        return f"Generic Linux (musl)"
