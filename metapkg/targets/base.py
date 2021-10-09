from __future__ import annotations
from typing import (
    TYPE_CHECKING,
    Any,
    Iterable,
    Literal,
    Mapping,
)

import collections
import os
import pathlib
import re
import shlex
import shutil
import stat
import sys
import textwrap

from metapkg import tools
from metapkg.packages import base as mpkg_base
from metapkg.packages import repository as mpkg_repo
from metapkg.packages import sources as mpkg_sources

from . import _helpers as helpers_pkg
from . import package as tgt_pkg

if TYPE_CHECKING:
    from cleo.io.io import IO
    from poetry import packages as poetry_pkg


class TargetAction:
    def __init__(self, build: Build) -> None:
        self._build = build


class Target:
    @property
    def name(self) -> str:
        raise NotImplementedError

    def get_package_repository(self) -> mpkg_repo.Repository:
        raise NotImplementedError

    def prepare(self) -> None:
        pass

    def build(
        self,
        *,
        io: IO,
        root_pkg: mpkg_base.BundledPackage,
        deps: list[mpkg_base.BasePackage],
        build_deps: list[mpkg_base.BasePackage],
        workdir: str | pathlib.Path,
        outputdir: str | pathlib.Path,
        build_source: bool,
        build_debug: bool,
        revision: str,
        subdist: str | None,
        extra_opt: bool,
    ) -> None:
        raise NotImplementedError

    def get_capabilities(self) -> list[str]:
        return []

    def has_capability(self, capability: str) -> bool:
        return capability in self.get_capabilities()

    def get_system_dependencies(self, dep_name: str) -> list[str]:
        return [dep_name]

    def get_action(self, name: str, build: Build) -> TargetAction:
        raise NotImplementedError(f"unknown target action: {name}")

    def get_resource_path(
        self, build: Build, resource: str
    ) -> pathlib.Path | None:
        return None

    def get_package_system_ident(
        self,
        build: Build,
        package: mpkg_base.BundledPackage,
        include_slot: bool = False,
    ) -> str:
        return package.name_slot if include_slot else package.name

    def service_scripts_for_package(
        self, build: Build, package: mpkg_base.BasePackage
    ) -> dict[pathlib.Path, str]:
        return {}

    def get_package_ld_env(
        self, build: Build, package: mpkg_base.BasePackage, wd: str
    ) -> dict[str, str]:
        raise NotImplementedError

    def get_ld_env_keys(self, build: Build) -> list[str]:
        raise NotImplementedError

    def get_shlib_path_link_time_ldflags(
        self,
        build: Build,
        path: str,
    ) -> list[str]:
        raise NotImplementedError

    def get_shlib_path_run_time_ldflags(
        self, build: Build, path: str
    ) -> list[str]:
        raise NotImplementedError

    def get_exe_suffix(self) -> str:
        raise NotImplementedError

    def get_install_root(self, build: Build) -> pathlib.Path:
        raise NotImplementedError

    def get_install_prefix(self, build: Build) -> pathlib.Path:
        raise NotImplementedError

    def get_full_install_prefix(self, build: Build) -> pathlib.Path:
        return self.get_install_root(build) / self.get_install_prefix(build)

    def get_install_path(self, build: Build, aspect: str) -> pathlib.Path:
        raise NotImplementedError

    def supports_lto(self) -> bool:
        return False

    def supports_pgo(self) -> bool:
        return False

    def uses_modern_gcc(self) -> bool:
        return False

    def get_su_script(self, build: Build, script: str, user: str) -> str:
        raise NotImplementedError


class EnsureDirAction(TargetAction):
    def get_script(
        self,
        *,
        path: str,
        owner_user: str | None = None,
        owner_group: str | None = None,
        owner_recursive: bool = False,
        mode: int = 0o755,
    ) -> str:
        raise NotImplementedError


