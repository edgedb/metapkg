import importlib
import os
import pathlib
import platform
import sys
import tempfile

from poetry.puzzle import solver as poetry_solver
from poetry.core.packages import dependency as poetry_dep
from poetry.core.packages import project_package
from poetry.utils import env as poetry_env

from metapkg import targets
from metapkg.packages import python as af_python
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

    _loggers = ["metapkg.build"]

    def handle(self):
        pkgname = self.argument("name")
        keepwork = self.option("keepwork")
        destination = self.option("dest")
        generic = self.option("generic")
        build_source = self.option("build-source")
        build_debug = self.option("build-debug")
        src_ref = self.option("source-ref")
        version = self.option("pkg-version")
        revision = self.option("pkg-revision")
        subdist = self.option("pkg-subdist")
        extra_opt = self.option("extra-optimizations")

        modname, _, clsname = pkgname.rpartition(":")

        mod = importlib.import_module(modname)
        pkgcls = getattr(mod, clsname)
        if src_ref:
            if "extras" not in pkgcls.sources[0]:
                pkgcls.sources[0]["extras"] = {}
            pkgcls.sources[0]["extras"]["version"] = src_ref
        pkg = pkgcls.resolve(self.io, version=version)

        sources = pkg.get_sources()

        if len(sources) != 1:
            self.error("Only single-source git packages are supported")
            return 1

        source = sources[0]
        if not isinstance(source, af_sources.GitSource):
            self.error("Only single-source git packages are supported")
            return 1

        root = project_package.ProjectPackage("__root__", "1")
        root.python_versions = af_python.python_dependency.pretty_constraint
        root.add_dependency(poetry_dep.Dependency(pkg.name, pkg.version))
        af_repo.bundle_repo.add_package(root)

        if generic:
            if platform.system() == "Linux":
                target = targets.generic.GenericLinuxTarget()
            else:
                target = targets.generic.GenericTarget()
        else:
            target = targets.detect_target(self.io)

        target.prepare()

        target_capabilities = target.get_capabilities()
        extras = [f"capability-{c}" for c in target_capabilities]

        repo_pool = af_repo.Pool()
        repo_pool.add_repository(target.get_package_repository())
        repo_pool.add_repository(af_repo.bundle_repo, secondary=True)

        item_repo = pkg.get_package_repository(target, io=self.io)
        if item_repo is not None:
            repo_pool.add_repository(item_repo, secondary=True)

        provider = af_repo.Provider(root, repo_pool, self.io, extras=extras)
        resolution = poetry_solver.resolve_version(root, provider)

        env = poetry_env.SystemEnv(pathlib.Path(sys.executable))

        graph = {}
        for dep_package in resolution.packages:
            package = dep_package.package
            if env.is_valid_for_marker(dep_package.dependency.marker):
                deps = {
                    req.name
                    for req in package.requires
                    if env.is_valid_for_marker(req.marker)
                }
                graph[package.name] = {"item": package, "deps": deps}
        packages = list(topological.sort(graph))

        # Build a separate package list for build deps.
        build_deps = {}
        build_root = project_package.ProjectPackage("__build_root__", "1")
        build_root.python_versions = (
            af_python.python_dependency.pretty_constraint
        )
        build_root.add_dependency(poetry_dep.Dependency(pkg.name, pkg.version))
        build_root.build_requires = []
        provider = af_repo.Provider(
            build_root, repo_pool, self.io, include_build_reqs=True
        )
        resolution = poetry_solver.resolve_version(build_root, provider)

        graph = {}
        for dep_package in resolution.packages:
            package = dep_package.package
            if env.is_valid_for_marker(dep_package.dependency.marker):
                reqs = set(package.requires) | set(
                    getattr(package, "build_requires", [])
                )
                deps = {
                    req.name
                    for req in reqs
                    if req.is_activated()
                    and env.is_valid_for_marker(req.marker)
                }
                graph[package.name] = {"item": package, "deps": deps}

        build_deps = list(topological.sort(graph))

        if keepwork:
            workdir = tempfile.mkdtemp(prefix="metapkg.")
        else:
            tempdir = tempfile.TemporaryDirectory(prefix="metapkg.")
            workdir = tempdir.name

        os.chmod(workdir, 0o755)

        try:
            target.build(
                root_pkg=pkg,
                deps=packages,
                build_deps=build_deps,
                io=self.io,
                workdir=workdir,
                outputdir=destination,
                build_source=build_source,
                build_debug=build_debug,
                revision=revision or "1",
                subdist=subdist,
                extra_opt=extra_opt,
            )
        finally:
            if not keepwork:
                tempdir.cleanup()
