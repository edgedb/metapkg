import pathlib
import re
import subprocess
import typing

from poetry import packages
from poetry import semver

from metapkg import tools
from metapkg.packages import repository
from metapkg.targets import base as targets
from metapkg.targets.package import SystemPackage

from . import build as rpmbuild


PACKAGE_MAP = {
    "icu": "libicu",
    "icu-dev": "libicu-devel",
    "zlib": "zlib",
    "zlib-dev": "zlib-devel",
    "libxslt-dev": "libxslt-devel",
    "pam-dev": "pam-devel",
    "python": "python3",
    "uuid": "libuuid",
    "uuid-dev": "libuuid-devel",
    "systemd-dev": "systemd-devel",
    "openssl-dev": "openssl-devel",
    "libffi-dev": "libffi-devel",
}


SYSTEM_DEPENDENCY_MAP = {
    "adduser": ["/usr/sbin/useradd", "/usr/sbin/groupadd"],
}


_version_trans = str.maketrans({"+": ".", "-": ".", "~": "."})


def _rpm_version_to_pep440(rpmver: str) -> str:
    m = re.match(
        r"""
        ^(?:(?P<epoch>\d+):)?(?P<upstream>[^-]+)(?:-(?P<rpm>.*))?$
    """,
        rpmver,
        re.X,
    )

    if not m:
        raise ValueError(f"unexpected RPM package version: {rpmver}")

    epoch = m.group("epoch")
    version = ""
    if epoch and False:
        version += f"{epoch}!"

    upstream_ver = m.group("upstream")
    is_extra = False

    for i, part in enumerate(upstream_ver.split(".")):
        if is_extra:
            version += "."
            version += part.translate(_version_trans)
        else:
            part_m = re.match(r"^([0-9]*)(.*)$", part)
            if part_m.group(1):
                if i > 0:
                    version += "."
                version += part_m.group(1)

            rest = part_m.group(2)
            if rest:
                if rest[0] in "+-~":
                    rest = rest[1:]
                version += f"+{rest.translate(_version_trans)}"
                is_extra = True

    rpm_part = m.group("rpm")
    if rpm_part:
        if not is_extra:
            version += "+"
        else:
            version += "."
        version += rpm_part.translate(_version_trans)

    return version


class RPMRepository(repository.Repository):
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
        allow_prereleases: bool = False,
    ) -> typing.List[packages.Package]:

        if name not in self._parsed:
            packages = self.apt_get_packages(name)
            for package in packages:
                self.add_package(package)
            self._parsed.add(name)

        return super().find_packages(
            name,
            constraint,
            extras=extras,
            allow_prereleases=allow_prereleases,
        )

    def apt_get_packages(self, name: str) -> list:
        system_name = PACKAGE_MAP.get(name, name)

        try:
            output = tools.cmd(
                "yum",
                "--showduplicates",
                "list",
                system_name,
                errors_are_fatal=False,
                hide_stderr=True,
            )
        except subprocess.CalledProcessError:
            return []
        else:
            policy = self._parse_yum_list_output(output.strip())
            if not policy:
                return []
            else:
                packages = []
                for version in policy["versions"]:
                    norm_version = _rpm_version_to_pep440(version)
                    pkg = SystemPackage(
                        name,
                        norm_version,
                        pretty_version=version,
                        system_name=system_name,
                    )
                    packages.append(pkg)

                return packages

    def _parse_yum_list_output(self, output: str) -> dict:
        if not output:
            return {}

        meta = {}

        lines = output.split("\n")

        for no, line in enumerate(lines):
            line = line.strip()
            if line == "Available Packages":
                break
        else:
            return {}

        versions = []

        for line in lines[no + 1 :]:
            cols = re.split(r"\s+", line)
            if cols[1] not in versions:
                versions.append(cols[1])

        meta["versions"] = versions

        return meta


class BaseRPMTarget(targets.FHSTarget, targets.LinuxTarget):
    def __init__(self, distro_info):
        self.distro = distro_info

    def get_package_repository(self):
        return RPMRepository()

    def get_arch_libdir(self):
        return pathlib.Path(tools.cmd("rpm", "--eval", "%_libdir").strip())

    def get_sys_bindir(self):
        return pathlib.Path(tools.cmd("rpm", "--eval", "%_bindir").strip())

    def build(self, **kwargs):
        return rpmbuild.Build(self, **kwargs).run()

    def get_system_dependencies(self, dep_name) -> list:
        try:
            return SYSTEM_DEPENDENCY_MAP[dep_name]
        except KeyError:
            return super().get_system_dependencies(dep_name)

    def install_build_deps(self, build, spec):
        tools.cmd(
            "yum-builddep",
            "-y",
            spec,
            cwd=str(build.get_spec_root(relative_to=None)),
            stdout=build._io.output.stream,
            stderr=subprocess.STDOUT,
        )


class RHEL7OrNewerTarget(BaseRPMTarget):
    def get_capabilities(self) -> list:
        capabilities = super().get_capabilities()
        return capabilities + ["systemd", "libffi", "tzdata"]

    def get_resource_path(self, build, resource):
        if resource == "systemd-units":
            return pathlib.Path(
                tools.cmd("rpm", "--eval", "%_unitdir").strip()
            )
        else:
            return super().get_resource_path(build, resource)


class FedoraTarget(RHEL7OrNewerTarget):
    def install_build_deps(self, build, spec):
        tools.cmd(
            "dnf",
            "builddep",
            "-y",
            spec,
            cwd=str(build.get_spec_root(relative_to=None)),
            stdout=build._io.output.stream,
            stderr=subprocess.STDOUT,
        )


def get_specific_target(distro_info):
    if distro_info["id"] in {"centos", "rhel"}:
        ver = int(distro_info["version_parts"]["major"])
        if ver >= 7:
            return RHEL7OrNewerTarget(distro_info)
        else:
            raise NotImplementedError(
                f'{distro_info["id"]} {distro_info["codename"]} '
                f"is not supported"
            )

    elif distro_info["id"] == "fedora":
        ver = int(distro_info["version_parts"]["major"])
        if ver < 29:
            raise NotImplementedError(
                f'{distro_info["id"]} {distro_info["codename"]} '
                f"is not supported"
            )
        else:
            return FedoraTarget(distro_info)

    else:
        raise NotImplementedError(f'{distro_info["id"]} is not supported')
