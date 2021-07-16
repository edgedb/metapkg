from __future__ import annotations
from typing import *

import datetime
import os
import pathlib
import shlex
import shutil
import stat
import subprocess
import textwrap

from metapkg import targets
from metapkg import tools


class Build(targets.Build):
    def prepare(self):
        super().prepare()

        self._pkgroot = self._droot / self._root_pkg.name_slot
        self._srcroot = self._pkgroot / self._root_pkg.name_slot
        self._debroot = self._srcroot / "debian"

        # Undefining MAKELEVEL is required because debian/rules
        # is a Makefile, and some package makefiles have
        # conditions on MAKELEVEL.
        self._system_tools["make"] = "env -u MAKELEVEL make -j{}".format(
            os.cpu_count()
        )
        self._system_tools["cargo"] = "cargo"
        self._system_tools["python"] = "python3"
        self._system_tools["cp"] = "cp"
        self._system_tools["install"] = "install"
        self._system_tools["useradd"] = "useradd"
        self._system_tools["groupadd"] = "groupadd"

        self._artifactroot = pathlib.Path("_artifacts")
        self._buildroot = self._artifactroot / "build"
        self._tmproot = self._artifactroot / "tmp"
        self._installroot = self._artifactroot / "install"

        self._debroot.mkdir(parents=True)
        (self._debroot / self._tmproot).mkdir(parents=True)

        self._bin_shims = self._root_pkg.get_bin_shims(self)

    def get_source_abspath(self):
        return self._srcroot

    def get_path(self, path, *, relative_to, package=None):
        """Return *path* relative to *relative_to* location.

        :param pathlike path:
            A path relative to bundle source root.

        :param str relative_to:
            Location name.  Can be one of:
              - ``'sourceroot'``: bundle source root
              - ``'pkgsource'``: package source directory
              - ``'pkgbuild'``: package build directory
              - ``None``: filesystem root (makes path absolute)

        :return:
            Path relative to the specified location.
        """

        if relative_to == "sourceroot":
            return pathlib.Path(path)
        elif relative_to == "pkgsource":
            return pathlib.Path("..") / path
        elif relative_to == "pkgbuild":
            return pathlib.Path("..") / ".." / ".." / path
        elif relative_to is None:
            return (self.get_source_abspath() / path).resolve()
        else:
            raise ValueError(f"invalid relative_to argument: {relative_to}")

    def get_spec_root(self, *, relative_to="sourceroot"):
        return self.get_dir(pathlib.Path("debian"), relative_to=relative_to)

    def get_helpers_root(self, *, relative_to="sourceroot"):
        return self.get_dir(
            pathlib.Path("debian") / "helpers", relative_to=relative_to
        )

    def get_source_root(self, *, relative_to="sourceroot"):
        return self.get_dir(pathlib.Path("."), relative_to=relative_to)

    def get_tarball_root(self, *, relative_to="sourceroot"):
        return self.get_dir(pathlib.Path(".."), relative_to=relative_to)

    def get_patches_root(self, *, relative_to="sourceroot"):
        return self.get_dir(
            pathlib.Path("debian") / "patches", relative_to=relative_to
        )

    def get_extras_root(self, *, relative_to="sourceroot"):
        return self.get_dir(
            pathlib.Path("debian") / "extras", relative_to=relative_to
        )

    def get_source_dir(self, package, *, relative_to="sourceroot"):
        return self.get_dir(
            pathlib.Path(package.name), relative_to=relative_to
        )

    def get_temp_dir(self, package, *, relative_to="sourceroot"):
        return self.get_dir(
            self._tmproot / package.name, relative_to=relative_to
        )

    def get_temp_root(self, *, relative_to="sourceroot"):
        return self.get_dir(self._tmproot, relative_to=relative_to)

    def get_build_dir(self, package, *, relative_to="sourceroot"):
        return self.get_dir(
            self._buildroot / package.name, relative_to=relative_to
        )

    def get_install_dir(self, package, *, relative_to="sourceroot"):
        return self.get_dir(
            self._installroot / package.name, relative_to=relative_to
        )

    def _get_tarball_tpl(self, package):
        rp = self._root_pkg
        return f"{rp.name}_{rp.version.text}.orig-{package.name}.tar{{comp}}"

    def build(self):
        self.prepare_tools()
        self.prepare_tarballs()
        self.unpack_sources()
        self.prepare_patches()
        self._write_common_bits()
        self._write_control()
        self._write_changelog()
        self._write_rules()
        self._write_scriptlets()
        self._dpkg_buildpackage()

    def _write_common_bits(self):
        debsource = self._debroot / "source"
        debsource.mkdir()
        with open(debsource / "format", "w") as f:
            f.write("3.0 (quilt)\n")
        with open(self._debroot / "compat", "w") as f:
            f.write("9\n")

    def _write_control(self):
        build_deps = ",\n ".join(
            f"{dep.system_name} (>= {dep.pretty_version})"
            for dep in self._build_deps
            if isinstance(dep, targets.SystemPackage)
        )

        deps = ",\n ".join(
            f"{dep.system_name} (>= {dep.pretty_version})"
            for dep in self._deps
            if isinstance(dep, targets.SystemPackage)
        )

        base_name = self._root_pkg.name
        name = self._root_pkg.name_slot

        if self._bin_shims:
            common_package = textwrap.dedent(
                """\
                Package: {name}-common
                Architecture: any
                Description:
                 Support files for {title}.
            """
            ).format(
                name=base_name,
                title=self._root_pkg.title,
            )
            deps += f",\n {base_name}-common (>= {self._root_pkg.version})"
        else:
            common_package = ""

        distro = self._target.distro["codename"]
        if self._subdist:
            distro = f"{distro}.{self._subdist}"
        root_version = (
            f"{self._root_pkg.version.text}-{self._revision}~{distro}"
        )

        meta_pkgs = self._root_pkg.get_meta_packages(self, root_version)
        meta_pkg_specs = []
        for meta_pkg in meta_pkgs:
            meta_pkg_spec = textwrap.dedent(
                """\
                Package: {name}
                Architecture: any
                Description:
                 {description}
                Depends:
                 {dependencies}
            """
            ).format(
                name=meta_pkg.name,
                description=meta_pkg.description,
                dependencies=",\n ".join(
                    f'{dep_name}{f" ({dep_ver})" if dep_ver else ""}'
                    for dep_name, dep_ver in meta_pkg.dependencies.items()
                ),
            )
            meta_pkg_specs.append(meta_pkg_spec)

        conflicts = self._root_pkg.get_conflict_packages(self, root_version)
        if conflicts:
            conflicts_list = ",\n ".join(conflicts)
            conflicts_spec = (
                "\n"
                + textwrap.dedent(
                    """\
                Conflicts:
                 {conflicts_list}
                Replaces:
                 {conflicts_list}
            """
                )
                .format(
                    conflicts_list=conflicts_list,
                )
                .rstrip()
            )
        else:
            conflicts_spec = ""

        provides = self._root_pkg.get_provided_packages(self, root_version)
        if provides:
            provides_list = ",\n ".join(
                f"{pkg} (= {ver})" for pkg, ver in provides
            )
            provides_spec = (
                "\n"
                + textwrap.dedent(
                    """\
                Provides:
                 {provides_list}
            """
                )
                .format(
                    provides_list=provides_list,
                )
                .rstrip()
            )
        else:
            provides_spec = ""

        control = textwrap.dedent(
            """\
            Source: {name}
            Priority: optional
            Section: {section}
            Maintainer: {maintainer}
            Standards-Version: 4.1.5
            Build-Depends:
             debhelper (>= 9~),
             dh-exec (>= 0.13~),
             dpkg-dev (>= 1.16.1~),
             {build_deps}

            Package: {name}
            Architecture: any
            Depends:
             {deps},
             ${{misc:Depends}},
             ${{shlibs:Depends}}{conflicts_spec}{provides_spec}
            Description:
             {description}

            {common_pkg}

            {meta_pkgs}
        """
        ).format(
            name=name,
            deps=deps,
            build_deps=build_deps,
            conflicts_spec=conflicts_spec,
            provides_spec=provides_spec,
            section=self._target.get_package_group(self._root_pkg),
            description=self._root_pkg.description,
            maintainer="MagicStack Inc. <hello@magic.io>",
            common_pkg=common_package,
            meta_pkgs="\n\n".join(meta_pkg_specs),
        )

        with open(self._debroot / "control", "w") as f:
            f.write(control)

        # Make sure we don't export any shlibs from the bundle.
        with open(self._debroot / f"{name}.shlibs", "w") as f:
            f.write("")

    def _write_changelog(self):
        distro = self._target.distro["codename"]
        if self._subdist:
            distro = f"{distro}.{self._subdist}"

        changelog = textwrap.dedent(
            """\
            {name} ({version}) {distro}; urgency=medium

              * New version.

             -- {maintainer}  {date}
        """
        ).format(
            name=f"{self._root_pkg.name_slot}",
            version=f"{self._root_pkg.version.text}-{self._revision}~{distro}",
            distro=distro,
            maintainer="MagicStack Inc. <hello@magic.io>",
            date=datetime.datetime.now(datetime.timezone.utc).strftime(
                "%a, %d %b %Y %H:%M:%S %z"
            ),
        )

        with open(self._debroot / "changelog", "w") as f:
            f.write(changelog)

    def _write_rules(self):
        shlib_paths = self._get_bundled_shlib_paths()
        if shlib_paths:
            shlib_paths_spec = ':'.join(
                shlex.quote(str(p)) for p in shlib_paths
            )
            shlib_paths_opt = f"-l {shlib_paths_spec}"
        else:
            shlib_paths_opt = ""

        rules = textwrap.dedent(
            """\
            #!/usr/bin/make -f

            include /usr/share/dpkg/architecture.mk

            {target_global_rules}

            DPKG_EXPORT_BUILDFLAGS = 1
            include /usr/share/dpkg/buildflags.mk

            # Facilitate hierarchical profile generation on amd64 (#730134)
            ifeq ($(DEB_HOST_ARCH),amd64)
            CFLAGS+= -fno-omit-frame-pointer
            endif

            export DPKG_GENSYMBOLS_CHECK_LEVEL=4

            %:
            \tdh $@

            override_dh_auto_configure-indep: stamp/configure-build
            override_dh_auto_configure-arch: stamp/configure-build
            override_dh_auto_build-indep: stamp/build
            override_dh_auto_build-arch: stamp/build

            stamp/configure-build:
            \tmkdir -p stamp _artifacts
            \ttouch "$@"

            stamp/build: stamp/configure-build
            {build_steps}
            \ttouch "$@"

            override_dh_auto_install-arch:
            {install_extras}

            override_dh_strip:
            \t{strip_steps}

            override_dh_install-arch:
            {install_steps}

            override_dh_auto_clean:
            \trm -rf stamp

            override_dh_shlibdeps:
            \tdh_shlibdeps {shlib_paths}
        """
        ).format(
            name=self._root_pkg.name_slot,
            target_global_rules=self._target.get_global_rules(),
            build_steps=self._write_script("complete"),
            install_extras=textwrap.indent(self._get_install_extras(), "\t"),
            install_steps=self._write_script("install", installable_only=True),
            strip_steps=(
                "dh_strip --automatic-dbgsym"
                if self._build_debug
                else "dh_strip --no-automatic-dbgsym"
            ),
            shlib_paths=shlib_paths_opt,
        )

        with open(self._debroot / "rules", "w") as f:
            f.write(rules)
            os.fchmod(f.fileno(), 0o755)

    def _write_scriptlets(self):

        stagemap = {
            "before_install": "preinst",
            "after_install": "postinst",
            "before_uninstall": "prerm",
            "after_uninstall": "postrm",
        }

        for genstage, debstage in stagemap.items():
            script = self.get_script(genstage, installable_only=True)
            if script:
                stagefile = f"{self.root_package.name_slot}.{debstage}"
                spec_root = self.get_spec_root(relative_to=None)
                with open(spec_root / stagefile, "w") as f:
                    print("#!/bin/bash\nset -e", file=f)
                    print(script, file=f)

    def _get_package_install_script(self, pkg) -> str:
        source_root = self.get_source_root(relative_to="pkgbuild")
        install_dir = self.get_install_dir(pkg, relative_to="sourceroot")
        temp_dir = self.get_temp_dir(pkg, relative_to="sourceroot")

        il_script_text = self._get_package_script(pkg, "install_list")
        il_script = self.sh_write_bash_helper(
            f"_gen_install_list_{pkg.unique_name}.sh",
            il_script_text,
            relative_to="sourceroot",
        )

        nil_script_text = self._get_package_script(pkg, "no_install_list")
        nil_script = self.sh_write_bash_helper(
            f"_gen_no_install_list_{pkg.unique_name}.sh",
            nil_script_text,
            relative_to="sourceroot",
        )

        ignore_script_text = self._get_package_script(pkg, "ignore_list")
        ignore_script = self.sh_write_bash_helper(
            f"_gen_ignore_list_{pkg.unique_name}.sh",
            ignore_script_text,
            relative_to="sourceroot",
        )

        trim_install = self.sh_get_command(
            "trim-install", relative_to="sourceroot"
        )

        return textwrap.dedent(
            f"""
            pushd "{source_root}" >/dev/null

            {il_script} > "{temp_dir}/install"
            {nil_script} > "{temp_dir}/not-installed"
            {ignore_script} > "{temp_dir}/ignored"

            {trim_install} "{temp_dir}/install" \\
                "{temp_dir}/not-installed" "{temp_dir}/ignored" \\
                "{install_dir}" > "debian/{self._root_pkg.name_slot}.install"

            dh_install --sourcedir="{install_dir}" --fail-missing

            popd >/dev/null
        """
        )

    def _get_bundled_shlib_paths(self) -> List[str]:
        paths = []

        for pkg in self._installable:
            paths.extend(pkg.get_shlib_paths(self))

        return paths

    def _get_install_extras(self) -> str:
        lines = []
        symlinks = []

        extras_dir = self.get_extras_root(relative_to=None)
        sys_bindir = self.get_install_path("systembin").relative_to("/")

        for pkg in self._installable:
            for path, content in pkg.get_service_scripts(self).items():
                directory = extras_dir / path.parent.relative_to("/")
                directory.mkdir(parents=True)
                with open(directory / path.name, "w") as f:
                    print(content, file=f)

            for cmd in pkg.get_exposed_commands(self):
                symlinks.append(
                    (
                        cmd.relative_to("/"),
                        f"{sys_bindir}/{cmd.name}{pkg.slot_suffix}",
                    )
                )

        if symlinks:
            spec_root = self.get_spec_root(relative_to=None)
            links = spec_root / f"{self.root_package.name_slot}.links"
            with open(links, "w") as f:
                print(
                    "\n".join(f"{src} {dst}" for src, dst in symlinks), file=f
                )

            lines.append("dh_link")

        if self._bin_shims:
            extras_dir_rel = self.get_extras_root(relative_to="sourceroot")

            dest = shlex.quote(
                str(self._debroot / f"{self._root_pkg.name}-common")
            )

            sysbindir = self.get_install_path("systembin")

            for path, data in self._bin_shims.items():
                bin_path = (sysbindir / path).relative_to("/")
                inst_path = extras_dir / bin_path
                inst_path.parent.mkdir(parents=True, exist_ok=True)
                with open(inst_path, "w") as f:
                    f.write(data)
                os.chmod(
                    inst_path,
                    stat.S_IRWXU
                    | stat.S_IRGRP
                    | stat.S_IXGRP
                    | stat.S_IROTH
                    | stat.S_IXOTH,
                )

                src_path = extras_dir_rel / bin_path
                src = shlex.quote(str(src_path))

                dest_path = dest / bin_path
                dst = shlex.quote(str(dest_path))
                dstdir = shlex.quote(str(dest_path.parent))

                lines.append(f"mkdir -p {dstdir}")
                lines.append(f"cp -p {src} {dst}")

        return "\n".join(lines)

    def _dpkg_buildpackage(self):
        env = os.environ.copy()
        env["DEBIAN_FRONTEND"] = "noninteractive"

        tools.cmd(
            "apt-get",
            "update",
            env=env,
            cwd=str(self._srcroot),
            stdout=self._io.output.stream,
            stderr=subprocess.STDOUT,
        )

        tools.cmd(
            "apt-get",
            "install",
            "-y",
            "--no-install-recommends",
            "equivs",
            "devscripts",
            env=env,
            cwd=str(self._srcroot),
            stdout=self._io.output.stream,
            stderr=subprocess.STDOUT,
        )

        tools.cmd(
            "mk-build-deps",
            "-t",
            "apt-get -y --no-install-recommends",
            "-i",
            str(self._debroot / "control"),
            env=env,
            cwd="/tmp",
            stdout=self._io.output.stream,
            stderr=subprocess.STDOUT,
        )

        args = ["-us", "-uc", "--source-option=--create-empty-orig"]
        if not self._build_source:
            args.append("-b")

        tools.cmd(
            "dpkg-buildpackage",
            *args,
            cwd=str(self._srcroot),
            stdout=self._io.output.stream,
            stderr=subprocess.STDOUT,
        )

        if self._outputroot is not None:
            if not self._outputroot.exists():
                self._outputroot.mkdir(parents=True, exist_ok=True)

            # Ubuntu likes to call their dbgsym packages ddebs,
            # whereas Debian tools, including reprepro like it
            # to just be a .deb.
            for changes in self._pkgroot.glob("*.changes"):
                with open(changes, "r+t") as f:
                    f.seek(0)
                    patched = f.read().replace(".ddeb", ".deb")
                    f.seek(0)
                    f.write(patched)

            for entry in self._pkgroot.iterdir():
                if not entry.is_dir():
                    if entry.suffix == ".ddeb":
                        output_name = entry.stem + ".deb"
                    else:
                        output_name = entry.name
                    shutil.copy2(entry, self._outputroot / output_name)
