from __future__ import annotations
from typing import (
    TYPE_CHECKING,
    Any,
    Iterable,
    Literal,
    Mapping,
    NamedTuple,
)

import collections
import hashlib
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
    from poetry.utils import env as poetry_env


class TargetAction:
    def __init__(self, build: Build) -> None:
        self._build = build


class BuildRequest(NamedTuple):
    io: IO
    env: poetry_env.Env
    root_pkg: mpkg_base.BundledPackage
    deps: list[mpkg_base.BasePackage]
    build_deps: list[mpkg_base.BasePackage]
    workdir: str | pathlib.Path
    outputdir: str | pathlib.Path
    build_source: bool
    build_debug: bool
    revision: str
    subdist: str | None
    extra_opt: bool
    jobs: int


class Target:
    def __init__(self, arch: str) -> None:
        self.arch = arch

    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def ident(self) -> str:
        raise NotImplementedError

    @property
    def triple(self) -> str:
        raise NotImplementedError

    @property
    def machine_architecture(self) -> str:
        return self.arch

    def is_portable(self) -> bool:
        return False

    def get_package_repository(self) -> mpkg_repo.Repository:
        raise NotImplementedError

    def prepare(self) -> None:
        pass

    def get_builder(self) -> type[Build]:
        raise NotImplementedError

    def build(self, request: BuildRequest) -> None:
        build = self.get_builder()(self, request)
        build.run()

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

    def get_shlib_relpath_run_time_ldflags(
        self, build: Build, path: str = ""
    ) -> list[str]:
        raise NotImplementedError

    def get_global_cflags(self, build: Build) -> list[str]:
        return []

    def get_global_cxxflags(self, build: Build) -> list[str]:
        return self.get_global_cflags(build)

    def get_global_ldflags(self, build: Build) -> list[str]:
        return []

    def is_binary_code_file(self, build: Build, path: pathlib.Path) -> bool:
        raise NotImplementedError

    def get_shlib_refs(
        self,
        build: Build,
        image_root: pathlib.Path,
        install_path: pathlib.Path,
        *,
        resolve: bool = True,
    ) -> tuple[set[pathlib.Path], set[pathlib.Path]]:
        raise NotImplementedError

    def is_allowed_system_shlib(
        self, build: Build, shlib: pathlib.Path
    ) -> bool:
        return False

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
    _sys_shlibs = {
        "libpthread",
        "libutil",
        "librt",
        "libdl",
        "libm",
        "libc",
        r"ld-linux-[\w-]+",
    }
    _sys_shlibs_re = re.compile(
        "|".join(rf"({lib}\.so(\.\d+)*)" for lib in _sys_shlibs),
        re.A,
    )

    def __init__(self, arch: str, libc: str) -> None:
        super().__init__(arch)
        self.libc = libc

    @property
    def triple(self) -> str:
        return f"{self.arch}-unknown-linux-{self.libc}"

    def get_action(self, name: str, build: Build) -> TargetAction:
        if name == "adduser":
            return LinuxAddUserAction(build)
        else:
            return super().get_action(name, build)

    def get_su_script(self, build: Build, script: str, user: str) -> str:
        return f"su '{user}' -c {shlex.quote(script)}\n"

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

    def get_shlib_relpath_run_time_ldflags(
        self, build: Build, path: str = ""
    ) -> list[str]:
        if path and path.startswith("/"):
            raise AssertionError(f"rpath must not be absolute: {path!r}")

        if path:
            rpath = f"$ORIGIN/{shlex.quote(path)}"
        else:
            rpath = f"$ORIGIN"

        # NOTE: we explicitly disable "new dtags" to get DT_RPATH,
        #       because we DO NOT want LD_LIBRARY_PATH to mess things
        #       up for us: we always want correct shared objects to load.
        #       Also, we must use the -Wl,@file approach, because of
        #       the utter insanity that is trying to quote $ORIGIN across
        #       sub-make and shell invocations.
        flags = f"-zorigin --disable-new-dtags -rpath={rpath}"
        flag_file_name = f"ld-{hashlib.md5(flags.encode()).hexdigest()}"
        flag_file_path = build.write_helper(
            flag_file_name, flags, relative_to="pkgbuild"
        )
        return [f"-Wl,@{flag_file_path}"]

    def is_binary_code_file(self, build: Build, path: pathlib.Path) -> bool:
        with open(path, "rb") as f:
            header = f.read(18)
            signature = header[:4]
            if signature == b"\x7FELF":
                if header[5] == 2:
                    byteorder = "big"
                elif header[5] == 1:
                    byteorder = "little"
                else:
                    raise AssertionError(
                        f"unexpected ELF endianness: {header[5]}"
                    )
                elf_type = int.from_bytes(
                    header[16:18], byteorder=byteorder, signed=False
                )
                return elf_type in {0x02, 0x03}  # ET_EXEC, ET_DYN

        return False

    def get_shlib_refs(
        self,
        build: Build,
        image_root: pathlib.Path,
        install_path: pathlib.Path,
        *,
        resolve: bool = True,
    ) -> tuple[set[pathlib.Path], set[pathlib.Path]]:
        # Scan the .dynamic section of the given ELF binary to find
        # which shared objects it needs and what the library runpath is.
        #
        # We have to rely on parsing the output of readelf, as there
        # seems to be no other reliable way to do this other than resorting
        # to the use of complex ELF-parsing libraries, which might be buggier
        # than binutils.
        shlib_re = re.compile(r".*\(NEEDED\)\s+Shared library: \[([^\]]+)\]")
        rpath_re = re.compile(
            r".*\((?:RPATH|RUNPATH)\)\s+Library.*path: \[([^\]]+)\]"
        )

        shlibs = set()
        rpaths = set()
        output = tools.cmd("readelf", "-d", image_root / install_path)
        for line in output.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            if m := shlib_re.match(line):
                shlibs.add(pathlib.Path(m.group(1)))
            if m := rpath_re.match(line):
                for entry in m.group(1).split(os.pathsep):
                    if entry.startswith("$ORIGIN") and resolve:
                        # $ORIGIN means the directory of the referring binary.
                        relpath = entry[len("$ORIGIN/") :]
                        rpath = (
                            pathlib.Path("/") / install_path.parent / relpath
                        )
                    else:
                        rpath = pathlib.Path(entry)
                    rpaths.add(rpath)

        return shlibs, rpaths

    def is_allowed_system_shlib(
        self, build: Build, shlib: pathlib.Path
    ) -> bool:
        return bool(self._sys_shlibs_re.fullmatch(str(shlib)))


