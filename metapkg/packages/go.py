from __future__ import annotations

import pathlib
import textwrap

from metapkg import targets
from metapkg import tools

from . import base


class BundledGoPackage(base.BuildSystemMakePackage):
    @property
    def supports_out_of_tree_builds(self) -> bool:
        return False

    def get_package_layout(
        self,
        build: targets.Build,
    ) -> base.PackageFileLayout:
        return base.PackageFileLayout.SINGLE_BINARY

    def get_make_install_target(self, build: targets.Build) -> str:
        return ""

    def get_build_install_script(self, build: targets.Build) -> str:
        if (
            self.get_package_layout(build)
            is not base.PackageFileLayout.SINGLE_BINARY
        ):
            script = super().get_build_install_script(build)
        else:
            script = ""
        installdest = build.get_build_install_dir(self, relative_to="pkgbuild")
        outdir = self.get_binary_output_dir()
        bindir = build.get_install_path(self, "bin").relative_to("/")
        dest = installdest / bindir / self.name
        return textwrap.dedent(
            f"""\
            {script}
            mkdir -p "$(dirname "{dest}")"
            cp -a "{outdir / self.name}" "{dest}"
            """
        )

    def get_file_install_entries(self, build: targets.Build) -> list[str]:
        entries = list(super().get_file_install_entries(build))
        entries.append(f"{{bindir}}/{self.name}{{exesuffix}}")
        return entries

    def get_exposed_commands(self, build: targets.Build) -> list[pathlib.Path]:
        bindir = build.get_install_path(self, "bin")

        return [
            bindir / self.name,
        ]


class BundledAdHocGoPackage(BundledGoPackage):
    sources = [
        {
            "url": "file://{dirname}/go",
        },
    ]

    @classmethod
    def version_from_source(
        cls,
        source_dir: pathlib.Path,
    ) -> str:
        out = tools.cmd("grep", "VERSION=", "Makefile", cwd=source_dir).strip()
        _, _, version = out.rpartition("=")
        return version.strip()
