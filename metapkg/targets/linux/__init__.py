from __future__ import annotations

import distro

from metapkg.targets import base as targets
from metapkg.targets import generic
from metapkg.targets import deb
from metapkg.targets import rpm

from . import build as linbuild


class LinuxGenericTarget(generic.GenericTarget, targets.LinuxTarget):
    @property
    def name(self) -> str:
        return f"Generic Linux ({self.libc} libc)"

    @property
    def ident(self) -> str:
        return f"{self.libc}linux"

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
    def __init__(self, arch: str) -> None:
        super().__init__(arch, libc="musl")


class LinuxPortableMuslTarget(LinuxMuslTarget, LinuxGenericTarget):
    pass


def get_specific_target(
    libc: str, arch: str, portable: bool
) -> targets.LinuxTarget:
    target: targets.LinuxTarget

    if portable:
        if libc == "musl":
            target = LinuxPortableMuslTarget(arch)
        elif libc == "gnu":
            target = LinuxGenericTarget(arch, libc)
        else:
            raise RuntimeError(f"Unsupported libc: {libc}")
    else:
        distro_info = distro.info()
        like = distro_info["like"]
        if not like:
            like = distro_info["id"]

        like_set = set(like.split(" "))

        if like_set & {"rhel", "fedora", "centos", "amzn"}:
            target = rpm.get_specific_target(distro_info, arch, libc)
        elif like_set & {"debian", "ubuntu"}:
            target = deb.get_specific_target(distro_info, arch, libc)
        else:
            raise RuntimeError(
                f"Linux distro not supported: {distro_info['id']}, use "
                f"--generic to build a generic portable build"
            )

    return target
