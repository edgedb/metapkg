from . import package as tgt_pkg


class Target:

    def build(self, root_pkg, deps, io):
        pass


class Build:

    def __init__(self, target, io, root_pkg, deps, build_deps):
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

    @property
    def root_package(self):
        return self._root_pkg

    @property
    def target(self):
        return self._target

    def sh_get_install_prefix(self):
        prefix = self._target.sh_get_install_path_prefix()
        return prefix / self._root_pkg.name

    def sh_get_install_path(self, aspect, package):
        rel_path = self._target.sh_get_rel_install_path(aspect, package)
        return self.sh_get_install_prefix() / rel_path

    def sh_get_command(self, command):
        return self.target.sh_get_command(command)
