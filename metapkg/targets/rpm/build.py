import datetime
import glob
import os
import pathlib
import platform
import subprocess
import textwrap

from metapkg import targets
from metapkg import tools


class Build(targets.Build):

    def run(self):
        self._io.writeln(f'<info>Building {self._root_pkg} on '
                         f'{self._target.distro["id"]}-'
                         f'{self._target.distro["version"]}</info>')

        self._pkgroot = self._droot / self._root_pkg.name
        self._srcroot = self._pkgroot / self._root_pkg.name

        self._buildroot = pathlib.Path('BUILD')
        self._tmproot = pathlib.Path('TEMP')
        self._installroot = pathlib.Path('INSTALL')

        self._system_tools['make'] = f'make -j{os.cpu_count()}'

        self._build()

    def get_source_abspath(self):
        return self._srcroot

    def get_path(self, path, *, relative_to):
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
            return pathlib.Path('..') / '..' / path
        elif relative_to == 'pkgbuild':
            return pathlib.Path('..') / '..' / path
        elif relative_to is None:
            return (self.get_source_abspath() / path).resolve()
        else:
            raise ValueError(f'invalid relative_to argument: {relative_to}')

    def get_helpers_root(self, *, relative_to='sourceroot'):
        return self.get_dir(
            self.get_tarball_root() / 'helpers', relative_to=relative_to)

    def get_source_root(self, *, relative_to='sourceroot'):
        return self.get_dir(pathlib.Path('.'), relative_to=relative_to)

    def get_tarball_root(self, *, relative_to='sourceroot'):
        return self.get_dir(pathlib.Path('SOURCES'), relative_to=relative_to)

    def get_patches_root(self, *, relative_to='sourceroot'):
        return self.get_tarball_root(relative_to=relative_to)

    def get_spec_root(self, *, relative_to='sourceroot'):
        return self.get_dir(pathlib.Path('SPECS'), relative_to=relative_to)

    def get_source_dir(self, package, *, relative_to='sourceroot'):
        return self.get_dir(
            pathlib.Path('BUILD') / package.name, relative_to=relative_to)

    def get_temp_dir(self, package, *, relative_to='sourceroot'):
        return self.get_dir(
            self._tmproot / package.name, relative_to=relative_to)

    def get_temp_root(self, *, relative_to='sourceroot'):
        return self.get_dir(
            self._tmproot, relative_to=relative_to)

    def get_image_root(self, *, relative_to='sourceroot'):
        return self.get_dir(
            pathlib.Path('BUILDROOT') / self._root_pkg.name,
            relative_to=relative_to)

    def get_build_dir(self, package, *, relative_to='sourceroot'):
        return self.get_dir(
            self._buildroot / package.name, relative_to=relative_to)

    def get_install_dir(self, package, *, relative_to='sourceroot'):
        return self.get_dir(
            self._installroot / package.name, relative_to=relative_to)

    def _get_tarball_tpl(self, package):
        rp = self._root_pkg
        return (
            f'{rp.name}_{rp.version.text}.orig-{package.name}.tar{{comp}}'
        )

    def _build(self):
        self.prepare_tools()
        self.prepare_tarballs()
        self.prepare_patches()
        self._write_spec()
        self._rpmbuild()

    def _write_spec(self):
        rules = textwrap.dedent('''\
            Name: {name}
            Version: {version}
            Release: 1%{{?dist}}
            Summary: {description}
            License: {license}
            URL: {url}
            Group: {group}

            BuildRequires: bash
            {build_reqs}
            {runtime_reqs}

            {source_spec}
            {patch_spec}

            %description
            {long_description}

            %global _privatelibs {privatelibs}
            %global __provides_exclude ^.*\.so$
            %global __requires_exclude ^(%{{_privatelibs}})$

            %define __python python3

            %debug_package

            %prep
            {unpack_script}
            {patch_script}

            %build
            {configure_script}
            {build_script}
            {build_install_script}

            %install
            {install_script}

            %files -f {temp_root}/install.list

            %changelog
            {changelog}
        ''').format(
            name=self._root_pkg.name,
            description=self._root_pkg.description,
            long_description=self._root_pkg.description,
            license=self._root_pkg.license,
            url=self._root_pkg.url,
            group=self._root_pkg.group,
            version=self._root_pkg.pretty_version,
            build_reqs=self._get_build_reqs_spec(),
            runtime_reqs=self._get_runtime_reqs_spec(),
            source_spec=self._get_source_spec(),
            patch_spec=self._get_patch_spec(),
            patch_script=self._get_patch_script(),
            unpack_script=self._write_script(
                'unpack', relative_to='buildroot'),
            configure_script=self._write_script(
                'configure', relative_to='buildroot'),
            build_script=self._write_script(
                'build', relative_to='buildroot'),
            build_install_script=self._write_script(
                'build_install', installable_only=True,
                relative_to='buildroot'),
            install_script=self._write_script(
                'install', installable_only=True,
                relative_to='buildroot'),
            temp_root=self.get_temp_root(relative_to='buildroot'),
            privatelibs=self._get_private_libs_pattern(),
            changelog=self._get_changelog(),
        )

        spec_root = self.get_spec_root(relative_to=None)
        with open(spec_root / f'{self._root_pkg.name}.spec', 'w') as f:
            f.write(rules)

    def _get_changelog(self):
        changelog = textwrap.dedent('''\
            * {date} {maintainer} {version}
            - New version.
        ''').format(
            maintainer='MagicStack Inc. <hello@magic.io>',
            version=f'{self._root_pkg.version.text}-1',
            date=datetime.datetime.now(datetime.timezone.utc).strftime(
                '%a %b %d %Y'
            )
        )

        return changelog

    def _get_private_libs_pattern(self):
        private_libs = set()

        for pkg in self._installable:
            private_libs.update(pkg.get_private_libraries(self))

        return '|'.join(private_libs)

    def _get_build_reqs_spec(self):
        lines = []

        deps = (pkg for pkg in self._build_deps
                if isinstance(pkg, targets.SystemPackage))
        for pkg in deps:
            lines.append(f'BuildRequires: {pkg.system_name}')

        return '\n'.join(lines)

    def _get_runtime_reqs_spec(self):
        lines = []

        deps = (pkg for pkg in self._deps
                if isinstance(pkg, targets.SystemPackage))
        for pkg in deps:
            lines.append(f'Requires: {pkg.system_name}')

        return '\n'.join(lines)

    def _get_source_spec(self):
        lines = []

        for i, tarball in enumerate(self._tarballs.values()):
            lines.append(f'Source{i}: {tarball.name}')

        return '\n'.join(lines)

    def _get_patch_spec(self):
        lines = []

        for i, patch in enumerate(self._patches):
            lines.append(f'Patch{i}: {patch}')

        return '\n'.join(lines)

    def _get_patch_script(self):
        lines = []

        for i, patch in enumerate(self._patches):
            lines.append(f'%patch -P {i} -p1')

        return '\n'.join(lines)

    def _get_package_unpack_script(self, pkg) -> str:
        tarball_root = self.get_tarball_root(relative_to='pkgbuild')
        tarball = tarball_root / self._tarballs[pkg]
        ext = tarball.suffix
        if ext == '.bz2':
            compflag = 'j'
        elif ext == '.gz':
            compflag = 'z'
        elif ext == '.tar':
            compflag = ''
        else:
            raise NotImplementedError(f'tar{ext} files are not supported')

        src_dir = self.get_source_dir(pkg, relative_to='pkgbuild')

        return textwrap.dedent(f'''
            pushd "{src_dir}" >/dev/null
            /usr/bin/tar -x{compflag} -f {tarball} --strip-components=1
            popd >/dev/null
        ''')

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

            rsync -av "{install_dir}/" "{image_root}/"

            while IFS= read -r path; do
                if [ -d "{install_dir}/${{path}}" ]; then
                    echo %dir /${{path}} >> "{temp_root}/install.list"
                else
                    echo /${{path}} >> "{temp_root}/install.list"
                fi
            done < <(cat "{temp_dir}/install.final")

            popd >/dev/null
        ''')

    def _rpmbuild(self):
        tools.cmd(
            'yum-builddep', '-y', f'{self._root_pkg.name}.spec',
            cwd=str(self.get_spec_root(relative_to=None)),
            stdout=self._io.output.stream,
            stderr=subprocess.STDOUT
        )

        image_root = self.get_image_root(relative_to=None)

        tools.cmd(
            'yum', 'install', '-y', 'rpm-build', 'rpmlint',
            stdout=self._io.output.stream,
            stderr=subprocess.STDOUT
        )

        tools.cmd(
            'rpmbuild', '-ba', f'{self._root_pkg.name}.spec',
            f'--define=%_topdir {self._srcroot}',
            f'--buildroot={image_root}',
            '--verbose',
            cwd=str(self.get_spec_root(relative_to=None)),
            stdout=self._io.output.stream,
            stderr=subprocess.STDOUT)

        tools.cmd(
            'rpmlint', '-i', f'{self._root_pkg.name}.spec',
            cwd=str(self.get_spec_root(relative_to=None)),
            stdout=self._io.output.stream,
            stderr=subprocess.STDOUT)

        rpms = self.get_dir('RPMS', relative_to=None) / platform.machine()

        for rpm in glob.glob(str(rpms / '*.rpm')):
            tools.cmd(
                'rpmlint', '-i', rpm,
                stdout=self._io.output.stream,
                stderr=subprocess.STDOUT)