class LinuxDistroTarget(LinuxTarget):
    def __init__(
        self, distro_info: dict[str, Any], arch: str, libc: str
    ) -> None:
        super().__init__(arch, libc)
        self.distro = distro_info

    @property
    def name(self) -> str:
        return f'{self.distro["id"]}-{self.distro["version"]}'

    @property
    def ident(self) -> str:
        return f'{self.distro["codename"]}'


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
        elif aspect == "legal":
            return (
                root
                / "usr"
                / "share"
                / "doc"
                / build.root_package.name_slot
                / "licenses"
            )
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
        request: BuildRequest,
    ) -> None:
        self._target = target
        self._io = request.io
        self._env = request.env
        self._droot = pathlib.Path(request.workdir)
        self._outputroot = pathlib.Path(request.outputdir)
        self._root_pkg = request.root_pkg
        self._deps = request.deps
        self._build_deps = request.build_deps
        self._build_source = request.build_source
        self._build_debug = request.build_debug
        self._revision = request.revision
        self._subdist = request.subdist
        self._extra_opt = request.extra_opt
        self._jobs = request.jobs
        self._bundled = [
            pkg
            for pkg in self._build_deps
            if not isinstance(
                pkg, (tgt_pkg.SystemPackage, mpkg_base.DummyPackage)
            )
            and pkg is not self._root_pkg
        ]
        self._build_only = set(self._build_deps) - set(self._deps)
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

    @property
    def channel(self) -> str:
        return self._subdist or "stable"

    @property
    def revision(self) -> str:
        return self._revision

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
        self,
        names: Iterable[str],
        *,
        recursive: bool = False,
        bundled_only: bool = True,
    ) -> set[mpkg_base.BasePackage]:
        return self._get_packages(
            names, recursive=recursive, bundled_only=bundled_only
        )

    def _get_packages(
        self,
        names: Iterable[str],
        *,
        recursive: bool,
        bundled_only: bool,
        _memo: set[str] | None = None,
    ) -> set[mpkg_base.BasePackage]:
        if _memo is None:
            _memo = set()

        packages = set()
        for name in names:
            package = self.get_package(name)
            if bundled_only and package not in self._bundled:
                continue
            _memo.add(name)
            if recursive:
                packages.update(
                    self._get_packages(
                        (
                            req.name
                            for req in package.requires
                            if req.name not in _memo
                            and req.is_activated()
                            and self._env.is_valid_for_marker(req.marker)
                        ),
                        recursive=True,
                        bundled_only=bundled_only,
                        _memo=_memo,
                    )
                )
            packages.add(package)

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
            f"<info>Building {self._root_pkg} for "
            f"{self._target.triple} ({self._target.name})</info>"
        )

        self.prepare()
        self.build()
        self.prepare_packaging()
        self.package()
        self.shrinkwrap()

    def define_tools(self) -> None:
        # Undefining MAKELEVEL is required because
        # some package makefiles have
        # conditions on MAKELEVEL.
        if self._jobs == 0:
            dash_j = f"-j{os.cpu_count()}"
        else:
            dash_j = f"-j{self._jobs}"
        self._system_tools["make"] = f"env -u MAKELEVEL make {dash_j}"
        self._system_tools["cp"] = "cp"
        self._system_tools["cargo"] = "cargo"
        self._system_tools["python"] = "python3"
        self._system_tools["install"] = "install"
        self._system_tools["patch"] = "patch"
        self._system_tools["useradd"] = "useradd"
        self._system_tools["groupadd"] = "groupadd"
        self._system_tools["sed"] = "sed"
        self._system_tools["tar"] = "tar"
        self._system_tools["bash"] = "/bin/bash"
        self._system_tools["find"] = "find"

    def prepare(self) -> None:
        self.define_tools()

    def build(self) -> None:
        raise NotImplementedError

    def prepare_packaging(self) -> None:
        self.get_intermediate_output_dir(relative_to="fsroot").mkdir(
            exist_ok=True, parents=True
        )

    def package(self) -> None:
        raise NotImplementedError

    def shrinkwrap(self) -> None:
        if not self._outputroot.exists():
            self._outputroot.mkdir(parents=True, exist_ok=True)

        pkg = self._root_pkg
        pkg_name = pkg.name
        pkg_ver = pkg.version.to_string(short=False)
        tgt_ident = self.target.ident
        tarball = f"{pkg_name}__{pkg_ver}__{tgt_ident}.tar"
        tar = self.sh_get_command("tar")
        intermediates = self.get_intermediate_output_dir(relative_to="fsroot")
        shipment = str(self.get_temp_root(relative_to="fsroot") / tarball)
        tools.cmd(
            tar,
            "--transform",
            f"flags=r;s|^\\./||",
            "-c",
            "-f",
            os.path.relpath(shipment, start=intermediates),
            ".",
            cwd=intermediates,
        )
        shutil.copy2(shipment, self._outputroot)

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

    def get_intermediate_output_dir(
        self,
        *,
        relative_to: Location = "sourceroot",
    ) -> pathlib.Path:
        return self.get_temp_root(relative_to=relative_to) / "intermediate"

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
        args: Mapping[str, str | pathlib.Path | None] | None = None,
        force_args_eq: bool = False,
        linebreaks: bool = True,
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

            elif rel_path.suffix == ".sh" or not rel_path.suffix:
                cmd = shlex.quote(str(rel_path))

            else:
                raise RuntimeError(f"unexpected tool type: {path}")

        if args is not None:
            cmd = self.sh_append_args(
                cmd, args, force_args_eq=force_args_eq, linebreaks=linebreaks
            )

        return cmd

    def sh_format_args(
        self,
        args: Mapping[str, str | pathlib.Path | None],
        *,
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

        sep = " \\\n    " if linebreaks else " "
        args_str = sep.join(args_parts)

        if linebreaks:
            args_str = textwrap.indent(args_str, " " * 4)

        return args_str

    def sh_append_args(
        self,
        cmd: str,
        args: Mapping[str, str | pathlib.Path | None],
        *,
        force_args_eq: bool = False,
        linebreaks: bool = True,
    ) -> str:
        args_str = self.sh_format_args(
            args, force_args_eq=force_args_eq, linebreaks=linebreaks
        )
        sep = " \\\n    " if linebreaks else " "
        return f"{cmd}{sep}{args_str}"

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
        result = self.sh_append_args(
            shlex.quote(str(path)),
            args,
            force_args_eq=force_args_eq,
            linebreaks=linebreaks,
        )
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
            "legal",
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
            set -Exe -o pipefail
            shopt -s dotglob nullglob

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
        return ""

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

            if not pkg.get_shlib_paths(self):
                continue

            for k, v in self.target.get_package_ld_env(self, pkg, wd).items():
                env[k].append(v)

        env_list = []
        for k, vv in env.items():
            v = ":".join(vv + list(extra) + [f"${{{k}}}"])
            env_list.append(f'{k}="{v}"')

        return env_list

    def sh_join_flags(
        self,
        flags: list[str] | tuple[str, ...],
    ) -> str:
        return '" "'.join(filter(None, flags))

    def sh_format_flags(
        self,
        flags: list[str] | tuple[str, ...],
    ) -> str:
        return self.sh_join_flags([shlex.quote(f) for f in flags if f])

    def sh_get_bundled_shlib_ldflags(
        self,
        pkg: poetry_pkg.Package,
        relative_to: Location = "pkgbuild",
    ) -> str:
        flags = []

        assert self.is_bundled(pkg)

        root_path = self.get_install_dir(pkg, relative_to=relative_to)
        for shlib_path in pkg.get_shlib_paths(self):
            # link-time dependency
            link_time = root_path / shlib_path.relative_to("/")
            flags.extend(
                self.target.get_shlib_path_link_time_ldflags(
                    self,
                    f"$(pwd -P)/{shlex.quote(str(link_time))}",
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

                for shlib in pkg.get_shlibs(self):
                    flags.append(f"-l{shlib}")

        return self.sh_join_flags(flags)

    def sh_get_bundled_shlibs_ldflags(
        self,
        deps: Iterable[poetry_pkg.Package],
        relative_to: Location = "pkgbuild",
    ) -> str:
        flags = []

        for pkg in deps:
            if self.is_bundled(pkg):
                flags.append(
                    self.sh_get_bundled_shlib_ldflags(
                        pkg, relative_to=relative_to
                    )
                )

        return self.sh_join_flags(flags)

    def sh_get_bundled_shlib_cflags(
        self,
        pkg: poetry_pkg.Package,
        relative_to: Location = "pkgbuild",
    ) -> str:
        flags = []

        assert self.is_bundled(pkg)

        root_path = self.get_install_dir(pkg, relative_to=relative_to)
        for include_path in pkg.get_include_paths(self):
            inc_path = root_path / include_path.relative_to("/")
            flags.append(f"-I$(pwd -P)/{shlex.quote(str(inc_path))}")

        return self.sh_join_flags(flags)

    def sh_get_bundled_shlibs_cflags(
        self,
        deps: Iterable[poetry_pkg.Package],
        relative_to: Location = "pkgbuild",
    ) -> str:
        flags = []

        for pkg in deps:
            if self.is_bundled(pkg):
                flags.append(
                    self.sh_get_bundled_shlib_cflags(
                        pkg, relative_to=relative_to
                    )
                )

        return self.sh_join_flags(flags)

    def sh_append_global_flags(
        self,
        args: Mapping[str, str | pathlib.Path | None] | None = None,
    ) -> dict[str, str | pathlib.Path | None]:
        global_cflags = self.target.get_global_cflags(self)
        global_cxxflags = self.target.get_global_cxxflags(self)
        global_ldflags = self.target.get_global_ldflags(self)
        if args is None:
            args = {}
        conf_args = dict(args)
        if global_cflags:
            self.sh_append_flags(conf_args, "CFLAGS", global_cflags)
        if global_cxxflags:
            self.sh_append_flags(conf_args, "CXXFLAGS", global_cxxflags)
        if global_ldflags:
            self.sh_append_flags(conf_args, "LDFLAGS", global_ldflags)
            rust_ldflags = []
            for f in global_ldflags:
                rust_ldflags.extend(["-C", f"link-arg={f}"])
            self.sh_append_flags(conf_args, "RUSTFLAGS", rust_ldflags)
        return conf_args

    def sh_configure(
        self,
        path: str | pathlib.Path,
        args: Mapping[str, str | pathlib.Path | None],
    ) -> str:
        conf_args = self.sh_append_global_flags(args)
        return self.sh_format_command(path, conf_args, force_args_eq=True)

    def sh_append_flags(
        self,
        args: dict[str, str | pathlib.Path | None],
        key: str,
        flags: list[str] | tuple[str, ...] | str,
    ) -> None:
        if isinstance(flags, str):
            flags_line = flags
        else:
            flags_line = self.sh_format_flags(flags)
        existing_flags = args.get(key)
        if existing_flags:
            assert isinstance(existing_flags, str)
            if not existing_flags.startswith("!"):
                raise AssertionError(
                    f"{key} must be pre-quoted: {existing_flags}"
                )
            args[key] = self.sh_join_flags([existing_flags, flags_line])
        else:
            args[key] = "!" + flags_line
