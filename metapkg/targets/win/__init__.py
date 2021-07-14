from __future__ import annotations
from typing import *  # noqa

from poetry import packages
from poetry import semver

from metapkg.packages import repository
from metapkg.targets import generic

from . import build as winbuild


class WindowsRepository(repository.Repository):
    def find_packages(
        self,
        name: str,
        constraint: Optional[Union[semver.VersionConstraint, str]] = None,
        extras: Optional[list] = None,
        allow_prereleases: bool = False,
    ) -> List[packages.Package]:
        return []


class WindowsTarget(generic.GenericTarget):
    def __init__(self, version):
        self.version = version

    @property
    def name(self):
        return f'Windows {".".join(str(v) for v in self.version)}'

    def get_package_system_ident(
        self, build, package, include_slot: bool = False
    ):
        if include_slot:
            return f"{package.identifier}{package.slot_suffix}"
        else:
            return package.identifier

    def get_package_repository(self):
        return WindowsRepository()

    def get_exe_suffix(self) -> str:
        return ".exe"


class ModernWindowsTarget(WindowsTarget):
    def build(self, **kwargs):
        return winbuild.Build(self, **kwargs).run()


def get_specific_target(version):

    if version >= (10, 0):
        return ModernWindowsTarget(version)
    else:
        raise NotImplementedError(
            f'Windows version {".".join(version)} is not supported'
        )
