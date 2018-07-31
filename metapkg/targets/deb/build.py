import datetime
import os
import pathlib
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
        self._debroot = self._srcroot / 'debian'
        self._tools = {}
        self._common_tools = {}
        self._system_tools = {
            'make': 'make -j{}'.format(os.cpu_count())
        }
        self._tool_wrappers = {}

        self._artifactroot = pathlib.Path('_artifacts')
        self._buildroot = self._artifactroot / 'build'
        self._tmproot = self._artifactroot / 'tmp'
        self._installroot = self._artifactroot / 'install'

        self._debroot.mkdir(parents=True)
        (self._debroot / self._tmproot).mkdir(parents=True)

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
        elif relative_to == 'pkgsource':
            return pathlib.Path('..') / path
        elif relative_to == 'pkgbuild':
            return pathlib.Path('..') / '..' / '..' / path
        elif relative_to is None:
            return (self.get_source_abspath() / path).resolve()
        else:
            raise ValueError(f'invalid relative_to argument: {relative_to}')

    def get_helpers_root(self, *, relative_to='sourceroot'):
        return self.get_dir(
            pathlib.Path('debian') / 'helpers', relative_to=relative_to)

    def get_source_root(self, *, relative_to='sourceroot'):
        return self.get_dir(pathlib.Path('.'), relative_to=relative_to)

    def get_tarball_root(self, *, relative_to='sourceroot'):
        return self.get_dir(pathlib.Path('..'), relative_to=relative_to)

    def get_patches_root(self, *, relative_to='sourceroot'):
        return self.get_dir(
            pathlib.Path('debian') / 'patches', relative_to=relative_to)

    def get_source_dir(self, package, *, relative_to='sourceroot'):
        return self.get_dir(
            pathlib.Path(package.name), relative_to=relative_to)

    def get_temp_dir(self, package, *, relative_to='sourceroot'):
        return self.get_dir(
            self._tmproot / package.name, relative_to=relative_to)

    def get_temp_root(self, *, relative_to='sourceroot'):
        return self.get_dir(
            self._tmproot, relative_to=relative_to)

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
        self.unpack_sources()
        self.prepare_patches()
        self._write_common_bits()
        self._write_control()
        self._write_changelog()
        self._write_rules()
        self._dpkg_buildpackage()

    def _write_common_bits(self):
        debsource = self._debroot / 'source'
        debsource.mkdir()
        with open(debsource / 'format', 'w') as f:
            f.write('3.0 (quilt)\n')
        with open(self._debroot / 'compat', 'w') as f:
            f.write('9\n')

    def _write_control(self):
        deps = ',\n '.join(f'{dep.system_name} (= {dep.pretty_version})'
                           for dep in self._deps
                           if isinstance(dep, targets.SystemPackage))

        control = textwrap.dedent('''\
            Source: {name}
            Priority: optional
            Maintainer: {maintainer}
            Standards-Version: 4.1.5
            Build-Depends:
             debhelper (>= 9~),
             dh-exec (>= 0.13~),
             dpkg-dev (>= 1.16.1~),
             {deps}

            Package: {name}
            Architecture: any
            Depends:
             ${{misc:Depends}},
             ${{shlibs:Depends}}
            Description:
             {description}
        ''').format(
            name=self._root_pkg.name,
            deps=deps,
            description=self._root_pkg.description,
            maintainer='MagicStack Inc. <hello@magic.io>',
        )

        with open(self._debroot / 'control', 'w') as f:
            f.write(control)

    def _write_changelog(self):
        changelog = textwrap.dedent('''\
            {name} ({version}) {distro}; urgency=medium

              * New version.

             -- {maintainer}  {date}
        ''').format(
            name=self._root_pkg.name,
            version=f'{self._root_pkg.version.text}-1',
            distro=self._target.distro['codename'],
            maintainer='MagicStack Inc. <hello@magic.io>',
            date=datetime.datetime.now(datetime.timezone.utc).strftime(
                '%a, %d %b %Y %H:%M:%S %z'
            )
        )

        with open(self._debroot / 'changelog', 'w') as f:
            f.write(changelog)

    def _write_rules(self):
        rules = textwrap.dedent('''\
            #!/usr/bin/make -f

            include /usr/share/dpkg/architecture.mk

            {target_global_rules}

            DPKG_EXPORT_BUILDFLAGS = 1
            include /usr/share/dpkg/buildflags.mk

            # Facilitate hierarchical profile generation on amd64 (#730134)
            ifeq ($(DEB_HOST_ARCH),amd64)
            CFLAGS+= -fno-omit-frame-pointer
            endif

            export DPKG_GENSYMBOLS_CHECK_LEVEL=4

            %:
            \tdh $@

            override_dh_auto_configure-indep: stamp/configure-build
            override_dh_auto_configure-arch: stamp/configure-build
            override_dh_auto_build-indep: stamp/build
            override_dh_auto_build-arch: stamp/build

            stamp/configure-build:
            \tmkdir -p stamp _artifacts
            {configure_steps}
            \ttouch "$@"

            stamp/build: stamp/configure-build
            {build_steps}
            \ttouch "$@"

            override_dh_auto_install-arch:
            {build_install_steps}

            override_dh_install-arch:
            {install_steps}

            override_dh_auto_clean:
            \trm -rf stamp
        ''').format(
            name=self._root_pkg.name,
            target_global_rules=self._target.get_global_rules(),
            configure_steps=self._write_script('configure'),
            build_steps=self._write_script('build'),
            build_install_steps=self._write_script(
                'build_install', installable_only=True),
            install_steps=self._write_script(
                'install', installable_only=True),
        )

        with open(self._debroot / 'rules', 'w') as f:
            f.write(rules)
            os.fchmod(f.fileno(), 0o755)

    def _get_package_install_script(self, pkg) -> str:
        source_root = self.get_source_root(relative_to='pkgbuild')
        install_dir = self.get_install_dir(pkg, relative_to='sourceroot')
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

        trim_install = self.sh_get_command(
            'trim-install', relative_to='sourceroot')

        return textwrap.dedent(f'''
            pushd "{source_root}" >/dev/null

            {il_script} > "{temp_dir}/install"
            {nil_script} > "{temp_dir}/not-installed"
            {ignore_script} > "{temp_dir}/ignored"

            {trim_install} "{temp_dir}/install" \\
                "{temp_dir}/not-installed" "{temp_dir}/ignored" \\
                "{install_dir}" > "debian/{self._root_pkg.name}.install"

            dh_install --sourcedir="{install_dir}" --fail-missing

            popd >/dev/null
        ''')

    def _dpkg_buildpackage(self):
        env = os.environ.copy()
        env['DEBIAN_FRONTEND'] = 'noninteractive'

        tools.cmd(
            'apt-get', 'update',
            env=env,
            cwd=str(self._srcroot),
            stdout=self._io.output.stream,
            stderr=subprocess.STDOUT)

        tools.cmd(
            'apt-get', 'install', '-y', '--no-install-recommends',
            'equivs', 'devscripts',
            env=env,
            cwd=str(self._srcroot),
            stdout=self._io.output.stream,
            stderr=subprocess.STDOUT)

        tools.cmd(
            'mk-build-deps', '-t', 'apt-get -y --no-install-recommends',
            '-i', str(self._debroot / 'control'),
            env=env,
            cwd='/tmp',
            stdout=self._io.output.stream,
            stderr=subprocess.STDOUT)

        tools.cmd(
            'dpkg-buildpackage', '-us', '-uc',
            '--source-option=--create-empty-orig',
            cwd=str(self._srcroot),
            stdout=self._io.output.stream,
            stderr=subprocess.STDOUT)
