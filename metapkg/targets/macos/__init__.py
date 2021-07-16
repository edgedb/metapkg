from __future__ import annotations
from typing import *

import pathlib
import shlex
import textwrap
import typing

from poetry import packages
from poetry import semver

from metapkg import tools
from metapkg.packages import repository
from metapkg.targets import base as targets
from metapkg.targets import generic
from metapkg.targets.package import SystemPackage

from . import build as macbuild


PACKAGE_WHITELIST = [
    "bison",
    "flex",
    "perl",
    "pam",
    "pam-dev",
    "uuid",
    "uuid-dev",
    "zlib",
    "zlib-dev",
]


class MacOSAddUserAction(targets.TargetAction):
    def get_script(
        self,
        *,
        name,
        group=None,
        homedir=None,
        shell=False,
        system=False,
        description=None,
    ) -> str:

        if group:
            groupname = f"/Groups/{group}"
            groupadd_cmds = [
                self._get_dscl_cmd(groupname),
            ]
            if description:
                groupadd_cmds.append(
                    self._get_dscl_cmd(
                        groupname, key="RealName", value=description
                    )
                )

            last_gid = self._get_dscl_cmd(
                "/Groups", action="list", key="PrimaryGroupID"
            )
            last_gid += " | awk '{ print $2 }' | sort -n | tail -1"

            groupadd_cmds.append(
                self._get_dscl_cmd(
                    groupname,
                    key="Password",
                    value="*",
                )
            )

            groupadd_cmds.append(
                self._get_dscl_cmd(
                    groupname,
                    key="PrimaryGroupID",
                    value=f"!$(($({last_gid}) + 1))",
                )
            )

            group_exists = self._get_dscl_cmd(groupname, action="-read")

            group_script = textwrap.dedent(
                """\
                if ! {group_exists} >/dev/null 2>&1; then
                    {groupadd_cmds}
                fi
            """
            ).format(
                groupadd_cmds="\n    ".join(groupadd_cmds),
                group_exists=group_exists,
            )

        else:
            group_script = ""

        username = f"/Users/{name}"
        useradd_cmds = []

        useradd_cmds.append(self._get_dscl_cmd(username))

        if homedir:
            useradd_cmds.append(
                self._get_dscl_cmd(
                    username, key="NFSHomeDirectory", value=homedir
                )
            )
        if shell:
            useradd_cmds.append(
                self._get_dscl_cmd(
                    username, key="UserShell", value="/bin/bash"
                )
            )
        else:
            useradd_cmds.append(
                self._get_dscl_cmd(
                    username, key="UserShell", value="/sbin/nologin"
                )
            )
        if description:
            useradd_cmds.append(
                self._get_dscl_cmd(username, key="RealName", value=description)
            )

        useradd_cmds.append(
            self._get_dscl_cmd(username, key="Password", value="*")
        )

        useradd_cmds.append(
            self._get_dscl_cmd(username, key="IsHidden", value="1")
        )

        last_uid = self._get_dscl_cmd("/Users", action="list", key="UniqueID")
        last_uid += " | awk '{ print $2 }' | sort -n | tail -1"

        useradd_cmds.append(
            self._get_dscl_cmd(
                username,
                key="UniqueID",
                value=f"!$(($({last_uid}) + 1))",
            )
        )

        if system:
            primary_group = "daemon"
        elif group:
            primary_group = group

        if primary_group:
            get_group = self._get_dscl_cmd(
                f"/Groups/{primary_group}", action="-read"
            )

            get_group += "| awk '($1 == \"PrimaryGroupID:\") { print $2 }'"

            useradd_cmds.append(
                self._get_dscl_cmd(
                    username, key="PrimaryGroupID", value=f"!$({get_group})"
                )
            )

        if group and group != primary_group:
            assign_group_script = (
                f'dseditgroup -o edit -a "{name}" -t user "{group}"'
            )
        else:
            assign_group_script = ""

        user_exists = self._get_dscl_cmd(username, action="-read")

        return textwrap.dedent(
            """\
            {create_group_script}
            if ! {user_exists} >/dev/null 2>&1; then
                {useradd_cmd}
            fi
            {assign_group_script}
        """
        ).format(
            create_group_script=group_script,
            useradd_cmd="\n    ".join(useradd_cmds),
            user_exists=user_exists,
            assign_group_script=assign_group_script,
        )

    def _get_dscl_cmd(
        self, name, *, action="create", key=None, value=None, indent=0
    ):
        args = {
            ".": None,
            action: None,
            name: None,
        }

        if key is not None:
            args[key] = value

        return self._build.sh_format_command(
            "dscl", args, extra_indent=indent, linebreaks=False
        )


