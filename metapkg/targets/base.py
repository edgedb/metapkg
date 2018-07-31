import os
import pathlib
import re
import shlex
import shutil
import stat
import sys
import textwrap

from metapkg.packages import sources as mpkg_sources

from . import _helpers as helpers_pkg
from . import package as tgt_pkg


class Target:

    def build(self, root_pkg, deps, io, workdir):
        pass


class Build:

    def __init__(self, target, io, root_pkg, deps, build_deps, workdir):
        self._droot = pathlib.Path(workdir)
        self._target = target
        self._io = io
        self._root_pkg = root_pkg
        self._deps = deps
        self._build_deps = build_deps
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

    def get_dir(self, path, *, relative_to):
        absolute_path = (self.get_source_abspath() / path).resolve()
        if not absolute_path.exists():
            absolute_path.mkdir(parents=True)

        return self.get_path(path, relative_to=relative_to)

    def sh_get_install_prefix(self):
        prefix = self._target.sh_get_install_path_prefix()
        return prefix / self._root_pkg.name

    def sh_get_install_path(self, aspect, package):
        rel_path = self._target.sh_get_rel_install_path(aspect, package)
        return self.sh_get_install_prefix() / rel_path

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

    def get_tarball_tpl(self, package):
        rp = self._root_pkg
        return (
            f'{rp.name}_{rp.version.text}.orig-{package.name}.tar{{comp}}'
        )

    def get_tool_list(self):
        return ['trim-install.py']

    def prepare_tools(self):
        for pkg in self._bundled:
            tools = pkg.get_build_tools(self)
            if tools:
                self._tools.update(tools)

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
                    pkg, str(tarball_root / tarball_tpl),
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

    def _write_script(
            self, stage: str, *,
            installable_only: bool=False,
            relative_to: str='sourceroot') -> str:
        scripts = []

        if installable_only:
            packages = self._installable
        else:
            packages = self._bundled

        for pkg in packages:
            script = self._get_package_script(
                pkg, stage, relative_to=relative_to)
            if script.strip():
                scripts.append(script)

        helper = self.sh_write_bash_helper(
            f'_{stage}.sh', '\n\n'.join(scripts),
            relative_to=relative_to)

        return f'\t{helper}'

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

        if pkg_script:
            script = (
                f'### {pkg.unique_name}\n'
                f'pushd "{bdir}" >/dev/null\n'
                f'{pkg_script}\n'
                f'popd >/dev/null'
            )
        else:
            script = ''

        return script
