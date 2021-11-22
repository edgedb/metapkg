from __future__ import annotations
from typing import (
    TYPE_CHECKING,
)

import datetime
import textwrap

from metapkg import targets
from metapkg import tools

from . import base

if TYPE_CHECKING:
    from cleo.io import io as cleo_io


class BundledRustPackage(base.BundledPackage):
    @classmethod
    def resolve(
        cls,
        io: cleo_io.IO,
        *,
        ref: str | None = None,
        version: str | None = None,
        is_release: bool = False,
    ) -> BundledRustPackage:
        repo_dir = cls.resolve_vcs_source(io, ref=ref)
        out = tools.cmd("cargo", "pkgid", cwd=repo_dir).strip()

        if version is None:
            _, _, version = out.rpartition("#")
            git_rev = cls.resolve_version(io)
            curdate = datetime.datetime.now(tz=datetime.timezone.utc)
            curdate_str = curdate.strftime(r"%Y%m%d")
            version = f"{version}+d{curdate_str}.g{git_rev[:9]}"

        package = cls(version, source_version=ref or "HEAD")
        return package

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