class MacOSRepository(repository.Repository):
    def find_packages(
        self,
        name: str,
        constraint: typing.Optional[
            typing.Union[semver.VersionConstraint, str]
        ] = None,
        extras: typing.Optional[list] = None,
        allow_prereleases: bool = False,
    ) -> typing.List[packages.Package]:

        if name in PACKAGE_WHITELIST:
            pkg = SystemPackage(
                name, version="1.0", pretty_version="1.0", system_name=name
            )
            self.add_package(pkg)

            return [pkg]
        else:
            return []


class MacOSTarget(generic.GenericTarget):
    def __init__(self, version):
        self.version = version

    def prepare(self):
        tools.cmd("brew", "update")
        brew_inst = (
            'if brew ls --versions "$1"; then brew upgrade "$1"; '
            'else brew install "$1"; fi'
        )
        tools.cmd("/bin/sh", "-c", brew_inst, "--", "bash")
        tools.cmd("/bin/sh", "-c", brew_inst, "--", "make")

    @property
    def name(self):
        return f'macOS {".".join(str(v) for v in self.version)}'

    def get_package_system_ident(
        self, build, package, include_slot: bool = False
    ):
        if include_slot:
            return f"{package.identifier}{package.slot_suffix}"
        else:
            return package.identifier

    def get_su_script(self, build, script, user) -> str:
        return f"su '{user}' -c {shlex.quote(script)}\n"

    def get_action(self, name, build) -> targets.TargetAction:
        if name == "adduser":
            return MacOSAddUserAction(build)
        else:
            return super().get_action(name, build)

    def get_install_path(self, build, aspect) -> pathlib.Path:
        if aspect == "localstate":
            return pathlib.Path("/") / "var"
        elif aspect == "userconf":
            return pathlib.Path("$HOME") / "Library" / "Application Support"
        elif aspect == "runstate":
            return pathlib.Path("/") / "var" / "run"
        elif aspect == "systembin":
            return self.get_install_root(build).parent.parent / "bin"
        else:
            return super().get_install_path(build, aspect)

    def get_package_repository(self):
        return MacOSRepository()

    def get_framework_root(self, build) -> pathlib.Path:
        rpkg = build.root_package
        return pathlib.Path(f"/Library/Frameworks/{rpkg.title}.framework")

    def get_install_root(self, build) -> pathlib.Path:
        rpkg = build.root_package
        return self.get_framework_root(build) / "Versions" / rpkg.slot

    def get_resource_path(self, build, resource):
        if resource == "system-daemons":
            return pathlib.Path("/Library/LaunchDaemons/")
        else:
            return super().get_resource_path(build, resource)

    def service_scripts_for_package(self, build, package) -> dict:
        units = package.read_support_files(build, "*.plist.in")
        launchd_path = self.get_resource_path(build, "system-daemons")
        return {launchd_path / name: data for name, data in units.items()}

    def get_capabilities(self) -> list:
        capabilities = super().get_capabilities()
        return capabilities + ["launchd"]

    def get_package_ld_env(self, build, package, wd) -> Dict[str, str]:
        pkg_install_root = build.get_install_dir(
            package, relative_to="pkgbuild"
        )
        pkg_lib_path = pkg_install_root / build.get_install_path(
            "lib"
        ).relative_to("/")

        fw_root = self.get_framework_root(build).parent
        pkg_fw_root = pkg_install_root / fw_root.relative_to("/")

        return {
            "DYLD_LIBRARY_PATH": f"{wd}/{pkg_lib_path}",
            "DYLD_FRAMEWORK_PATH": f"{wd}/{pkg_fw_root}",
        }

    def get_ld_env_keys(self, build) -> List[str]:
        return ["DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH"]

    def get_shlib_path_link_time_ldflags(self, build, path) -> List[str]:
        return [f"-L{path}"]

    def get_shlib_path_run_time_ldflags(self, build, path) -> List[str]:
        return []


class ModernMacOSTarget(MacOSTarget):
    def build(self, **kwargs):
        return macbuild.Build(self, **kwargs).run()


def get_specific_target(version):

    if version >= (10, 10):
        return ModernMacOSTarget(version)
    else:
        raise NotImplementedError(
            f'macOS version {".".join(version)} is not supported'
        )
