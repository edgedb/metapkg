import importlib
import os
import platform
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
        { --generic : Build a generic target. }
        { --build-source : Build source packages. }
        { --build-debug : Build debug symbol packages. }
        { --source-ref= : VCS ref to build. }
        { --pkg-version= : Override package version. }
        { --pkg-revision= : Override package revision number (defaults to 1). }
        { --pkg-subdist= : Set package sub-distribution (e.g. nightly). }
        { --extra-optimizations : Enable extra optimization
                                  (increases build times). }
    """

    help = """Builds the specified package on the current platform."""

    _loggers = ['metapkg.build']

    def handle(self):
        pkgname = self.argument('name')
        keepwork = self.option('keepwork')
        destination = self.option('dest')
        generic = self.option('generic')
        build_source = self.option('build-source')
        build_debug = self.option('build-debug')
        src_ref = self.option('source-ref')
        version = self.option('pkg-version')
        revision = self.option('pkg-revision')
        subdist = self.option('pkg-subdist')
        extra_opt = self.option('extra-optimizations')

        modname, _, clsname = pkgname.rpartition(':')

        mod = importlib.import_module(modname)
        pkgcls = getattr(mod, clsname)
        if src_ref:
            if 'extras' not in pkgcls.sources[0]:
                pkgcls.sources[0]['extras'] = {}
            pkgcls.sources[0]['extras']['version'] = src_ref
        pkg = pkgcls.resolve(self.output, version=version)

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

        if generic:
            if platform.system() == 'Linux':
                target = targets.generic.GenericLinuxTarget()
            else:
                target = targets.generic.GenericTarget()
        else:
            target = targets.detect_target(self.output)

        target.prepare()

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
        build_root.add_dependency(pkg.name, pkg.version.text)
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
            deps = {req.name for req in reqs if req.is_activated()}
            graph[package.name] = {'item': package, 'deps': deps}

        build_deps = list(topological.sort(graph))

        if keepwork:
            workdir = tempfile.mkdtemp(prefix='metapkg.')
        else:
            tempdir = tempfile.TemporaryDirectory(prefix='metapkg.')
            workdir = tempdir.name

        os.chmod(workdir, 0o755)

        try:
            target.build(
                root_pkg=pkg, deps=packages, build_deps=build_deps,
                io=self.output, workdir=workdir, outputdir=destination,
                build_source=build_source, build_debug=build_debug,
                revision=revision or '1', subdist=subdist, extra_opt=extra_opt)
        finally:
            if not keepwork:
                tempdir.cleanup()
