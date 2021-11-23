from __future__ import annotations

import collections
import graphlib
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

from . import base


class Build(base.Command):
    """Build the specified package

    build
        { name : Package to build. }
        { --jobs= : Use up to N processes in parallel to build. }
        { --dest= : Destination path. }
        { --keepwork : Do not remove the work directory. }
        { --generic : Build a generic target. }
        { --libc= : Libc to target. }
        { --build-source : Build source packages. }
        { --build-debug : Build debug symbol packages. }
        { --release : Whether this build is a release. }
        { --source-ref= : VCS ref to build. }
        { --pkg-version= : Override package version. }
        { --pkg-revision= : Override package revision number (defaults to 1). }
        { --pkg-subdist= : Set package sub-distribution (e.g. nightly). }
        { --extra-optimizations : Enable extra optimization
                                  (increases build times). }
    """

    help = """Builds the specified package on the current platform."""

    _loggers = ["metapkg.build"]

    def handle(self) -> int:
        pkgname = self.argument("name")
        keepwork = self.option("keepwork")
        destination = self.option("dest")
        generic = self.option("generic")
        libc = self.option("libc")
        build_source = self.option("build-source")
        build_debug = self.option("build-debug")
        src_ref = self.option("source-ref")
        version = self.option("pkg-version")
        revision = self.option("pkg-revision")
        subdist = self.option("pkg-subdist")
        is_release = self.option("release")
        extra_opt = self.option("extra-optimizations")
        jobs = self.option("jobs")

        modname, _, clsname = pkgname.rpartition(":")

        mod = importlib.import_module(modname)
        pkgcls = getattr(mod, clsname)
        if src_ref:
            if "extras" not in pkgcls.sources[0]:
                pkgcls.sources[0]["extras"] = {}
            pkgcls.sources[0]["extras"]["version"] = src_ref
        root_pkg = pkgcls.resolve(
            self.io, version=version, is_release=is_release
        )

        sources = root_pkg.get_sources()

        if len(sources) != 1:
            self.error("Only single-source git packages are supported")
            return 1

        source = sources[0]
        if not isinstance(source, af_sources.GitSource):
            self.error("Only single-source git packages are supported")
            return 1

        root = project_package.ProjectPackage("__root__", "1")
        root.python_versions = af_python.python_dependency.pretty_constraint
        root.add_dependency(
            poetry_dep.Dependency(root_pkg.name, root_pkg.version)
        )
        af_repo.bundle_repo.add_package(root)

        target = targets.detect_target(self.io, portable=generic, libc=libc)
        target.prepare()

        target_capabilities = target.get_capabilities()
        extras = [f"capability-{c}" for c in target_capabilities]

        repo_pool = af_repo.Pool()
        repo_pool.add_repository(target.get_package_repository())
        repo_pool.add_repository(af_repo.bundle_repo, secondary=True)

        item_repo = root_pkg.get_package_repository(target, io=self.io)
        if item_repo is not None:
            repo_pool.add_repository(item_repo, secondary=True)

        provider = af_repo.Provider(root, repo_pool, self.io, extras=extras)
        resolution = poetry_solver.resolve_version(root, provider)

        env = poetry_env.SystemEnv(pathlib.Path(sys.executable))
        pkg_map = {}
        graph = {}
        for dep_package in resolution.packages:
            pkg_map[dep_package.name] = dep_package.package
            package = dep_package.package
            if env.is_valid_for_marker(dep_package.dependency.marker):
                deps = {
                    req.name
                    for req in package.requires
                    if env.is_valid_for_marker(req.marker)
                }
                graph[package.name] = deps
        sorter = graphlib.TopologicalSorter(graph)
        packages = [pkg_map[pn] for pn in sorter.static_order()]

        # Build a separate package list for build deps.
        build_root = project_package.ProjectPackage("__build_root__", "1")
        build_root.python_versions = (
            af_python.python_dependency.pretty_constraint
        )
        build_root.add_dependency(
            poetry_dep.Dependency(root_pkg.name, root_pkg.version)
        )
        build_root.build_requires = []
        provider = af_repo.Provider(
            build_root, repo_pool, self.io, include_build_reqs=True
        )
        resolution = poetry_solver.resolve_version(build_root, provider)

        pkg_map = {}
        graph = {}
        for dep_package in resolution.packages:
            pkg_map[dep_package.name] = dep_package.package
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
                graph[package.name] = deps

        # Workaround cycles in build/runtime dependencies between
        # packages.  This requires the depending package to explicitly
        # declare its cyclic runtime dependencies in get_cyclic_runtime_deps()
        # and then the cyclic dependency must take care to inject itself
        # into the dependent's context to build itself (e.g. by manipulating
        # PYTHONPATH at build time.)  An example of such cycle is
        # flit-core -> tomli -> flit-core.
        cyclic_runtime_deps = collections.defaultdict(list)
        last_cycle = None
        current_cycle = None
        while True:
            sorter = graphlib.TopologicalSorter(graph)

            try:
                build_pkgs = [pkg_map[pn] for pn in sorter.static_order()]
            except graphlib.CycleError as e:
                cycle = e.args[1]
                if len(cycle) > 3 or cycle == last_cycle:
                    raise

                dep = pkg_map[cycle[-1]]
                pkg_with_dep = pkg_map[cycle[-2]]
                if dep.name not in pkg_with_dep.get_cyclic_runtime_deps():
                    dep, pkg_with_dep = pkg_with_dep, dep
                    if dep.name not in pkg_with_dep.get_cyclic_runtime_deps():
                        raise

                last_cycle = current_cycle
                current_cycle = cycle
                cyclic_runtime_deps[pkg_with_dep].append(dep)
                graph[pkg_with_dep.name].remove(dep.name)
            else:
                break

        for pkg_with_cr_deps, cr_deps in cyclic_runtime_deps.items():
            for i, build_pkg in enumerate(build_pkgs):
                if build_pkg == pkg_with_cr_deps:
                    build_pkgs[i + 1 : i + 1] = cr_deps
                    break

        if keepwork:
            workdir = tempfile.mkdtemp(prefix="metapkg.")
        else:
            tempdir = tempfile.TemporaryDirectory(prefix="metapkg.")
            workdir = tempdir.name

        os.chmod(workdir, 0o755)

        try:
            target.build(
                targets.BuildRequest(
                    io=self.io,
                    env=env,
                    root_pkg=root_pkg,
                    deps=packages,
                    build_deps=build_pkgs,
                    workdir=workdir,
                    outputdir=destination,
                    build_source=build_source,
                    build_debug=build_debug,
                    revision=revision or "1",
                    subdist=subdist,
                    extra_opt=extra_opt,
                    jobs=jobs or 0,
                ),
            )
        finally:
            if not keepwork:
                tempdir.cleanup()

        return 0
