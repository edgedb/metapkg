from __future__ import annotations
from typing import *

from metapkg.packages import repository
from metapkg.targets import generic

from . import build as winbuild

if TYPE_CHECKING:
    from poetry.core.packages import package as poetry_pkg
    from poetry.core.packages import dependency as poetry_dep


class WindowsRepository(repository.Repository):
    def find_packages(
        self,
        dependency: poetry_dep.Dependency,
    ) -> list[poetry_pkg.Package]:
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
