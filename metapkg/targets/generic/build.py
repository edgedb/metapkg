import os
import pathlib
import shlex
import subprocess
import textwrap

from metapkg import targets
from metapkg import tools


class Build(targets.Build):

    def prepare(self):
        super().prepare()

        self._pkgroot = self._droot / self._root_pkg.name
        self._srcroot = self._pkgroot / self._root_pkg.name

        # MAKELEVEL=0 is required because
        # some package makefiles have
        # conditions on MAKELEVEL.
        self._system_tools['make'] = \
            'make MAKELEVEL=0 -j{}'.format(os.cpu_count())
        self._system_tools['install'] = 'install'
        self._system_tools['patch'] = 'patch'
        self._system_tools['useradd'] = 'useradd'
        self._system_tools['groupadd'] = 'groupadd'

        self._artifactroot = pathlib.Path('_artifacts')
        self._buildroot = self._artifactroot / 'build'
        self._tmproot = self._artifactroot / 'tmp'
        self._installroot = self._artifactroot / 'install'

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

        if relative_to == 'sourceroot':
            return pathlib.Path(path)
        elif relative_to == 'buildroot':
            return pathlib.Path('..') / path
        elif relative_to == 'pkgsource':
            if package is not None and package.name == self.root_package.name:
                return pathlib.Path(path)
            else:
                return pathlib.Path('..') / '..' / path
        elif relative_to == 'pkgbuild':
            return pathlib.Path('..') / '..' / '..' / path
        elif relative_to is None:
            return (self.get_source_abspath() / path).resolve()
        else:
            raise ValueError(f'invalid relative_to argument: {relative_to}')

    def get_helpers_root(self, *, relative_to='sourceroot'):
        return self.get_dir(
            pathlib.Path('build') / 'helpers', relative_to=relative_to)

    def get_source_root(self, *, relative_to='sourceroot'):
        return self.get_dir(pathlib.Path('.'), relative_to=relative_to)

    def get_tarball_root(self, *, relative_to='sourceroot'):
        return self.get_dir(self._tmproot / 'tarballs',
                            relative_to=relative_to)

    def get_patches_root(self, *, relative_to='sourceroot'):
        return self.get_tarball_root(relative_to=relative_to)

    def get_extras_root(self, *, relative_to='sourceroot'):
        return self.get_tarball_root(relative_to=relative_to) / 'extras'

    def get_spec_root(self, *, relative_to='sourceroot'):
        return self.get_dir(pathlib.Path('SPECS'), relative_to=relative_to)

    def get_source_dir(self, package, *, relative_to='sourceroot'):
        if package.name == self.root_package.name:
            return self.get_dir('.', relative_to=relative_to)
        else:
            return self.get_dir(
                pathlib.Path('thirdparty') / package.name,
                relative_to=relative_to, package=package)

    def get_temp_dir(self, package, *, relative_to='sourceroot'):
        return self.get_dir(
            self._tmproot / package.name, relative_to=relative_to,
            package=package)

    def get_temp_root(self, *, relative_to='sourceroot'):
        return self.get_dir(
            self._tmproot, relative_to=relative_to)

    def get_image_root(self, *, relative_to='sourceroot'):
        return self.get_dir(
            self._tmproot / 'buildroot' / self._root_pkg.name,
            relative_to=relative_to)

    def get_build_dir(self, package, *, relative_to='sourceroot'):
        return self.get_dir(
            self._buildroot / package.name, relative_to=relative_to,
            package=package)

    def get_install_dir(self, package, *, relative_to='sourceroot'):
        return self.get_dir(
            self._installroot / package.name, relative_to=relative_to,
            package=package)

    def _get_tarball_tpl(self, package):
        rp = self._root_pkg
        return (
            f'{rp.name}_{rp.version.text}.orig-{package.name}.tar{{comp}}'
        )

    def build(self):
        self.prepare_tools()
        self.prepare_tarballs()
        self.prepare_patches()
        self.unpack_sources()
        self._apply_patches()
        self._write_makefile()
        self._build()

    def _apply_patches(self):
        proot = self.get_patches_root(relative_to=None)
        patch_cmd = shlex.split(self.sh_get_command('patch'))
        sroot = self.get_dir('thirdparty', relative_to=None)
        for patchname in self._patches:
            patch = proot / patchname
            tools.cmd(
                *(patch_cmd + ['-p1', '-i', patch]),
                cwd=sroot,
            )

    def _write_makefile(self):
        temp_root = self.get_temp_root(relative_to='sourceroot')
        image_root = self.get_image_root(relative_to='sourceroot')

        makefile = textwrap.dedent('''\
            .PHONY: build install

            {temp_root}/stamp/build:
            \t{build_script}
            \t{install_script}
            \tmkdir -p "{temp_root}/stamp"
            \ttouch "{temp_root}/stamp/build"

            build: {temp_root}/stamp/build

            install: build
            \trsync -arv --omit-dir-times --relative --no-perms --no-owner \\
            \t\t--no-group --executability \\
            \t\t--files-from="{temp_root}/install.list" "{image_root}/" /

        ''').format(
            temp_root=temp_root,
            image_root=image_root,
            build_script=self._write_script(
                'complete', relative_to='sourceroot'),
            install_script=self._write_script(
                'install', relative_to='sourceroot', installable_only=True),
        )

        with open(self._srcroot / 'Makefile', 'w') as f:
            f.write(makefile)

    def _get_package_install_script(self, pkg) -> str:
        source_root = self.get_source_root(relative_to='pkgbuild')
        install_dir = self.get_install_dir(pkg, relative_to='sourceroot')
        image_root = self.get_image_root(relative_to='sourceroot')
        temp_root = self.get_temp_root(relative_to='sourceroot')
        temp_dir = self.get_temp_dir(pkg, relative_to='sourceroot')

        il_script_text = self._get_package_script(pkg, 'install_list')
        il_script = self.sh_write_bash_helper(
            f'_gen_install_list_{pkg.unique_name}.sh', il_script_text,
            relative_to='sourceroot')

        nil_script_text = self._get_package_script(pkg, 'no_install_list')
        nil_script = self.sh_write_bash_helper(
            f'_gen_no_install_list_{pkg.unique_name}.sh', nil_script_text,
            relative_to='sourceroot')

        ignore_script_text = self._get_package_script(pkg, 'ignore_list')
        ignore_script = self.sh_write_bash_helper(
            f'_gen_ignore_list_{pkg.unique_name}.sh', ignore_script_text,
            relative_to='sourceroot')

        ignored_dep_text = self._get_package_script(pkg, 'ignored_dependency')
        ignored_dep_script = self.sh_write_bash_helper(
            f'_gen_ignored_deps_{pkg.unique_name}.sh', ignored_dep_text,
            relative_to='sourceroot')

        extras_text = self._get_package_extras_script(pkg)
        extras_script = self.sh_write_bash_helper(
            f'_install_extras_{pkg.unique_name}.sh', extras_text,
            relative_to='sourceroot')

        trim_install = self.sh_get_command(
            'trim-install', relative_to='sourceroot')

        return textwrap.dedent(f'''
            pushd "{source_root}" >/dev/null

            {il_script} > "{temp_dir}/install"
            {nil_script} > "{temp_dir}/not-installed"
            {ignore_script} > "{temp_dir}/ignored"
            {ignored_dep_script} >> "{temp_root}/ignored-reqs"

            {trim_install} "{temp_dir}/install" \\
                "{temp_dir}/not-installed" "{temp_dir}/ignored" \\
                "{install_dir}" > "{temp_dir}/install.final"

            cat "{temp_dir}/install.final" >> "{temp_root}/install.list"

            {extras_script} >> "{temp_root}/install.list"

            rsync -av "{install_dir}/" "{image_root}/"

            popd >/dev/null
        ''')

    def _get_package_extras_script(self, pkg) -> str:
        lines = []
        install_dir = self.get_install_dir(pkg, relative_to='sourceroot')
        bindir = (self.target.get_install_root(self) / 'bin').relative_to('/')

        lines.append(f'mkdir -p "{install_dir / bindir}"')
        for cmd in pkg.get_exposed_commands(self):
            lines.append(f'ln -sf "{cmd}" "{install_dir / bindir}/{cmd.name}"')
            lines.append(f'echo {bindir / cmd.name}')

        return '\n'.join(lines)

    def _build(self):
        make = self.sh_get_command('make', relative_to='sourceroot')
        command = shlex.split(make)
        tools.cmd(
            *command,
            cwd=str(self._srcroot),
            stdout=self._io.output.stream,
            stderr=subprocess.STDOUT)
