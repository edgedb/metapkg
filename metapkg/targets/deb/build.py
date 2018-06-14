import datetime
import os
import pathlib
import re
import shutil
import shlex
import subprocess
import tempfile
import textwrap

from metapkg import targets
from metapkg import tools
from metapkg.packages import sources as af_sources
from metapkg.targets import _helpers as helpers_pkg


class Build(targets.Build):

    def run(self):
        self._io.writeln(f'<info>Building {self._root_pkg} on '
                         f'{self._target.distro["id"]}-'
                         f'{self._target.distro["version"]}</info>')

        # td = tempfile.TemporaryDirectory(prefix='metapkg')
        # d = td.name
        d = '/tmp/metapkg'

        self._droot = pathlib.Path(d)
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

    def get_dir(self, path, *, relative_to):
        absolute_path = (self.get_source_abspath() / path).resolve()
        if not absolute_path.exists():
            absolute_path.mkdir(parents=True)

        return self.get_path(path, relative_to=relative_to)

    def get_helpers_root(self, *, relative_to='sourceroot'):
        return self.get_dir(
            pathlib.Path('debian') / 'helpers', relative_to=relative_to)

    def get_source_root(self, *, relative_to='sourceroot'):
        return self.get_dir(pathlib.Path('.'), relative_to=relative_to)

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

    def sh_get_command(self, command, *, relative_to='pkgbuild'):
        path = self._tools.get(command)
        if not path:
            path = self._common_tools.get(command)

        if not path:
            # This is an unclaimed command.  Assume system executable.
            system_tool = self._system_tools.get(command)
            if not system_tool:
                raise RuntimeError(
                    f'unrecognized command: {command}')

            # System tools are already properly quoted shell commands.
            cmd = system_tool

        else:
            rel_path = self.get_path(path, relative_to=relative_to)
            if rel_path.suffix == '.py':
                python = self.sh_get_command(
                    'python', relative_to=relative_to)

                cmd = f'{python} {shlex.quote(str(rel_path))}'

            elif not rel_path.suffix:
                cmd = shlex.quote(str(rel_path))

            else:
                raise RuntimeError(
                    f'unexpected tool type: {path}')

        return cmd

    def write_helper(self, name: str, text: str, *, relative_to: str) -> str:
        """Write an executable helper and return it's shell-escaped name."""

        helpers_abs = self.get_helpers_root(relative_to=None)
        helpers_rel = self.get_helpers_root(relative_to=relative_to)

        with open(helpers_abs / name, 'w') as f:
            print(text, file=f)
            os.fchmod(f.fileno(), 0o755)

        return f'{helpers_rel / name}'

    def sh_write_helper(
            self, name: str, text: str, *, relative_to: str) -> str:
        cmd = self.write_helper(name, text, relative_to=relative_to)
        return f'{shlex.quote(cmd)}'

    def sh_write_python_helper(
            self, name: str, text: str, *, relative_to: str) -> str:

        python = self.sh_get_command('python', relative_to=relative_to)
        path = self.sh_write_helper(name, text, relative_to=relative_to)

        return f'{shlex.quote(python)} {path}'

    def sh_write_bash_helper(
            self, name: str, text: str, *, relative_to: str) -> str:
        script = textwrap.dedent('''\
            #!/bin/bash
            set -ex

            {text}
        ''').format(text=text)

        return self.sh_write_helper(name, script, relative_to=relative_to)

    def _get_tarball_tpl(self, package):
        rp = self._root_pkg
        return (
            f'{rp.name}_{rp.version.text}.orig-{package.name}.tar{{comp}}'
        )

    def _build(self):
        for pkg in self._bundled:
            tools = pkg.get_build_tools(self)
            if tools:
                self._tools.update(tools)

        self._prepare_sources()
        self._copy_helpers()
        self._write_common_bits()
        self._write_patches()
        self._write_control()
        self._write_changelog()
        self._write_rules()
        self._dpkg_buildpackage()

    def _prepare_sources(self):
        for pkg in self._bundled:
            tarball_tpl = self._get_tarball_tpl(pkg)
            for source in pkg.get_sources():
                tarball = source.tarball(
                    pkg, str(self._pkgroot / tarball_tpl),
                    io=self._io)
                self._io.writeln(f'<info>Extracting {tarball.name}...</>')
                af_sources.unpack(
                    tarball,
                    dest=self._srcroot / self.get_source_dir(pkg),
                    io=self._io)

    def _copy_helpers(self):
        helpers_source_dir = pathlib.Path(helpers_pkg.__path__[0])
        helpers_target_dir = self.get_helpers_root(relative_to=None)
        helpers_rel_dir = self.get_helpers_root(relative_to='sourceroot')

        for helper in ('trim-install.py',):
            helper = pathlib.Path(helper)

            shutil.copy(helpers_source_dir / helper,
                        helpers_target_dir / helper)

            self._common_tools[helper.stem] = helpers_rel_dir / helper

    def _write_common_bits(self):
        debsource = self._debroot / 'source'
        debsource.mkdir()
        with open(debsource / 'format', 'w') as f:
            f.write('3.0 (quilt)\n')
        with open(self._debroot / 'compat', 'w') as f:
            f.write('9\n')

    def _write_patches(self):
        patches_dir = self._debroot / 'patches'
        patches_dir.mkdir()

        i = 0
        series = []

        for pkg in self._bundled:
            for pkgname, patches in pkg.get_patches().items():
                for patchname, patch in patches:
                    fixed_patch = re.sub(
                        r'(---|\+\+\+) (a|b)/(.*)',
                        f'\\g<1> \\g<2>/{pkgname}/\\g<3>',
                        patch
                    )

                    if patchname:
                        patchname = f'--{patchname}'

                    filename = f'{i:04d}-{pkgname}{patchname}.patch'

                    with open(patches_dir / filename, 'w') as f:
                        f.write(fixed_patch)

                    series.append(filename)
                    i += 1

        with open(patches_dir / 'series', 'w') as f:
            print('\n'.join(series), file=f)

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
            \trm -rf _artifacts stamp
        ''').format(
            name=self._root_pkg.name,
            target_global_rules=self._target.get_global_rules(),
            configure_steps=self._write_script('configure'),
            build_steps=self._write_script('build'),
            build_install_steps=self._write_script(
                'build_install', installable_only=True),
            no_install_list_steps=self._write_script(
                'no_install_list', installable_only=True).strip(),
            install_steps=self._write_script(
                'install', installable_only=True),
        )

        with open(self._debroot / 'rules', 'w') as f:
            f.write(rules)
            os.fchmod(f.fileno(), 0o755)

    def _write_script(self, stage: str, installable_only: bool=False) -> str:
        scripts = []

        if installable_only:
            packages = self._installable
        else:
            packages = self._bundled

        for pkg in packages:
            script = self._get_package_script(pkg, stage)
            if script.strip():
                scripts.append(script)

        helper = self.sh_write_bash_helper(
            f'_{stage}.sh', '\n\n'.join(scripts), relative_to='sourceroot')

        return f'\t{helper}'

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

        trim_install = self.sh_get_command(
            'trim-install', relative_to='sourceroot')

        return textwrap.dedent(f'''
            pushd "{source_root}" >/dev/null
            {il_script} > "debian/{self._root_pkg.name}.install"
            mkdir -p "{temp_dir}"
            {nil_script} > "{temp_dir}/not-installed"
            {trim_install} "debian/{self._root_pkg.name}.install" \\
                "{temp_dir}/not-installed" "{install_dir}"
            dh_install --sourcedir="{install_dir}" --fail-missing
            popd >/dev/null
        ''')

    def _get_package_script(
            self, pkg, stage: str, *, in_subshell=False) -> str:
        method = f'get_{stage}_script'
        self_method = getattr(self, f'_get_package_{stage}_script', None)
        if self_method:
            pkg_script = self_method(pkg) + '\n'
        else:
            pkg_script = ''

        bdir = self.get_build_dir(pkg)

        pkg_script += getattr(pkg, method)(self)

        if pkg_script:
            if in_subshell:
                script = textwrap.indent(
                    pkg_script, '    ').strip('\n').split('\n')
                script_len = len(script)
                lines = []
                for i, line in enumerate(script):
                    if not line.endswith('\\') and i < script_len - 1:
                        if line.strip():
                            line += ';\\'
                        else:
                            line += '\\'
                    lines.append(line)

                script = '\n'.join(lines)
                script = (
                    f'### {pkg.unique_name}\n'
                    f'mkdir -p "{bdir}"\n'
                    f'(cd "{bdir}";\\\n'
                    f'{script})'
                )
            else:
                script = (
                    f'### {pkg.unique_name}\n'
                    f'mkdir -p "{bdir}"\n'
                    f'pushd "{bdir}" >/dev/null\n'
                    f'{pkg_script}\n'
                    f'popd >/dev/null'
                )
        else:
            script = ''

        return script

    def _dpkg_buildpackage(self):
        env = os.environ.copy()
        env['DEBIAN_FRONTEND'] = 'noninteractive'

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
