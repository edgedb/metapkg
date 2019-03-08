import collections
import os
import pathlib
import re
import shlex
import shutil
import stat
import sys
import textwrap

from metapkg import tools
from metapkg.packages import sources as mpkg_sources

from . import _helpers as helpers_pkg
from . import package as tgt_pkg


class TargetAction:

    def __init__(self, build) -> None:
        self._build = build

    def get_script(self, **kwargs) -> str:
        raise NotImplementedError


class Target:

    def get_capabilities(self) -> list:
        return []

    def has_capability(self, capability):
        return capability in self.get_capabilities()

    def get_system_dependencies(self, dep_name) -> list:
        return [dep_name]

    def get_action(self, name, build) -> TargetAction:
        raise NotImplementedError(f'unknown target action: {name}')

    def get_resource_path(self, build, resource):
        return None

    def get_package_system_ident(self, build, package,
                                 include_slot: bool = False):
        return package.name_slot if include_slot else package.name

    def get_full_install_prefix(self, build) -> pathlib.Path:
        return self.get_install_root(build) / self.get_install_prefix(build)


class PosixEnsureDirAction(TargetAction):

    def get_script(self, *, path, owner_user=None,
                   owner_group=None, owner_recursive=False, mode=0o755):

        chown_flags = '-R' if owner_recursive else ''

        script = textwrap.dedent(f'''\
            if ! [ -d "{path}" ]; then
                mkdir -p "{path}"
            fi
            chmod "{mode:o}" "{path}"
        ''')

        if owner_user and owner_group:
            script += (
                f'\nchown {chown_flags} "{owner_user}:{owner_group}" "{path}"')
        elif owner_user:
            script += (
                f'\nchown {chown_flags} "{owner_user}" "{path}"')
        elif owner_group:
            script += (
                f'\nchgrp {chown_flags} "{owner_group}" "{path}"')

        return script


class PosixTarget(Target):

    def get_action(self, name, build) -> TargetAction:
        if name == 'ensuredir':
            return PosixEnsureDirAction(build)
        else:
            return super().get_action(name, build)


class LinuxAddUserAction(TargetAction):

    def get_script(self, *, name, group=None, homedir=None,
                   shell=False, system=False, description=None) -> str:

        args = {}
        if group:
            args['-g'] = group
        if homedir:
            args['-d'] = homedir
        else:
            args['-M'] = None
        if shell:
            args['-s'] = '/bin/bash'
        else:
            args['-s'] = '/sbin/nologin'
        if system:
            args['-r'] = None
        if description:
            args['-c'] = description

        args[name] = None

        user_group = name

        if group:
            group_args = {}
            if system:
                group_args['-r'] = None
            group_args[group] = None

            groupadd = self._build.sh_get_command('groupadd')

            groupadd_cmd = self._build.sh_format_command(
                groupadd, group_args, extra_indent=4)
            group_script = textwrap.dedent('''\
                if ! getent group "{group}" > /dev/null; then
                    {groupadd_cmd}
                fi
            ''').format(group=group, groupadd_cmd=groupadd_cmd)

            user_group += f':{group}'
        else:
            group_script = ''

        if homedir:
            homedir_script = PosixEnsureDirAction(self._build).get_script(
                path=homedir, owner_user=name, owner_group=group)
        else:
            homedir_script = ''

        useradd = self._build.sh_get_command('useradd')
        useradd_cmd = self._build.sh_format_command(
            useradd, args, extra_indent=4)

        return textwrap.dedent('''\
            {group_script}
            if ! getent passwd "{name}" > /dev/null; then
                {useradd_cmd}
            fi
            {homedir_script}
        ''').format(group_script=group_script, name=name,
                    useradd_cmd=useradd_cmd,
                    homedir_script=homedir_script)


