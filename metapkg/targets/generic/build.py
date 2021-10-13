from __future__ import annotations

import json
import os
import pathlib
import shlex
import subprocess
import textwrap
import zipfile

from metapkg import packages
from metapkg import targets
from metapkg import tools


class Build(targets.Build):
    _srcroot: pathlib.Path
    _pkgroot: pathlib.Path

    def prepare(self) -> None:
        super().prepare()

        self._pkgroot = self._droot / self._root_pkg.name_slot
        self._srcroot = self._pkgroot / self._root_pkg.name_slot

        self._artifactroot = pathlib.Path("_artifacts")
        self._buildroot = self._artifactroot / "build"
        self._tmproot = self._artifactroot / "tmp"
        self._installroot = self._artifactroot / "install"

    def get_source_abspath(self) -> pathlib.Path:
        return self._srcroot

    def get_path(
        self,
        path: str | pathlib.Path,
        *,
        relative_to: targets.Location,
        package: packages.BasePackage | None = None,
    ) -> pathlib.Path:
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
        elif relative_to == "buildroot":
            return pathlib.Path("..") / path
        elif relative_to == "pkgsource":
            if (
                package is not None
                and package.name == self.root_package.name_slot
            ):
                return pathlib.Path(path)
            else:
                return pathlib.Path("..") / ".." / path
        elif relative_to == "pkgbuild":
            return pathlib.Path("..") / ".." / ".." / path
        elif relative_to == "fsroot":
            return (self.get_source_abspath() / path).resolve()
        else:
            raise ValueError(f"invalid relative_to argument: {relative_to}")

    def get_helpers_root(
        self, *, relative_to: targets.Location = "sourceroot"
    ) -> pathlib.Path:
        return self.get_dir(
            pathlib.Path("build") / "helpers", relative_to=relative_to
        )

    def get_source_root(
        self, *, relative_to: targets.Location = "sourceroot"
    ) -> pathlib.Path:
        return self.get_dir(pathlib.Path("."), relative_to=relative_to)

    def get_tarball_root(
        self, *, relative_to: targets.Location = "sourceroot"
    ) -> pathlib.Path:
        return self.get_dir(
            self._tmproot / "tarballs", relative_to=relative_to
        )

    def get_patches_root(
        self, *, relative_to: targets.Location = "sourceroot"
    ) -> pathlib.Path:
        return self.get_tarball_root(relative_to=relative_to)

    def get_extras_root(
        self, *, relative_to: targets.Location = "sourceroot"
    ) -> pathlib.Path:
        return self.get_source_root(relative_to=relative_to) / "extras"

    def get_spec_root(
        self, *, relative_to: targets.Location = "sourceroot"
    ) -> pathlib.Path:
        return self.get_dir(pathlib.Path("SPECS"), relative_to=relative_to)

    def get_source_dir(
        self,
        package: packages.BasePackage,
        *,
        relative_to: targets.Location = "sourceroot",
    ) -> pathlib.Path:
        if package.name == self.root_package.name_slot:
            return self.get_dir(".", relative_to=relative_to)
        else:
            return self.get_dir(
                pathlib.Path("thirdparty") / package.name,
                relative_to=relative_to,
                package=package,
            )

    def get_temp_dir(
        self,
        package: packages.BasePackage,
        *,
        relative_to: targets.Location = "sourceroot",
    ) -> pathlib.Path:
        return self.get_dir(
            self._tmproot / package.name,
            relative_to=relative_to,
            package=package,
        )

    def get_temp_root(
        self, *, relative_to: targets.Location = "sourceroot"
    ) -> pathlib.Path:
        return self.get_dir(self._tmproot, relative_to=relative_to)

    def get_image_root(
        self, *, relative_to: targets.Location = "sourceroot"
    ) -> pathlib.Path:
        return self.get_dir(
            self._tmproot / "buildroot" / self._root_pkg.name_slot,
            relative_to=relative_to,
        )

    def get_build_dir(
        self,
        package: packages.BasePackage,
        *,
        relative_to: targets.Location = "sourceroot",
    ) -> pathlib.Path:
        return self.get_dir(
            self._buildroot / package.name,
            relative_to=relative_to,
            package=package,
        )

    def get_install_dir(
        self,
        package: packages.BasePackage,
        *,
        relative_to: targets.Location = "sourceroot",
    ) -> pathlib.Path:
        return self.get_dir(
            self._installroot / package.name,
            relative_to=relative_to,
            package=package,
        )

    def _get_tarball_tpl(self, package: packages.BasePackage) -> str:
        rp = self._root_pkg
        return f"{rp.name}_{rp.version.text}.orig-{package.name}.tar{{comp}}"

    def build(self) -> None:
        self.prepare_tools()
        self.prepare_tarballs()
        self.prepare_patches()
        self.unpack_sources()
        self._apply_patches()
        self._write_makefile()
        self._build()
        self._package()

    def _apply_patches(self) -> None:
        proot = self.get_patches_root(relative_to="fsroot")
        patch_cmd = shlex.split(self.sh_get_command("patch"))
        sroot = self.get_dir("thirdparty", relative_to="fsroot")
        for patchname in self._patches:
            patch = proot / patchname
            tools.cmd(
                *(patch_cmd + ["-p1", "-i", str(patch)]),
                cwd=sroot,
            )

    def _write_makefile(self) -> None:
        temp_root = self.get_temp_root(relative_to="sourceroot")
        image_root = self.get_image_root(relative_to="sourceroot")

        makefile = textwrap.dedent(
            """\
            .PHONY: build install

            export SHELL = {bash}

            DESTDIR := /

            {temp_root}/stamp/build:
            \t{build_script}
            \t{install_script}
            \tmkdir -p "{temp_root}/stamp"
            \ttouch "{temp_root}/stamp/build"

            build: {temp_root}/stamp/build

            install: build
            \t{copy_tree} -v "{image_root}/" "$(DESTDIR)"

        """
        ).format(
            bash=self.sh_get_command("bash"),
            temp_root=temp_root,
            image_root=image_root,
            build_script=self._write_script(
                "complete", relative_to="sourceroot"
            ),
            install_script=self._write_script(
                "install", relative_to="sourceroot", installable_only=True
            ),
            copy_tree=self.sh_get_command(
                "copy-tree", relative_to="sourceroot"
            ),
        )

        with open(self._srcroot / "Makefile", "w") as f:
            f.write(makefile)

    def _get_package_install_script(self, pkg: packages.BasePackage) -> str:
        source_root = self.get_source_root(relative_to="pkgbuild")
        install_dir = self.get_install_dir(pkg, relative_to="sourceroot")
        image_root = self.get_image_root(relative_to="sourceroot")
        temp_root = self.get_temp_root(relative_to="sourceroot")
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

        ignored_dep_text = self._get_package_script(pkg, "ignored_dependency")
        ignored_dep_script = self.sh_write_bash_helper(
            f"_gen_ignored_deps_{pkg.unique_name}.sh",
            ignored_dep_text,
            relative_to="sourceroot",
        )

        extras_text = self._get_package_extras_script(pkg)
        extras_script = self.sh_write_bash_helper(
            f"_install_extras_{pkg.unique_name}.sh",
            extras_text,
            relative_to="sourceroot",
        )
        trim_install = self.sh_get_command(
            "trim-install", relative_to="sourceroot"
        )
        copy_tree = self.sh_get_command("copy-tree", relative_to="sourceroot")

        return textwrap.dedent(
            f"""
            pushd "{source_root}" >/dev/null

            {il_script} > "{temp_dir}/install"
            {nil_script} > "{temp_dir}/not-installed"
            {ignore_script} > "{temp_dir}/ignored"
            {ignored_dep_script} >> "{temp_root}/ignored-reqs"

            {trim_install} "{temp_dir}/install" \\
                "{temp_dir}/not-installed" "{temp_dir}/ignored" \\
                "{install_dir}" > "{temp_dir}/install.list"

            {extras_script} >> "{temp_dir}/install.list"

            {copy_tree} -v --files-from="{temp_dir}/install.list" \\
                "{install_dir}/" "{image_root}/"

            popd >/dev/null
        """
        )

    def _get_package_extras_script(self, pkg: packages.BasePackage) -> str:
        lines = []
        install_dir = self.get_install_dir(pkg, relative_to="sourceroot")
        bindir = self.get_install_path("systembin").relative_to("/")

        lines.append(f'mkdir -p "{install_dir / bindir}"')
        for cmd in pkg.get_exposed_commands(self):
            cmdname = f"{cmd.name}{pkg.slot_suffix}"
            lines.append(f'ln -sf "{cmd}" "{install_dir / bindir}/{cmdname}"')
            lines.append(f"echo {bindir / cmdname}")

        return "\n".join(lines)

    def _build(self) -> None:
        make = self.sh_get_command("make", relative_to="sourceroot")
        command = shlex.split(make)
        tools.cmd(
            *command,
            cwd=str(self._srcroot),
            stdout=self._io.output.stream,
            stderr=subprocess.STDOUT,
        )

    def _package(self) -> None:
        pkg = self._root_pkg
        title = pkg.name

        image_root = self.get_image_root(relative_to="sourceroot")
        find = self.sh_get_command("find", relative_to="sourceroot")
        files = (
            tools.cmd(
                find,
                image_root,
                "-type",
                "f",
                cwd=str(self._srcroot),
            )
            .strip()
            .split("\n")
        )

        if not self._outputroot.exists():
            self._outputroot.mkdir(parents=True, exist_ok=True)
        elif tuple(self._outputroot.iterdir()):
            raise RuntimeError(
                f"target directory {self._outputroot} is not empty"
            )

        version = pkg.pretty_version
        suffix = self._revision
        if self._subdist:
            suffix = f"{suffix}~{self._subdist}"
        an = f"{title}{pkg.slot_suffix}_{version}_{suffix}"

        with open(self._outputroot / "package-version.json", "w") as f:
            json.dump(
                {
                    "installref": an,
                    **self._root_pkg.get_artifact_metadata(self),
                },
                f,
            )

        if pkg.get_package_layout(self) is packages.PackageFileLayout.FLAT:
            if len(files) == 1:
                fn = pathlib.Path(files[0])
                tools.cmd(
                    "cp",
                    str(self._srcroot / files[0]),
                    f"{self._outputroot / an}{fn.suffix}",
                )

                return
            else:
                with zipfile.ZipFile(
                    self._outputroot / f"{an}.zip",
                    mode="w",
                    compression=zipfile.ZIP_DEFLATED,
                ) as z:
                    for file in files:
                        z.write(
                            str(self._srcroot / file),
                            arcname=pathlib.Path(file).name,
                        )
        else:
            with zipfile.ZipFile(
                self._outputroot / f"{an}.zip",
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
            ) as z:
                for file in files:
                    z.write(
                        str(self._srcroot / file),
                        arcname=(
                            an / pathlib.Path(file).relative_to(image_root)
                        ),
                    )
