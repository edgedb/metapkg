import datetime
import textwrap

from metapkg import targets
from metapkg import tools

from . import base


class BundledRustPackage(base.BundledPackage):
    @classmethod
    def resolve(cls, io, *, ref=None, version=None) -> "BundledRustPackage":
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

    def get_configure_script(self, build) -> str:
        return ""

    def get_build_script(self, build) -> str:
        return ""

    def get_build_install_script(self, build) -> str:
        cargo = build.sh_get_command("cargo")
        installdest = build.get_temp_dir(self, relative_to="pkgbuild")
        src = build.get_source_dir(self, relative_to="pkgbuild")
        bindir = build.get_install_path("systembin").relative_to("/")
        install_bindir = (
            build.get_install_dir(self, relative_to="pkgbuild") / bindir
        )
        if isinstance(build.target, targets.generic.GenericLinuxTarget):
            target = "--target x86_64-unknown-linux-musl"
        else:
            target = ""
        return textwrap.dedent(
            f"""\
            sed -i -e '/\\[package\\]/,/\\[.*\\]/{{
                    s/^version\\s*=.*/version = "{self.version.text}"/;
                }}' \\
                "{src}/Cargo.toml"
            {cargo} install {target} \\
                --root "{installdest}" \\
                --path "{src}" \\
                --locked
            mkdir -p "{install_bindir}"
            cp -a "{installdest}/bin/"* "{install_bindir}/"
        """
        )
