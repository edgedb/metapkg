import pathlib
import re
import subprocess
import textwrap
import typing

from poetry import packages
from poetry import semver

from metapkg import tools
from metapkg.packages import repository
from metapkg.targets import base as targets
from metapkg.targets.package import SystemPackage

from . import build as debuild


PACKAGE_MAP = {
    'icu': 'libicu-dev',
    'zlib': 'zlib1g-dev',
    'libxslt': 'libxslt1-dev',
    'pam': 'libpam0g-dev',
    'python': 'python3',
    'uuid': 'uuid-dev',
    'systemd-dev': 'libsystemd-dev',
}

_version_trans = str.maketrans({'+': '.', '-': '.', '~': '.'})


def _debian_version_to_pep440(debver: str) -> str:
    m = re.match(r'''
        ^(?:(?P<epoch>\d+):)?(?P<upstream>[^-]+)(?:-(?P<debian>.*))?$
    ''', debver, re.X)

    if not m:
        raise ValueError(f'unexpected debian package version: {debver}')

    epoch = m.group('epoch')
    version = ''
    if epoch:
        version += f'{epoch}!'

    upstream_ver = m.group('upstream')
    is_extra = False

    for i, part in enumerate(upstream_ver.split('.')):
        if is_extra:
            version += '.'
            version += part.translate(_version_trans)
        else:
            part_m = re.match(r'^([0-9]*)(.*)$', part)
            if part_m.group(1):
                if i > 0:
                    version += '.'
                version += part_m.group(1)

            rest = part_m.group(2)
            if rest:
                if rest[0] in '+-~':
                    rest = rest[1:]
                version += f'+{rest.translate(_version_trans)}'
                is_extra = True

    debian_part = m.group('debian')
    if debian_part:
        if not is_extra:
            version += '+'
        else:
            version += '.'
        version += debian_part.translate(_version_trans)

    return version


class DebRepository(repository.Repository):

    def __init__(self, packages=None):
        super().__init__(packages)
        self._parsed = set()

    def find_packages(
            self,
            name: str,
            constraint: typing.Optional[
                typing.Union[semver.VersionConstraint, str]
            ] = None,
            extras: typing.Optional[list] = None,
            allow_prereleases: bool = False) \
            -> typing.List[packages.Package]:

        if name not in self._parsed:
            packages = self.apt_get_packages(name)
            for package in packages:
                self.add_package(package)
            self._parsed.add(name)

        return super().find_packages(
            name, constraint, extras=extras,
            allow_prereleases=allow_prereleases)

    def apt_get_packages(self, name: str) -> list:
        system_name = PACKAGE_MAP.get(name, name)

        try:
            output = tools.cmd('apt-cache', 'policy', system_name,
                               errors_are_fatal=False)
        except subprocess.CalledProcessError:
            return []
        else:
            policy = self._parse_apt_policy_output(output.strip())
            if not policy:
                return []
            else:
                packages = []
                for version in policy['versions']:
                    norm_version = _debian_version_to_pep440(version)
                    pkg = SystemPackage(
                        name, norm_version, pretty_version=version,
                        system_name=system_name)
                    packages.append(pkg)

                return packages

    def _parse_apt_policy_output(self, output: str) -> dict:
        if not output:
            return {}

        meta = {}

        lines = output.split('\n')

        for no, line in enumerate(lines):
            line = line.strip()
            if no == 0:
                continue

            name, _, value = line.partition(':')
            value = value.strip()
            if value:
                meta[name.lower()] = value
            elif name.lower() == 'version table':
                break

        versions = []
        last_indent = -1

        for line in lines[no + 1:]:
            m = re.match(r'^([\s*]*)(.*)$', line)
            indent = len(m.group(1))
            content = m.group(2)

            if last_indent == -1 or indent < last_indent:
                version = content.split(' ')[0]
                versions.append(version)

            last_indent = indent

        meta['versions'] = versions

        return meta


class BaseDebTarget(targets.FHSTarget, targets.LinuxTarget):

    def __init__(self, distro_info):
        self.distro = distro_info

    def get_package_repository(self):
        return DebRepository()

    def get_arch_libdir(self):
        arch = tools.cmd('dpkg-architecture', '-qDEB_HOST_MULTIARCH').strip()
        return pathlib.Path('/usr/lib') / arch

    def build(self, root_pkg, deps, build_deps, io, workdir, outputdir):
        return debuild.Build(
            self, io, root_pkg, deps, build_deps, workdir, outputdir).run()


class ModernDebianTarget(BaseDebTarget):

    def get_capabilities(self) -> list:
        capabilities = super().get_capabilities()
        return capabilities + ['systemd']

    def get_resource_path(self, build, resource):
        if resource == 'systemd-units':
            return pathlib.Path('/lib/systemd/system')
        else:
            return super().get_resource_path(build, resource)

    def get_global_rules(self):
        return textwrap.dedent('''\
            export DH_VERBOSE=1
            export SHELL = /bin/bash
            export DEB_BUILD_MAINT_OPTIONS = hardening=+all
            dpkg_buildflags = \
                DEB_BUILD_MAINT_OPTIONS=$(DEB_BUILD_MAINT_OPTIONS) \
                dpkg-buildflags
        ''')


class DebianStretchOrNewerTarget(ModernDebianTarget):
    pass


class UbuntuBionicOrNewerTarget(ModernDebianTarget):
    pass


def get_specific_target(distro_info):
    if distro_info['id'] == 'debian':
        ver = int(distro_info['version_parts']['major'])
        if ver >= 9:
            return DebianStretchOrNewerTarget(distro_info)
        else:
            raise NotImplementedError(
                f'{distro_info["id"]} {distro_info["codename"]} '
                f'is not supported')

    elif distro_info['id'] == 'ubuntu':
        major = int(distro_info['version_parts']['major'])
        minor = int(distro_info['version_parts']['minor'])

        if (major, minor) >= (18, 4):
            return UbuntuBionicOrNewerTarget(distro_info)
        else:
            raise NotImplementedError(
                f'{distro_info["id"]} {distro_info["codename"]} '
                f'is not supported')

    else:
        raise NotImplementedError(f'{distro_info["id"]} is not supported')