class AddUserAction(TargetAction):
    def get_script(
        self,
        *,
        name: str,
        group: str | None = None,
        homedir: str | None = None,
        shell: bool = False,
        system: bool = False,
        description: str | None = None,
    ) -> str:
        raise NotImplementedError


class PosixEnsureDirAction(EnsureDirAction):
    def get_script(
        self,
        *,
        path: str,
        owner_user: str | None = None,
        owner_group: str | None = None,
        owner_recursive: bool = False,
        mode: int = 0o755,
    ) -> str:
        chown_flags = "-R" if owner_recursive else ""

        script = textwrap.dedent(
            f"""\
            if ! [ -d "{path}" ]; then
                mkdir -p "{path}"
            fi
            chmod "{mode:o}" "{path}"
        """
        )

        if owner_user and owner_group:
            script += (
                f'\nchown {chown_flags} "{owner_user}:{owner_group}" "{path}"'
            )
        elif owner_user:
            script += f'\nchown {chown_flags} "{owner_user}" "{path}"'
        elif owner_group:
            script += f'\nchgrp {chown_flags} "{owner_group}" "{path}"'

        return script


class PosixTarget(Target):
    def get_action(self, name: str, build: Build) -> TargetAction:
        if name == "ensuredir":
            return PosixEnsureDirAction(build)
        else:
            return super().get_action(name, build)

    def get_exe_suffix(self) -> str:
        return ""


class LinuxAddUserAction(AddUserAction):
    def get_script(
        self,
        *,
        name: str,
        group: str | None = None,
        homedir: str | None = None,
        shell: bool = False,
        system: bool = False,
        description: str | None = None,
    ) -> str:

        args: dict[str, str | None] = {}
        if group:
            args["-g"] = group
        if homedir:
            args["-d"] = homedir
        else:
            args["-M"] = None
        if shell:
            args["-s"] = "/bin/bash"
        else:
            args["-s"] = "/sbin/nologin"
        if system:
            args["-r"] = None
        if description:
            args["-c"] = description

        args[name] = None

        user_group = name

        if group:
            group_args: dict[str, str | None] = {}
            if system:
                group_args["-r"] = None
            group_args[group] = None

            groupadd = self._build.sh_get_command("groupadd")

            groupadd_cmd = self._build.sh_format_command(
                groupadd, group_args, extra_indent=4
            )
            group_script = textwrap.dedent(
                """\
                if ! getent group "{group}" > /dev/null; then
                    {groupadd_cmd}
                fi
            """
            ).format(group=group, groupadd_cmd=groupadd_cmd)

            user_group += f":{group}"
        else:
            group_script = ""

        if homedir:
            homedir_script = PosixEnsureDirAction(self._build).get_script(
                path=homedir, owner_user=name, owner_group=group
            )
        else:
            homedir_script = ""

        useradd = self._build.sh_get_command("useradd")
        useradd_cmd = self._build.sh_format_command(
            useradd, args, extra_indent=4
        )

        return textwrap.dedent(
            """\
            {group_script}
            if ! getent passwd "{name}" > /dev/null; then
                {useradd_cmd}
            fi
            {homedir_script}
        """
        ).format(
            group_script=group_script,
            name=name,
            useradd_cmd=useradd_cmd,
            homedir_script=homedir_script,
        )


