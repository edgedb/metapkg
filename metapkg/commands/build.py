import importlib
import tempfile

from poetry import packages as poetry_pkg
from poetry.puzzle import solver as poetry_solver

from metapkg import targets
from metapkg.packages import repository as af_repo
from metapkg.packages import sources as af_sources
from metapkg.packages import topological

from . import base


class Build(base.Command):
    """Build the specified package

    build
        { name : Package to build. }
        { --dest= : Destination path. }
        { --keepwork : Do not remove the work directory. }
    """

    help = """Builds the specified package on the current platform."""

    _loggers = ['metapkg.build']

    def handle(self):
        # import logging
        # logging.basicConfig(level='DEBUG')

        pkgname = self.argument('name')
        keepwork = self.option('keepwork')
        # destination = self.option('dest')

        modname, _, clsname = pkgname.rpartition(':')

        mod = importlib.import_module(modname)
        pkgcls = getattr(mod, clsname)
        pkg = pkgcls.resolve(self.output)

        sources = pkg.get_sources()

        if len(sources) != 1:
            self.error('Only single-source git packages are supported')
            return 1

        source = sources[0]
        if not isinstance(source, af_sources.GitSource):
            self.error('Only single-source git packages are supported')
            return 1

        root = poetry_pkg.ProjectPackage('__root__', '1')
        root.add_dependency(pkg.name, pkg.version.text)
        af_repo.bundle_repo.add_package(root)

        target = targets.detect_target(self.output)
        target_capabilities = target.get_capabilities()
        extras = [f'capability-{c}' for c in target_capabilities]

        repo_pool = af_repo.Pool()
        repo_pool.add_repository(target.get_package_repository())
        repo_pool.add_repository(af_repo.bundle_repo)

        item_repo = pkg.get_package_repository(target, io=self.output)
        if item_repo is not None:
            repo_pool.add_repository(item_repo)

        provider = af_repo.Provider(
            root, repo_pool, self.output, extras=extras)
        resolution = poetry_solver.resolve_version(root, provider)

        graph = {}
        for package in resolution.packages:
            deps = {req.name for req in package.requires}
            graph[package.name] = {'item': package, 'deps': deps}
        packages = list(topological.sort(graph))

        # Build a separate package list for build deps.
        build_deps = {}
        build_root = poetry_pkg.ProjectPackage('__build_root__', '1')
        build_root.add_dependency(pkg.name, {'git': source.url})
        build_root.build_requires = []
        provider = af_repo.Provider(
            build_root, repo_pool, self.output,
            include_build_reqs=True)
        resolution = poetry_solver.resolve_version(
            build_root, provider)

        graph = {}
        for package in resolution.packages:
            reqs = (set(package.requires) |
                    set(getattr(package, 'build_requires', [])))
            deps = {req.name for req in reqs}
            graph[package.name] = {'item': package, 'deps': deps}

        build_deps = list(topological.sort(graph))

        if keepwork:
            workdir = tempfile.mkdtemp(prefix='metapkg.')
        else:
            tempdir = tempfile.TemporaryDirectory(prefix='metapkg.')
            workdir = tempdir.name

        try:
            target.build(
                pkg, packages, build_deps, io=self.output, workdir=workdir)
        finally:
            if not keepwork:
                tempdir.cleanup()
