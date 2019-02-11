import pathlib
import typing

from poetry import packages
from poetry import semver

from metapkg.packages import repository
from metapkg.targets import base as targets
from metapkg.targets.package import SystemPackage

from .build import Build


PACKAGE_WHITELIST = [
    'bison',
    'flex',
    'perl',
    'uuid',
    'zlib',
]


class GenericRepository(repository.Repository):

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


class GenericTarget(targets.FHSTarget):

    def __init__(self):
        pass

    @property
    def name(self):
        return f'Generic POSIX'

    def get_package_repository(self):
        return GenericRepository()

    def get_install_root(self, build) -> pathlib.Path:
        return pathlib.Path('/usr/local')

    def get_install_prefix(self, build) -> pathlib.Path:
        return pathlib.Path('lib') / build.root_package.name

    def get_install_path(self, build, aspect) -> pathlib.Path:
        root = self.get_install_root(build)
        prefix = self.get_install_prefix(build)

        if aspect == 'sysconf':
            return root / 'etc'
        elif aspect == 'data':
            return root / 'share' / build.root_package.name
        elif aspect == 'bin':
            return root / prefix / 'bin'
        elif aspect == 'lib':
            return root / prefix / 'lib'
        elif aspect == 'include':
            return root / 'include' / build.root_package.name
        elif aspect == 'localstate':
            return pathlib.Path('/var')
        elif aspect == 'runstate':
            return pathlib.Path('/run')
        else:
            raise LookupError(f'aspect: {aspect}')

    def build(self, root_pkg, deps, build_deps, io, workdir, outputdir):
        return Build(
            self, io, root_pkg, deps, build_deps, workdir, outputdir).run()

    def service_scripts_for_package(self, build, package) -> dict:
        return {}
