import textwrap
from metapkg import tools

from . import base


class BundledRustPackage(base.BundledPackage):

    @classmethod
    def resolve(cls, io, *, ref=None, version=None) -> 'BundledRustPackage':
        repo_dir = cls.resolve_vcs_source(io, ref=ref)
        out = tools.cmd('cargo', 'pkgid', cwd=repo_dir).strip()
        _, _, version = out.rpartition('#')

        package = cls(version, source_version=ref or 'HEAD')
        return package

    def get_configure_script(self, build) -> str:
        return ''

    def get_build_script(self, build) -> str:
        cargo = build.sh_get_command('cargo')
        src = build.get_source_dir(self, relative_to='pkgbuild')
        return textwrap.dedent(f'''\
            {cargo} build --release --manifest-path="{src}/Cargo.toml"
        ''')

    def get_build_install_script(self, build) -> str:
        cargo = build.sh_get_command('cargo')
        installdest = build.get_temp_dir(self, relative_to='pkgbuild')
        src = build.get_source_dir(self, relative_to='pkgbuild')
        bindir = build.get_install_path('systembin').relative_to('/')
        install_bindir = (
            build.get_install_dir(self, relative_to='pkgbuild') / bindir
        )
        return textwrap.dedent(f'''\
            {cargo} install --root "{installdest}" --path "{src}"
            mkdir -p "{install_bindir}"
            cp -a "{installdest}/bin/"* "{install_bindir}/"
        ''')
