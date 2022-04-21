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
    def resolve(
        cls,
        io: cleo_io.IO,
        *,
        ref: str | None = None,
        version: str | None = None,
        revision: str | None = None,
        is_release: bool = False,
        target: targets.Target,
    ) -> BundledRustPackage:
        repo_dir = cls.resolve_vcs_source(io, ref=ref)
        out = tools.cmd("cargo", "pkgid", cwd=repo_dir).strip()
        pretty_version: str | None

        if version is None:
            _, _, version_base = out.rpartition("#")
            git_rev = cls.resolve_vcs_version(io)

            if not is_release:
                commits = tools.cmd(
                    "git",
                    "rev-list",
                    "--count",
                    git_rev,
                    cwd=repo_dir,
                )

                version_base = (
                    poetry_version.Version.parse(version_base)
                    .replace(
                        local=None,
                        pre=None,
                        dev=poetry_pep440.ReleaseTag("dev", commits),
                    )
                    .to_string(short=False)
                )

            git_date = tools.cmd(
                "git",
                "show",
                "-s",
                "--format=%cd",
                "--date=format-local:%Y%m%d%H",
                git_rev,
                env={**os.environ, **{"TZ": "UTC", "LANG": "C"}},
                cwd=repo_dir,
            )
            if not revision:
                revision = "1"
            metadata = ".".join(
                (
                    f"r{revision}",
                    f"d{git_date}",
                    f"g{git_rev[:9]}",
                )
            )
            pv = f"{version_base}+{metadata}"
            version_hash = hashlib.sha256(pv.encode("utf-8")).hexdigest()
            pretty_version = f"{pv}.s{version_hash[:7]}"
            version = f"{version_base}+{version_hash[:7]}"
        else:
            pretty_version = None

        package = cls(
            version,
            pretty_version=pretty_version,
            source_version=ref or "HEAD",
        )
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

    def version_includes_revision(self) -> bool:
        return True