class LinuxTarget(PosixTarget):
    def __init__(self, distro_info: dict[str, Any]) -> None:
        self.distro = distro_info

    @property
    def name(self) -> str:
        return f'{self.distro["id"]}-{self.distro["version"]}'

    def get_action(self, name: str, build: Build) -> TargetAction:
        if name == "adduser":
            return LinuxAddUserAction(build)
        else:
            return super().get_action(name, build)

    def get_su_script(self, build: Build, script: str, user: str) -> str:
        return f"su '{user}' -c {shlex.quote(script)}\n"

    def service_scripts_for_package(
        self, build: Build, package: mpkg_base.BasePackage
    ) -> dict[pathlib.Path, str]:
        if self.has_capability("systemd"):
            units = package.read_support_files(build, "*.service.in")
            systemd_path = self.get_resource_path(build, "systemd-units")
            if systemd_path is None:
                raise RuntimeError(
                    "systemd-enabled target does not define "
                    '"systemd-units" path'
                )
            return {systemd_path / name: data for name, data in units.items()}

        else:
            raise NotImplementedError(
                "non-systemd linux targets are not supported"
            )

    def supports_lto(self) -> bool:
        # LTO more-or-less stabilized in GCC 4.9.0.
        gcc_ver = tools.cmd("gcc", "--version")
        m = re.match(r"^gcc.*?(\d+(?:\.\d+)+)", gcc_ver, re.M)
        if not m:
            raise RuntimeError(f"cannot determine gcc version:\n{gcc_ver}")
        return tuple(int(v) for v in m.group(1).split(".")) >= (4, 9)

    def supports_pgo(self) -> bool:
        # PGO is broken on pre-4.9, similarly to LTO.
        return self.supports_lto()

    def uses_modern_gcc(self) -> bool:
        gcc_ver = tools.cmd("gcc", "--version")
        m = re.match(r"^gcc.*?(\d+(?:\.\d+)+)", gcc_ver, re.M)
        if not m:
            raise RuntimeError(f"cannot determine gcc version:\n{gcc_ver}")
        return tuple(int(v) for v in m.group(1).split(".")) >= (10, 0)

    def get_package_ld_env(
        self, build: Build, package: mpkg_base.BasePackage, wd: str
    ) -> dict[str, str]:
        pkg_install_root = build.get_install_dir(
            package, relative_to="pkgbuild"
        )
        pkg_lib_path = pkg_install_root / build.get_install_path(
            "lib"
        ).relative_to("/")
        return {"LD_LIBRARY_PATH": f"{wd}/{pkg_lib_path}"}

    def get_ld_env_keys(self, build: Build) -> list[str]:
        return ["LD_LIBRARY_PATH"]

    def get_shlib_path_link_time_ldflags(
        self, build: Build, path: str
    ) -> list[str]:
        return [f"-L{path}", f"-Wl,-rpath-link,{path}"]

    def get_shlib_path_run_time_ldflags(
        self, build: Build, path: str
    ) -> list[str]:
        return [f"-Wl,-rpath,{path}"]


class FHSTarget(PosixTarget):
    def get_arch_libdir(self) -> pathlib.Path:
        raise NotImplementedError

    def get_sys_bindir(self) -> pathlib.Path:
        return pathlib.Path("/usr/bin")

    def sh_get_command(self, command: str) -> str:
        return command

    def get_install_root(self, build: Build) -> pathlib.Path:
        return pathlib.Path("/")

    def get_install_prefix(self, build: Build) -> pathlib.Path:
        libdir = self.get_arch_libdir()
        return (libdir / build.root_package.name_slot).relative_to("/")

    def get_install_path(self, build: Build, aspect: str) -> pathlib.Path:
        root = self.get_install_root(build)
        prefix = self.get_install_prefix(build)

        if aspect == "sysconf":
            return root / "etc"
        elif aspect == "userconf":
            return pathlib.Path("$HOME") / ".config"
        elif aspect == "data":
            return root / "usr" / "share" / build.root_package.name_slot
        elif aspect == "bin":
            return root / prefix / "bin"
        elif aspect == "systembin":
            if root == pathlib.Path("/"):
                return self.get_sys_bindir()
            else:
                return root / "bin"
        elif aspect == "lib":
            return root / prefix / "lib"
        elif aspect == "include":
            return root / "usr" / "include" / build.root_package.name_slot
        elif aspect == "localstate":
            return root / "var"
        elif aspect == "runstate":
            return root / "run"
        else:
            raise LookupError(f"aspect: {aspect}")

    def get_resource_path(
        self, build: Build, resource: str
    ) -> pathlib.Path | None:
        if resource == "tzdata":
            return pathlib.Path("/usr/share/zoneinfo")
        else:
            return None


