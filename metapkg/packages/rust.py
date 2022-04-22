from __future__ import annotations
from typing import (
    TYPE_CHECKING,
)

import hashlib
import os
import textwrap

from poetry.core.semver import version as poetry_version
from poetry.core.version import pep440 as poetry_pep440

from metapkg import targets
from metapkg import tools

from . import base

if TYPE_CHECKING:
    from cleo.io import io as cleo_io


class BundledRustPackage(base.BundledPackage):
    @classmethod
    def version_from_vcs_version(
        cls,
        io: cleo_io.IO,
        repo: tools.git.Git,
        vcs_version: str,
        is_release: bool,
    ) -> str:
        out = tools.cmd("cargo", "pkgid", cwd=repo.work_tree).strip()
        _, _, version = out.rpartition("#")

        if not is_release:
            commits = repo.run(
                "rev-list",
                "--count",
                vcs_version,
            )

            version = (
                poetry_version.Version.parse(version)
                .replace(
                    local=None,
                    pre=None,
                    dev=poetry_pep440.ReleaseTag("dev", commits),
                )
                .to_string(short=False)
            )

        return version

    def get_configure_script(self, build: targets.Build) -> str:
        return ""

    def get_build_script(self, build: targets.Build) -> str:
        return ""

    def get_build_install_script(self, build: targets.Build) -> str:
        script = super().get_build_install_script(build)
        cargo = build.sh_get_command("cargo")
        sed = build.sh_get_command("sed")
        installdest = build.get_temp_dir(self, relative_to="pkgbuild")
        src = build.get_source_dir(self, relative_to="pkgbuild")
        bindir = build.get_install_path("systembin").relative_to("/")
        install_bindir = (
            build.get_install_dir(self, relative_to="pkgbuild") / bindir
        )
        if isinstance(build.target, targets.linux.LinuxMuslTarget):
            target = "--target x86_64-unknown-linux-musl"
        else:
            target = ""

        env = build.sh_append_global_flags({})
        env["RUST_BACKTRACE"] = "1"
        env_str = build.sh_format_command("env", env, force_args_eq=True)
        script += textwrap.dedent(
            f"""\
            {sed} -i -e '/\\[package\\]/,/\\[.*\\]/{{
                    s/^version\\s*=.*/version = "{self.version.text}"/;
                }}' \\
                "{src}/Cargo.toml"
            {env_str} \\
                {cargo} install {target} \\
                    --verbose --verbose \\
                    --root "{installdest}" \\
                    --path "{src}" \\
                    --locked
            mkdir -p "{install_bindir}"
            cp -a "{installdest}/bin/"* "{install_bindir}/"
            """
        )
        return script

    def version_includes_revision(self) -> bool:
        return True
