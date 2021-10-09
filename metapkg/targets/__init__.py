from __future__ import annotations
from typing import TYPE_CHECKING

import distro
import platform

from .base import (
    Build,
    Target,
    Location,
    LinuxTarget,
    EnsureDirAction,
    AddUserAction,
)
from .package import SystemPackage

from . import deb, rpm, macos, generic, win  # noqa

if TYPE_CHECKING:
    from cleo.io.io import IO


__all__ = (
    "Build",
    "EnsureDirAction",
    "AddUserAction",
    "Location",
    "Target",
    "LinuxTarget",
    "SystemPackage",
)


def detect_target(io: IO) -> Target:
    target: Target
    system = platform.system()

    if system == "Linux":
        distro_info = distro.info()
        like = distro_info["like"]
        if not like:
            like = distro_info["id"]

        like_set = set(like.split(" "))

        if like_set & {"rhel", "fedora", "centos"}:
            target = rpm.get_specific_target(distro_info)
        elif like_set & {"debian", "ubuntu"}:
            target = deb.get_specific_target(distro_info)
        else:
            raise RuntimeError(
                f'Linux distro not supported: {distro_info["id"]}'
            )

    elif system == "Darwin":
        v, _, _ = platform.mac_ver()
        version = tuple(int(p) for p in v.split("."))
        target = macos.get_specific_target(version)

    elif system == "Windows":
        v = platform.version()
        version = tuple(int(p) for p in v.split("."))
        target = win.get_specific_target(version)

    else:
        raise RuntimeError(f"System not supported: {system}")

    return target
