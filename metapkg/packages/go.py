from __future__ import annotations

import pathlib
import shlex
import textwrap

from metapkg import targets
from metapkg import tools

from . import base


class BundledGoPackage(base.BuildSystemMakePackage):
    def get_package_layout(
        self,
        build: targets.Build,
    ) -> base.PackageFileLayout:
        return base.PackageFileLayout.FLAT

    def get_configure_script(self, build: targets.Build) -> str:
        sdir = shlex.quote(
            str(build.get_source_dir(self, relative_to="pkgbuild"))
        )
        copy_sources = f"test ./ -ef {sdir} || cp -a {sdir}/* ./"
        return copy_sources

    def get_build_install_script(self, build: targets.Build) -> str:
        installdest = build.get_install_dir(self, relative_to="pkgbuild")
        bindir = build.get_install_path("systembin").relative_to("/")
        dest = installdest / bindir / self.name
        return textwrap.dedent(
            f"""\
            mkdir -p "$(dirname "{dest}")"
            cp -a "bin/{self.name}" "{dest}"
            """
        )

    def get_file_install_entries(self, build: targets.Build) -> list[str]:
        entries = list(super().get_file_install_entries(build))
        entries.append(f"{{systembindir}}/{self.name}{{exesuffix}}")
        return entries


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
