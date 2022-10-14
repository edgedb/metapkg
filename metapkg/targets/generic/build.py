from __future__ import annotations

import collections
import json
import os
import os.path
import pathlib
import platform
import shlex
import subprocess
import textwrap
import zipfile

import magic

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

        self._artifactroot = pathlib.Path("..") / "_artifacts"
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
            return (
                pathlib.Path("..")
                / ".."
                / ".."
                / self._root_pkg.name_slot
                / path
            )
        elif relative_to == "fsroot":
            return (self.get_source_abspath() / path).resolve()
        else:
            raise ValueError(f"invalid relative_to argument: {relative_to}")

    def get_helpers_root(
        self, *, relative_to: targets.Location = "sourceroot"
    ) -> pathlib.Path:
        return self.get_dir(
            self._artifactroot / "helpers", relative_to=relative_to
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

    def package(self) -> None:
        files = self._list_installed_files()
        self._fixup_binaries(files)
        self._package(files)

    def _apply_patches(self) -> None:
        proot = self.get_patches_root(relative_to="fsroot")
        patch_cmd = shlex.split(self.sh_get_command("patch"))
        dep_root = self.get_dir("thirdparty", relative_to="fsroot")
        my_root = self.get_dir("..", relative_to="fsroot")
        for pkgname, patchname in self._patches:
            sroot = my_root if pkgname == self.root_package.name else dep_root
            patch = proot / patchname
            tools.cmd(
                *(patch_cmd + ["-p1", "-i", str(patch)]),
                cwd=sroot,
            )

    def _get_global_env_vars(self) -> dict[str, str]:
        return {}

    def _write_makefile(self) -> None:
        temp_root = self.get_temp_root(relative_to="sourceroot")
        image_root = self.get_image_root(relative_to="sourceroot")

        makefile = textwrap.dedent(
            """\
            .PHONY: build install

            ROOT = $(dir $(realpath $(firstword $(MAKEFILE_LIST))))

            export SHELL = {bash}

            {env}

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
            env="\n".join(
                f"export {var} = {val}"
                for var, val in self._get_global_env_vars().items()
            ),
        )

        with open(self._srcroot / "Makefile.metapkg", "w") as f:
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
            relpath = os.path.relpath(cmd.relative_to("/"), start=bindir)
            cmdname = f"{cmd.name}{pkg.slot_suffix}"
            lines.append(
                f'ln -sf "{relpath}" "{install_dir / bindir}/{cmdname}"'
            )
            lines.append(f"echo {bindir / cmdname}")

        extras_dir = self.get_extras_root(relative_to="fsroot")
        for path, content in pkg.get_service_scripts(self).items():
            directory = extras_dir / path.parent.relative_to("/")
            directory.mkdir(parents=True, exist_ok=True)
            with open(directory / path.name, "w") as f:
                print(content, file=f)

            lines.append(f'echo {path.relative_to("/")}')

        return "\n".join(lines)

    def _build(self) -> None:
        make = self.sh_get_command("make", relative_to="sourceroot")
        command = shlex.split(make)
        command.extend(["-f", "Makefile.metapkg"])
        tools.cmd(
            *command,
            cwd=str(self._srcroot),
            stdout=self._io.output.stream,
            stderr=subprocess.STDOUT,
        )

    def _list_installed_files(self) -> list[pathlib.Path]:
        image_root = self.get_image_root(relative_to="sourceroot")
        find = self.sh_get_command("find", relative_to="sourceroot")
        listing = (
            tools.cmd(
                find,
                image_root,
                "-type",
                "f",
                "-o",
                "-type",
                "l",
                cwd=str(self._srcroot),
            )
            .strip()
            .split("\n")
        )
        return [
            pathlib.Path(entry).relative_to(image_root) for entry in listing
        ]

    def _fixup_rpath(
        self, image_root: pathlib.Path, binary_relpath: pathlib.Path
    ) -> None:
        pass

    def _strip(
        self, image_root: pathlib.Path, binary_relpath: pathlib.Path
    ) -> None:
        pass

    def _fixup_binaries(self, files: list[pathlib.Path]) -> None:
        # Here we examine all produced executables for references to
        # shared libraries outside of what's bundled in the package and
        # what's allowed to be linked to on the target system (typically,
        # just the C library).
        image_root = self.get_image_root(relative_to="fsroot")
        bin_paths: dict[str, set[pathlib.Path]] = collections.defaultdict(set)
        binaries = set()
        refs = {}
        root = pathlib.Path("/")
        symlinks = []
        # First, build the list of all binaries and their shlib references.
        for file in files:
            full_path = image_root / file
            inst_path = root / file
            if full_path.is_symlink():
                # We'll deal with symlinks separately below.
                symlinks.append((inst_path, full_path))
                continue

            if self.target.is_binary_code_file(self, full_path):
                bin_paths[file.name].add(inst_path)
                binaries.add(inst_path)
                self._strip(image_root, file)
                if self.target.is_dynamically_linked(self, full_path):
                    self._fixup_rpath(image_root, file)
                    refs[inst_path] = self.target.get_shlib_refs(
                        self, image_root, file
                    )

        # Now, scan for all symbolic links to binaries
        # (it is common for .so files to be symlinks to their
        # fully-versioned counterparts).
        for inst_path, full_path in symlinks:
            target = full_path.readlink()
            if not target.is_absolute():
                target = full_path.parent / target
            else:
                target = image_root / target.relative_to("/")
            target_inst_path = root / target.relative_to(image_root)
            if target_inst_path in binaries:
                bin_paths[inst_path.name].add(inst_path)

        # Finally, do the sanity check.
        for binary, (shlibs, rpaths) in refs.items():
            for shlib_path in shlibs:
                if self.target.is_allowed_system_shlib(self, shlib_path):
                    continue
                shlib = str(shlib_path)
                bundled = bin_paths.get(shlib, set())
                if any(
                    (rpath / shlib).resolve() in bundled for rpath in rpaths
                ):
                    continue

                rpath_list = ":".join(str(rpath) for rpath in rpaths)
                if rpath_list:
                    raise AssertionError(
                        f"{binary} links to {shlib} which is neither an"
                        f" allowed system library, nor a bundled library"
                        f" in rpath: {rpath_list}"
                    )
                else:
                    raise AssertionError(
                        f"{binary} links to {shlib} which is not an"
                        f" allowed system library, and {binary} does"
                        f" not define a library rpath"
                    )

    def _package(self, files: list[pathlib.Path]) -> None:
        pkg = self._root_pkg
        title = pkg.name

        src_root = self.get_source_abspath()
        image_root = self.get_image_root(relative_to="fsroot")

        version = packages.pep440_to_semver(pkg.version)
        an = f"{title}-{version}"
        if not pkg.version_includes_revision():
            an = f"{an}_{self._revision}"
        archives = self.get_intermediate_output_dir()
        archives_abs = self.get_intermediate_output_dir(relative_to="fsroot")

        if pkg.get_package_layout(self) is packages.PackageFileLayout.FLAT:
            if len(files) == 1:
                fn = files[0]
                dest = f"{archives / an}{fn.suffix}"
                tools.cmd(
                    "cp",
                    image_root / fn,
                    dest,
                    cwd=src_root,
                )

                mime = magic.from_file(str(src_root / dest), mime=True)

                tools.cmd(
                    "zstd",
                    "-19",
                    f"{an}{fn.suffix}",
                    cwd=archives_abs,
                )

                installrefs = [
                    f"{an}{fn.suffix}",
                    f"{an}{fn.suffix}.zst",
                ]

                installrefs_ct = {
                    f"{an}{fn.suffix}": {
                        "type": mime,
                        "encoding": "identity",
                        "suffix": fn.suffix,
                    },
                    f"{an}{fn.suffix}.zst": {
                        "type": mime,
                        "encoding": "zstd",
                        "suffix": ".zst",
                    },
                }
            else:
                raise AssertionError(
                    "Single-file package build produced multiple files!"
                )
        else:
            src = image_root / self.get_full_install_prefix().relative_to("/")
            tarball = f"{an}.tar"
            tools.cmd(
                self.sh_get_command("tar"),
                "--transform",
                f"flags=r;s|^\\./|{an}/|",
                "-c",
                "-f",
                os.path.relpath(archives_abs / tarball, start=src),
                ".",
                cwd=src,
            )

            tools.cmd(
                "zstd",
                "-19",
                tarball,
                cwd=archives_abs,
            )

            tools.cmd(
                "gzip",
                "-9",
                tarball,
                cwd=archives_abs,
            )

            installrefs = [
                f"{tarball}.gz",
                f"{tarball}.zst",
            ]

            installrefs_ct = {
                f"{tarball}.gz": {
                    "type": "application/x-tar",
                    "encoding": "gzip",
                    "suffix": ".tar.gz",
                },
                f"{tarball}.zst": {
                    "type": "application/x-tar",
                    "encoding": "zstd",
                    "suffix": ".tar.zst",
                },
            }

        with open(archives_abs / "build-metadata.json", "w") as vf:
            json.dump(
                {
                    "installrefs": installrefs,
                    "contents": installrefs_ct,
                    "repository": "generic",
                    **self._root_pkg.get_artifact_metadata(self),
                },
                vf,
            )
