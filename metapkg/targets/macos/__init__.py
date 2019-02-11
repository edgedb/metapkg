import pathlib
import typing

from poetry import packages
from poetry import semver

from metapkg.packages import repository
from metapkg.targets import generic
from metapkg.targets.package import SystemPackage

from . import build as macbuild


PACKAGE_WHITELIST = [
    'bison',
    'flex',
    'perl',
    'pam',
    'uuid',
    'zlib',
]


class MacOSRepository(repository.Repository):

    def find_packages(
            self,
            name: str,
            constraint: typing.Optional[
                typing.Union[semver.VersionConstraint, str]
            ] = None,
            extras: typing.Optional[list] = None,
            allow_prereleases: bool = False) \
            -> typing.List[packages.Package]:

        if name in PACKAGE_WHITELIST:
            pkg = SystemPackage(
                name, version='1.0', pretty_version='1.0', system_name=name)
            self.add_package(pkg)

            return [pkg]
        else:
            return []


class MacOSTarget(generic.GenericTarget):

    def __init__(self, version):
        self.version = version

    @property
    def name(self):
        return f'macOS {".".join(str(v) for v in self.version)}'

    def get_package_repository(self):
        return MacOSRepository()

    def get_install_root(self, build) -> pathlib.Path:
        rpkg = build.root_package
        return pathlib.Path(
            f'/Library/Frameworks/{rpkg.title}.framework/{rpkg.version.major}')


class ModernMacOSTarget(MacOSTarget):

    def build(self, root_pkg, deps, build_deps, io, workdir, outputdir):
        return macbuild.Build(
            self, io, root_pkg, deps, build_deps, workdir, outputdir).run()


def get_specific_target(version):

    if version >= (10, 10):
        return ModernMacOSTarget(version)
    else:
        raise NotImplementedError(
            f'macOS version {".".join(version)} is not supported')