class LinuxTarget(PosixTarget):

    @property
    def name(self):
        return f'{self.distro["id"]}-{self.distro["version"]}'

    def get_action(self, name, build) -> TargetAction:
        if name == 'adduser':
            return LinuxAddUserAction(build)
        else:
            return super().get_action(name, build)

    def get_su_script(self, build, script, user) -> str:
        return f"su '{user}' -c {shlex.quote(script)}\n"

    def service_scripts_for_package(self, build, package) -> dict:
        if self.has_capability('systemd'):
            units = package.read_support_files(build, '*.service.in')
            systemd_path = self.get_resource_path(build, 'systemd-units')
            if systemd_path is None:
                raise RuntimeError(
                    'systemd-enabled target does not define '
                    '"systemd-units" path')
            return {systemd_path / name: data for name, data in units.items()}

        else:
            raise NotImplementedError(
                'non-systemd linux targets are not supported')


class FHSTarget(PosixTarget):

    def get_arch_libdir(self):
        raise NotImplementedError

    def get_sys_bindir(self):
        return pathlib.Path('/usr/bin')

    def sh_get_command(self, command):
        return command

    def get_install_root(self, build):
        return pathlib.Path('/')

    def get_install_prefix(self, build):
        libdir = self.get_arch_libdir()
        return (libdir / build.root_package.name_slot).relative_to('/')

    def get_install_path(self, build, aspect):
        root = self.get_install_root(build)
        prefix = self.get_install_prefix(build)

        if aspect == 'sysconf':
            return root / 'etc'
        elif aspect == 'userconf':
            return pathlib.Path('$HOME') / '.config'
        elif aspect == 'data':
            return root / 'usr' / 'share' / build.root_package.name_slot
        elif aspect == 'bin':
            return root / prefix / 'bin'
        elif aspect == 'systembin':
            if root == pathlib.Path('/'):
                return self.get_sys_bindir()
            else:
                return root / 'bin'
        elif aspect == 'lib':
            return root / prefix / 'lib'
        elif aspect == 'include':
            return root / 'usr' / 'include' / build.root_package.name_slot
        elif aspect == 'localstate':
            return root / 'var'
        elif aspect == 'runstate':
            return root / 'run'
        else:
            raise LookupError(f'aspect: {aspect}')

    def get_resource_path(self, build, resource):
        if resource == 'tzdata':
            return pathlib.Path('/usr/share/zoneinfo')
        else:
            return None