Location = Literal[
    "fsroot", "buildroot", "pkgsource", "sourceroot", "pkgbuild"
]


class Build:
    def __init__(
        self,
        target: Target,
        *,
        io: IO,
        root_pkg: mpkg_base.BundledPackage,
        deps: list[mpkg_base.BasePackage],
        build_deps: list[mpkg_base.BasePackage],
        workdir: str | pathlib.Path,
        outputdir: str | pathlib.Path,
        build_source: bool,
        build_debug: bool,
        revision: str,
        subdist: str | None,
        extra_opt: bool,
    ) -> None:
        self._droot = pathlib.Path(workdir)
        self._outputroot = pathlib.Path(outputdir)
        self._target = target
        self._io = io
        self._root_pkg = root_pkg
        self._deps = deps
        self._build_deps = build_deps
        self._build_source = build_source
        self._build_debug = build_debug
        self._revision = revision
        self._subdist = subdist
        self._extra_opt = extra_opt
        self._bundled = [
            pkg
            for pkg in self._build_deps
            if not isinstance(
                pkg, (tgt_pkg.SystemPackage, mpkg_base.DummyPackage)
            )
            and pkg is not root_pkg
        ]
        self._build_only = set(build_deps) - set(deps)
        self._installable = [
            pkg for pkg in self._bundled if pkg not in self._build_only
        ]
        self._tools: dict[str, pathlib.Path] = {}
        self._common_tools: dict[str, pathlib.Path] = {}
        self._system_tools: dict[str, str] = {}
        self._tarballs: dict[mpkg_base.BasePackage, pathlib.Path] = {}
        self._patches: list[str] = []

    @property
    def io(self) -> IO:
        return self._io

    @property
    def root_package(self) -> mpkg_base.BundledPackage:
        return self._root_pkg

    @property
    def target(self) -> Target:
        return self._target

    def get_source_abspath(self) -> pathlib.Path:
        raise NotImplementedError

    def get_path(
        self,
        path: str | pathlib.Path,
        *,
        relative_to: Location,
        package: mpkg_base.BasePackage | None = None,
    ) -> pathlib.Path:
        raise NotImplementedError

    def get_package(self, name: str) -> mpkg_base.BasePackage:
        for pkg in self._deps:
            if pkg.name == name:
                return pkg

        for pkg in self._build_deps:
            if pkg.name == name:
                return pkg

        raise LookupError(f"package not found: {name}")

    def get_packages(
        self, names: Iterable[str]
    ) -> list[mpkg_base.BasePackage]:
        packages = []
        for name in names:
            package = self.get_package(name)
            if package is not None:
                packages.append(package)
        return packages

    def is_bundled(self, pkg: mpkg_base.BasePackage) -> bool:
        return pkg in self._bundled

    def extra_optimizations_enabled(self) -> bool:
        return self._extra_opt

    def supports_lto(self) -> bool:
        return self._target.supports_lto()

    def supports_pgo(self) -> bool:
        return self._target.supports_pgo()

    def uses_modern_gcc(self) -> bool:
        return self._target.uses_modern_gcc()

    def run(self) -> None:
        self._io.write_line(
            f"<info>Building {self._root_pkg} on "
            f"{self._target.name}</info>"
        )

        self.prepare()
        self.build()

    def prepare(self) -> None:
        self._system_tools["make"] = "make"
        self._system_tools["bash"] = "/bin/bash"
        self._system_tools["find"] = "find"

    def build(self) -> None:
        raise NotImplementedError

    def get_dir(
        self,
        path: str | pathlib.Path,
        *,
        relative_to: Location,
        package: mpkg_base.BasePackage | None = None,
    ) -> pathlib.Path:
        absolute_path = (self.get_source_abspath() / path).resolve()
        if not absolute_path.exists():
            absolute_path.mkdir(parents=True)

        return self.get_path(path, relative_to=relative_to, package=package)

    def get_install_dir(
        self,
        package: mpkg_base.BasePackage,
        *,
        relative_to: Location = "sourceroot",
    ) -> pathlib.Path:
        raise NotImplementedError

    def get_install_path(self, aspect: str) -> pathlib.Path:
        return self._target.get_install_path(self, aspect)

    def get_install_prefix(self) -> pathlib.Path:
        return self._target.get_install_prefix(self)

    def get_full_install_prefix(self) -> pathlib.Path:
        return self._target.get_full_install_prefix(self)

    def get_helpers_root(
        self, *, relative_to: Location = "sourceroot"
    ) -> pathlib.Path:
        raise NotImplementedError

    def get_build_dir(
        self,
        package: mpkg_base.BasePackage,
        *,
        relative_to: Location = "sourceroot",
    ) -> pathlib.Path:
        raise NotImplementedError

    def get_temp_root(
        self, *, relative_to: Location = "sourceroot"
    ) -> pathlib.Path:
        raise NotImplementedError

    def get_temp_dir(
        self,
        package: mpkg_base.BasePackage,
        *,
        relative_to: Location = "sourceroot",
    ) -> pathlib.Path:
        raise NotImplementedError

    def get_extras_root(
        self, *, relative_to: Location = "sourceroot"
    ) -> pathlib.Path:
        raise NotImplementedError

    def get_exe_suffix(self) -> str:
        return self._target.get_exe_suffix()

    def sh_get_command(
        self,
        command: str,
        *,
        package: mpkg_base.BasePackage | None = None,
        relative_to: Location = "pkgbuild",
    ) -> str:
        path = self._tools.get(command)
        if not path:
            path = self._common_tools.get(command)

        if not path:
            # This is an unclaimed command.  Assume system executable.
            system_tool = self._system_tools.get(command)
            if not system_tool:
                raise RuntimeError(f"unrecognized command: {command}")

            # System tools are already properly quoted shell commands.
            cmd = system_tool

        else:
            rel_path = self.get_path(
                path, package=package, relative_to=relative_to
            )
            if rel_path.suffix == ".py":
                python = self.sh_get_command(
                    "python", package=package, relative_to=relative_to
                )

                cmd = f"{python} {shlex.quote(str(rel_path))}"

            elif not rel_path.suffix:
                cmd = shlex.quote(str(rel_path))

            else:
                raise RuntimeError(f"unexpected tool type: {path}")

        return cmd

    def sh_format_command(
        self,
        path: str | pathlib.Path,
        args: Mapping[str, str | pathlib.Path | None],
        *,
        extra_indent: int = 0,
        user: str | None = None,
        force_args_eq: bool = False,
        linebreaks: bool = True,
    ) -> str:
        args_parts = []
        for arg, val in args.items():
            if val is None:
                args_parts.append(arg)
            else:
                val = str(val)
                if not val.startswith("!"):
                    val = shlex.quote(val)
                else:
                    val = val[1:]
                sep = "=" if arg.startswith("--") or force_args_eq else " "
                args_parts.append(f"{arg}{sep}{val}")

        if linebreaks:
            sep = " \\\n    "
        else:
            sep = " "

        args_str = sep.join(args_parts)

        if linebreaks:
            args_str = textwrap.indent(args_str, " " * 4)

        result = f"{shlex.quote(str(path))}{sep}{args_str}"

        if extra_indent:
            result = textwrap.indent(result, " " * extra_indent)

        return result

    def format_package_template(
        self, tpl: str, package: mpkg_base.BasePackage
    ) -> str:
        variables: dict[str, str] = {}
        for aspect in (
            "bin",
            "data",
            "include",
            "lib",
            "runstate",
            "localstate",
            "userconf",
        ):
            path = self.get_install_path(aspect)
            variables[f"{aspect}dir"] = str(path)

        variables["prefix"] = str(self.get_install_prefix())
        variables["slot"] = package.slot
        variables["identifier"] = self.target.get_package_system_ident(
            self, package
        )
        variables["name"] = package.name
        variables["description"] = package.description
        variables["documentation"] = package.url

        return tools.format_template(tpl, **variables)

    def write_helper(
        self, name: str, text: str, *, relative_to: Location
    ) -> pathlib.Path:
        helpers_abs = self.get_helpers_root(relative_to="fsroot")
        helpers_rel = self.get_helpers_root(relative_to=relative_to)

        with open(helpers_abs / name, "w") as f:
            print(text, file=f)
            os.chmod(f.name, 0o755)

        return helpers_rel / name

    def sh_write_helper(
        self, name: str, text: str, *, relative_to: Location
    ) -> str:
        """Write an executable helper and return it's shell-escaped name."""

        cmd = self.write_helper(name, text, relative_to=relative_to)
        return f"{shlex.quote(str(cmd))}"

    def sh_write_python_helper(
        self, name: str, text: str, *, relative_to: Location
    ) -> str:

        python = self.sh_get_command("python", relative_to=relative_to)
        path = self.sh_write_helper(name, text, relative_to=relative_to)

        return f"{shlex.quote(python)} {path}"

    def sh_write_bash_helper(
        self, name: str, text: str, *, relative_to: Location
    ) -> str:
        bash = self.sh_get_command("bash")
        script = textwrap.dedent(
            """\
            #!{bash}
            set -ex

            {text}
        """
        ).format(text=text, bash=bash)

        return self.sh_write_helper(name, script, relative_to=relative_to)

    def get_tarball_tpl(self, package: mpkg_base.BasePackage) -> str:
        rp = self._root_pkg
        return (
            f"{rp.name_slot}_{rp.version.text}.orig-{package.name}.tar{{comp}}"
        )

    def get_tarball_root(
        self, *, relative_to: Location = "sourceroot"
    ) -> pathlib.Path:
        raise NotImplementedError

    def get_patches_root(
        self, *, relative_to: Location = "sourceroot"
    ) -> pathlib.Path:
        raise NotImplementedError

    def get_source_dir(
        self,
        package: mpkg_base.BasePackage,
        *,
        relative_to: Location = "sourceroot",
    ) -> pathlib.Path:
        raise NotImplementedError

    def get_tool_list(self) -> list[str]:
        return ["trim-install.py", "copy-tree.py"]

    def get_su_script(self, script: str, user: str) -> str:
        return self.target.get_su_script(self, script, user)

    def prepare_tools(self) -> None:
        for pkg in self._bundled:
            bundled_tools = pkg.get_build_tools(self)
            if bundled_tools:
                self._tools.update(bundled_tools)

        source_dirs = [pathlib.Path(helpers_pkg.__path__[0])]  # type: ignore
        specific_helpers = (
            pathlib.Path(sys.modules[self.__module__].__file__).parent
            / "_helpers"
        )
        if specific_helpers.exists():
            source_dirs.insert(0, specific_helpers)

        helpers_target_dir = self.get_helpers_root(relative_to="fsroot")
        helpers_rel_dir = self.get_helpers_root(relative_to="sourceroot")

        for helper in self.get_tool_list():
            for source_dir in source_dirs:
                if (source_dir / helper).exists():
                    shutil.copy(
                        source_dir / helper, helpers_target_dir / helper
                    )
                    os.chmod(
                        helpers_target_dir / helper,
                        stat.S_IRWXU
                        | stat.S_IRGRP
                        | stat.S_IXGRP
                        | stat.S_IROTH
                        | stat.S_IXOTH,
                    )
                    break
            else:
                raise RuntimeError(f"cannot find helper: {helper}")

            self._common_tools[pathlib.Path(helper).stem] = (
                helpers_rel_dir / helper
            )

    def prepare_tarballs(self) -> None:
        tarball_root = self.get_tarball_root(relative_to="fsroot")

        for pkg in self._bundled:
            tarball_tpl = self.get_tarball_tpl(pkg)
            for source in pkg.get_sources():
                tarball = source.tarball(
                    pkg, tarball_tpl, target_dir=tarball_root, io=self._io
                )

                self._tarballs[pkg] = tarball

    def unpack_sources(self) -> None:
        for pkg, tarball in self._tarballs.items():
            self._io.write_line(f"<info>Extracting {tarball.name}...</>")
            mpkg_sources.unpack(
                tarball,
                dest=self.get_source_dir(pkg, relative_to="fsroot"),
                io=self._io,
            )

    def prepare_patches(self) -> None:
        patches_dir = self.get_patches_root(relative_to="fsroot")

        i = 0
        series = []

        for pkg in self._bundled:
            for pkgname, patches in pkg.get_patches().items():
                for patchname, patch in patches:
                    fixed_patch = re.sub(
                        r"(---|\+\+\+) (a|b)/(.*)",
                        f"\\g<1> \\g<2>/{pkgname}/\\g<3>",
                        patch,
                    )

                    if patchname:
                        patchname = f"--{patchname}"

                    filename = f"{i:04d}-{pkgname}{patchname}.patch"

                    with open(patches_dir / filename, "w") as f:
                        f.write(fixed_patch)

                    series.append(filename)
                    i += 1

        with open(patches_dir / "series", "w") as f:
            print("\n".join(series), file=f)

        self._patches = series

    def get_extra_system_requirements(self) -> dict[str, set[str]]:
        all_reqs = collections.defaultdict(set)

        for pkg in self._installable:
            reqs = pkg.get_extra_system_requirements(self)
            for req_type, req_list in reqs.items():
                sys_reqs = set()
                for req in req_list:
                    sys_reqs.update(self.target.get_system_dependencies(req))

                all_reqs[req_type].update(sys_reqs)

        return dict(all_reqs)

    def get_service_scripts(self) -> dict[pathlib.Path, str]:
        all_scripts = {}

        for pkg in self._installable:
            pkg_scripts = pkg.get_service_scripts(self)
            all_scripts.update(pkg_scripts)

        return all_scripts

    def _write_script(
        self,
        stage: str,
        *,
        installable_only: bool = False,
        relative_to: Location = "sourceroot",
    ) -> str:

        script = self.get_script(
            stage, installable_only=installable_only, relative_to=relative_to
        )

        helper = self.sh_write_bash_helper(
            f"_{stage}.sh", script, relative_to=relative_to
        )

        return f"\t{helper}"

    def get_script(
        self,
        stage: str,
        *,
        installable_only: bool = False,
        relative_to: Location = "sourceroot",
    ) -> str:

        scripts = []

        if installable_only:
            packages = self._installable
        else:
            packages = self._bundled

        if stage == "complete":
            stages = ["configure", "build", "build_install"]
        else:
            stages = [stage]

        for pkg in packages:
            for stg in stages:
                script = self._get_package_script(
                    pkg, stg, relative_to=relative_to
                )
                if script.strip():
                    scripts.append(script)

        global_method = getattr(self, f"_get_global_{stage}_script", None)
        if global_method:
            global_script = global_method()
            if global_script:
                scripts.append(global_script)

        return "\n\n".join(scripts)

    def _get_global_after_install_script(self) -> str:
        script = ""
        service_scripts = self.get_service_scripts()

        if service_scripts:
            if self.target.has_capability("systemd"):
                rundir = self.get_install_path("runstate")
                systemd = rundir / "systemd" / "system"

                script = textwrap.dedent(
                    f"""\
                    if [ -d "{systemd}" ]; then
                        systemctl daemon-reload
                    fi
                """
                )

            elif self.target.has_capability("launchd"):
                script_lines = []

                for path in service_scripts:
                    ident = path.stem
                    script_lines.append(f'launchctl bootstrap system "{path}"')
                    script_lines.append(f"launchctl enable system/{ident}")

                script = "\n".join(script_lines)

        if script:
            script = (
                f'if [ -z "${{_EDGEDB_INSTALL_SKIP_BOOTSTRAP}}" ]; then\n'
                f"{script}\nfi"
            )

        return script

    def _get_package_script(
        self,
        pkg: mpkg_base.BasePackage,
        stage: str,
        *,
        relative_to: Location = "sourceroot",
    ) -> str:
        method = f"get_{stage}_script"
        self_method = getattr(self, f"_get_package_{stage}_script", None)
        if self_method:
            pkg_script = self_method(pkg) + "\n"
        else:
            pkg_script = ""

        bdir = self.get_build_dir(pkg, relative_to=relative_to)

        pkg_method = getattr(pkg, method, None)
        if pkg_method:
            pkg_script += pkg_method(self)

        build_time = stage not in {
            "before_install",
            "after_install",
            "before_uninstall",
            "after_uninstall",
        }

        if pkg_script:
            script_lines = [f"### {pkg.unique_name}\n"]
            if build_time:
                script_lines.append(f'pushd "{bdir}" >/dev/null\n')
            script_lines.append(f"{pkg_script}\n")
            if build_time:
                script_lines.append(f"popd >/dev/null")

            script = "".join(script_lines)
        else:
            script = ""

        return script

    def get_ld_env(
        self,
        deps: Iterable[mpkg_base.BasePackage],
        wd: str,
        extra: Iterable[str] = (),
    ) -> list[str]:
        env: dict[str, list[str]] = collections.defaultdict(list)
        keys = self.target.get_ld_env_keys(self)
        for k in keys:
            env[k] = []

        for pkg in deps:
            if not self.is_bundled(pkg):
                continue

            for k, v in self.target.get_package_ld_env(self, pkg, wd).items():
                env[k].append(v)

        env_list = []
        for k, vv in env.items():
            v = ":".join(vv + list(extra) + [f"${{{k}}}"])
            env_list.append(f'{k}="{v}"')

        return env_list

    def sh_get_bundled_shlibs_ldflags(
        self,
        deps: Iterable[poetry_pkg.Package],
        relative_to: Location = "pkgbuild",
    ) -> str:
        flags = []

        for pkg in deps:
            if not self.is_bundled(pkg):
                continue

            root_path = self.get_install_dir(pkg, relative_to=relative_to)
            for shlib_path in pkg.get_shlib_paths(self):
                # link-time dependency
                link_time = root_path / shlib_path.relative_to("/")
                flags.extend(
                    self.target.get_shlib_path_link_time_ldflags(
                        self,
                        f"$(pwd)/{shlex.quote(str(link_time))}",
                    ),
                )

                if pkg not in self._build_only:
                    # run-time dependency
                    flags.extend(
                        self.target.get_shlib_path_run_time_ldflags(
                            self,
                            shlex.quote(str(shlib_path)),
                        ),
                    )

        return '" "'.join(flags)

    def sh_get_bundled_shlibs_cflags(
        self,
        deps: Iterable[poetry_pkg.Package],
        relative_to: Location = "pkgbuild",
    ) -> str:
        flags = []

        for pkg in deps:
            if not self.is_bundled(pkg):
                continue

            root_path = self.get_install_dir(pkg, relative_to=relative_to)
            for include_path in pkg.get_include_paths(self):
                inc_path = root_path / include_path.relative_to("/")
                flags.append(f"-I$(pwd)/{shlex.quote(str(inc_path))}")

        return '" "'.join(flags)
