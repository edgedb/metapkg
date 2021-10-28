from __future__ import annotations
from typing import *

import pathlib

from metapkg.packages import repository
from metapkg.targets import base as targets
from metapkg.targets import generic
from metapkg.targets.package import SystemPackage

from . import build as linbuild

if TYPE_CHECKING:
    from poetry.core.packages import dependency as poetry_dep
    from poetry.core.packages import package as poetry_pkg


class LinuxGenericTarget(generic.GenericTarget, targets.LinuxTarget):
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

    def get_builder(self) -> type[linbuild.GenericLinuxBuild]:
        return linbuild.GenericLinuxBuild

    def is_portable(self) -> bool:
        return True


class LinuxMuslTarget(targets.LinuxTarget):
    pass


class LinuxPortableMuslTarget(LinuxGenericTarget, LinuxMuslTarget):
    @property
    def name(self) -> str:
        return f"Generic Linux (musl)"