class Build:

    def __init__(self, target, *, io, root_pkg, deps,
                 build_deps, workdir, outputdir, build_source,
                 build_debug):
        self._droot = pathlib.Path(workdir)
        if outputdir is not None:
            self._outputroot = pathlib.Path(outputdir)
        else:
            self._outputroot = None
        self._target = target
        self._io = io
        self._root_pkg = root_pkg
        self._deps = deps
        self._build_deps = build_deps
        self._build_source = build_source
        self._build_debug = build_debug
        self._bundled = [
            pkg for pkg in self._build_deps
            if not isinstance(pkg, tgt_pkg.SystemPackage) and
            pkg is not root_pkg
        ]
        self._build_only = set(build_deps) - set(deps)
        self._installable = [
            pkg for pkg in self._bundled if pkg not in self._build_only
        ]
        self._tools = {}
        self._common_tools = {}
        self._system_tools = {}
        self._tool_wrappers = {}
        self._tarballs = {}
        self._patches = []

    @property
    def root_package(self):
        return self._root_pkg

    @property
    def target(self):
        return self._target

    def get_package(self, name):
        for pkg in self._deps:
            if pkg.name == name:
                return pkg

        for pkg in self._build_deps:
            if pkg.name == name:
                return pkg

    def is_bundled(self, pkg):
        return pkg in self._bundled

    def run(self):
        self._io.writeln(f'<info>Building {self._root_pkg} on '
                         f'{self._target.name}</info>')

        self.prepare()
        self.build()

    def prepare(self):
        pass

    def build(self):
        raise NotImplementedError

    def get_dir(self, path, *, relative_to, package=None):
        absolute_path = (self.get_source_abspath() / path).resolve()
        if not absolute_path.exists():
            absolute_path.mkdir(parents=True)

        return self.get_path(path, relative_to=relative_to, package=package)

    def get_install_path(self, aspect):
        return self._target.get_install_path(self, aspect)

    def get_install_prefix(self):
        return self._target.get_install_prefix(self)

    def get_full_install_prefix(self):
        return self._target.get_full_install_prefix(self)

    def sh_get_command(self, command, *, package=None, relative_to='pkgbuild'):
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
            rel_path = self.get_path(path, package=package,
                                     relative_to=relative_to)
            if rel_path.suffix == '.py':
                python = self.sh_get_command(
                    'python', package=package, relative_to=relative_to)

                cmd = f'{python} {shlex.quote(str(rel_path))}'

            elif not rel_path.suffix:
                cmd = shlex.quote(str(rel_path))

            else:
                raise RuntimeError(
                    f'unexpected tool type: {path}')

        return cmd

    def sh_format_command(self, path, args: dict, *, extra_indent=0,
                          user=None, force_args_eq=False,
                          linebreaks=True) -> str:
        args_parts = []
        for arg, val in args.items():
            if val is None:
                args_parts.append(arg)
            else:
                val = str(val)
                if not val.startswith('!'):
                    val = shlex.quote(val)
                else:
                    val = val[1:]
                sep = '=' if arg.startswith('--') or force_args_eq else ' '
                args_parts.append(f'{arg}{sep}{val}')

        if linebreaks:
            sep = ' \\\n    '
        else:
            sep = ' '

        args_str = sep.join(args_parts)

        if linebreaks:
            args_str = textwrap.indent(args_str, ' ' * 4)

        result = f'{shlex.quote(str(path))}{sep}{args_str}'

        if extra_indent:
            result = textwrap.indent(result, ' ' * extra_indent)

        return result

    def format_package_template(self, tpl, package) -> str:
        variables = {}
        for aspect in ('bin', 'data', 'include', 'lib', 'runstate',
                       'localstate', 'userconf'):
            path = self.get_install_path(aspect)
            variables[f'{aspect}dir'] = path

        variables['prefix'] = self.get_install_prefix()
        variables['slot'] = package.slot
        variables['identifier'] = self.target.get_package_system_ident(
            self, package)
        variables['name'] = package.name
        variables['description'] = package.description
        variables['documentation'] = package.url

        return tools.format_template(tpl, **variables)

    def write_helper(self, name: str, text: str, *, relative_to: str) -> str:
        helpers_abs = self.get_helpers_root(relative_to=None)
        helpers_rel = self.get_helpers_root(relative_to=relative_to)

        with open(helpers_abs / name, 'w') as f:
            print(text, file=f)
            os.fchmod(f.fileno(), 0o755)

        return f'{helpers_rel / name}'

    def sh_write_helper(
            self, name: str, text: str, *, relative_to: str) -> str:
        """Write an executable helper and return it's shell-escaped name."""

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

    def get_tarball_tpl(self, package):
        rp = self._root_pkg
        return (
            f'{rp.name_slot}_{rp.version.text}.orig-{package.name}.tar{{comp}}'
        )

    def get_tool_list(self):
        return ['trim-install.py']

    def get_su_script(self, script, user):
        return self.target.get_su_script(self, script, user)

    def prepare_tools(self):
        for pkg in self._bundled:
            bundled_tools = pkg.get_build_tools(self)
            if bundled_tools:
                self._tools.update(bundled_tools)

        source_dirs = [pathlib.Path(helpers_pkg.__path__[0])]
        specific_helpers = pathlib.Path(
            sys.modules[self.__module__].__file__).parent / '_helpers'
        if specific_helpers.exists():
            source_dirs.insert(0, specific_helpers)

        helpers_target_dir = self.get_helpers_root(relative_to=None)
        helpers_rel_dir = self.get_helpers_root(relative_to='sourceroot')

        for helper in self.get_tool_list():
            helper = pathlib.Path(helper)

            for source_dir in source_dirs:
                if (source_dir / helper).exists():
                    shutil.copy(source_dir / helper,
                                helpers_target_dir / helper)
                    os.chmod(helpers_target_dir / helper,
                             stat.S_IRWXU |
                             stat.S_IRGRP | stat.S_IXGRP |
                             stat.S_IROTH | stat.S_IXOTH)
                    break
            else:
                raise RuntimeError(f'cannot find helper: {helper}')

            self._common_tools[helper.stem] = helpers_rel_dir / helper

    def prepare_tarballs(self):
        tarball_root = self.get_tarball_root(relative_to=None)

        for pkg in self._bundled:
            tarball_tpl = self.get_tarball_tpl(pkg)
            for source in pkg.get_sources():
                tarball = source.tarball(
                    pkg, tarball_tpl,
                    target_dir=tarball_root,
                    io=self._io)

                self._tarballs[pkg] = tarball

    def unpack_sources(self):
        for pkg, tarball in self._tarballs.items():
            self._io.writeln(f'<info>Extracting {tarball.name}...</>')
            mpkg_sources.unpack(
                tarball,
                dest=self.get_source_dir(pkg, relative_to=None),
                io=self._io)

    def prepare_patches(self):
        patches_dir = self.get_patches_root(relative_to=None)

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

        self._patches = series

    def get_extra_system_requirements(self) -> dict:
        all_reqs = collections.defaultdict(set)

        for pkg in self._installable:
            reqs = pkg.get_extra_system_requirements(self)
            for req_type, req_list in reqs.items():
                sys_reqs = set()
                for req in req_list:
                    sys_reqs.update(self.target.get_system_dependencies(req))

                all_reqs[req_type].update(sys_reqs)

        return all_reqs

    def get_service_scripts(self) -> dict:
        all_scripts = {}

        for pkg in self._installable:
            pkg_scripts = pkg.get_service_scripts(self)
            all_scripts.update(pkg_scripts)

        return all_scripts

    def _write_script(
            self, stage: str, *,
            installable_only: bool=False,
            relative_to: str='sourceroot') -> str:

        script = self.get_script(stage, installable_only=installable_only,
                                 relative_to=relative_to)

        helper = self.sh_write_bash_helper(
            f'_{stage}.sh', script, relative_to=relative_to)

        return f'\t{helper}'

    def get_script(
            self, stage: str, *,
            installable_only: bool=False,
            relative_to: str='sourceroot') -> str:

        scripts = []

        if installable_only:
            packages = self._installable
        else:
            packages = self._bundled

        if stage == 'complete':
            stages = ['configure', 'build', 'build_install']
        else:
            stages = [stage]

        for pkg in packages:
            for stg in stages:
                script = self._get_package_script(
                    pkg, stg, relative_to=relative_to)
                if script.strip():
                    scripts.append(script)

        global_method = getattr(self, f'_get_global_{stage}_script', None)
        if global_method:
            global_script = global_method()
            if global_script:
                scripts.append(global_script)

        return '\n\n'.join(scripts)

    def _get_global_after_install_script(self) -> str:
        script = ''
        service_scripts = self.get_service_scripts()

        if service_scripts:
            if self.target.has_capability('systemd'):
                rundir = self.get_install_path('runstate')
                systemd = rundir / 'systemd' / 'system'

                script = textwrap.dedent(f'''\
                    if [ -d "{systemd}" ]; then
                        systemctl daemon-reload
                    fi
                ''')

            elif self.target.has_capability('launchd'):
                script_lines = []

                for path in service_scripts:
                    ident = path.stem
                    script_lines.append(
                        f'launchctl bootstrap system "{path}"')
                    script_lines.append(
                        f'launchctl enable system/{ident}')

                script = '\n'.join(script_lines)

        return script

    def _get_package_script(
            self, pkg, stage: str, *, relative_to='sourceroot') -> str:
        method = f'get_{stage}_script'
        self_method = getattr(self, f'_get_package_{stage}_script', None)
        if self_method:
            pkg_script = self_method(pkg) + '\n'
        else:
            pkg_script = ''

        bdir = self.get_build_dir(pkg, relative_to=relative_to)

        pkg_method = getattr(pkg, method, None)
        if pkg_method:
            pkg_script += pkg_method(self)

        build_time = stage not in {'before_install', 'after_install',
                                   'before_uninstall', 'after_uninstall'}

        if pkg_script:
            script_lines = [f'### {pkg.unique_name}\n']
            if build_time:
                script_lines.append(f'pushd "{bdir}" >/dev/null\n')
            script_lines.append(f'{pkg_script}\n')
            if build_time:
                script_lines.append(f'popd >/dev/null')

            script = ''.join(script_lines)
        else:
            script = ''

        return script
