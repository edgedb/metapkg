from __future__ import annotations
from typing import (
    TYPE_CHECKING,
)

import itertools

from poetry.repositories import exceptions as poetry_repo_exc
from poetry.repositories import pool as poetry_pool
from poetry.repositories import repository as poetry_repo
from poetry.core.packages import dependency as poetry_dep
from poetry.core.packages import dependency_group as poetry_depgroup
from poetry.core.packages import vcs_dependency as poetry_vcsdep
from poetry.packages import dependency_package as poetry_deppkg
from poetry.core.packages import package as poetry_pkg
from poetry.mixology import incompatibility as poetry_incompat
from poetry.puzzle import provider as poetry_provider

from metapkg import tools

from . import utils

if TYPE_CHECKING:
    from cleo.io import io as cleo_io


class Pool(poetry_pool.Pool):
    def package(
        self,
        name: str,
        version: str,
        extras: list[str] | None = None,
        repository: str | None = None,
    ) -> poetry_pkg.Package:
        for repo in self.repositories:
            try:
                package = repo.package(name, version, extras=extras)
            except poetry_repo_exc.PackageNotFound:
                continue

            if package:
                self._packages.append(package)

                return package

        raise poetry_repo_exc.PackageNotFound(
            f"Package {name} ({version}) not found."
        )

    def find_packages(
        self, dependency: poetry_dep.Dependency
    ) -> list[poetry_pkg.Package]:
        for repo in self.repositories:
            packages = repo.find_packages(dependency)
            if packages:
                return packages

        return []


class BundleRepository(poetry_repo.Repository):
    def add_package(self, package: poetry_pkg.Package) -> None:
        if not self.has_package(package):
            super().add_package(package)


bundle_repo = BundleRepository()


class Provider(poetry_provider.Provider):
    def __init__(
        self,
        package: poetry_pkg.Package,
        pool: poetry_pool.Pool,
        io: cleo_io.IO,
        *,
        include_build_reqs: bool = False,
        extras: list[str] | None = None,
    ) -> None:
        super().__init__(package, pool, io)
        self.include_build_reqs = include_build_reqs
        self._active_extras = set(extras) if extras else set()

    def search_for_vcs(
        self,
        dependency: poetry_vcsdep.VCSDependency,
    ) -> list[poetry_pkg.Package]:
        path = tools.git.repodir(dependency.source)
        setup_py = path / "setup.py"

        result: list[poetry_pkg.Package]
        if setup_py.exists():
            from . import python

            dist = tools.python.get_dist(path)
            package = python.PythonPackage(dependency.name, dist.version)
            package.build_requires = []

            build_requires = tools.python.get_build_requires_from_setup_py(
                setup_py
            )
            for breq in build_requires:
                dep = utils.python_dependency_from_pep_508(breq)
                package.build_requires.append(dep)

            for req in dist.metadata.run_requires:
                dep = utils.python_dependency_from_pep_508(req)
                package.add_dependency(dep)

            result = [package]
        else:
            if dependency.name.startswith("pypkg-"):
                dependency._name = dependency.name.replace("pypkg-", "")
                dependency._pretty_name = dependency.pretty_name.replace(
                    "pypkg-", ""
                )
            result = super().search_for_vcs(dependency)

        return result

    def incompatibilities_for(
        self,
        package: poetry_deppkg.DependencyPackage,
    ) -> list[poetry_incompat.Incompatibility]:
        if self.include_build_reqs:
            old_requires = package._dependency_groups.get(
                poetry_depgroup.MAIN_GROUP
            )

            try:
                breqs = list(getattr(package, "build_requires", []))
                breqs = [req for req in breqs if req.is_activated()]
                dep_group = poetry_depgroup.DependencyGroup(
                    poetry_depgroup.MAIN_GROUP
                )
                reqs = old_requires.dependencies if old_requires else []
                for req in reqs + breqs:
                    dep_group.add_dependency(req)
                package._dependency_groups[
                    poetry_depgroup.MAIN_GROUP
                ] = dep_group
                result = super().incompatibilities_for(package)
            finally:
                if old_requires is not None:
                    package._dependency_groups[
                        poetry_depgroup.MAIN_GROUP
                    ] = old_requires
        else:
            result = super().incompatibilities_for(package)

        return result

    def complete_package(
        self,
        package: poetry_deppkg.DependencyPackage,
    ) -> poetry_deppkg.DependencyPackage:
        chain = [package.requires]
        build_requires = getattr(package, "build_requires", None)
        if build_requires:
            chain.append(build_requires)

        pkg = super().complete_package(package)

        for dep in itertools.chain.from_iterable(chain):
            if not dep.is_activated() and dep.in_extras:
                if not (set(dep.in_extras) - self._active_extras):
                    dep.activate()
                    pkg.add_dependency(dep)

        return pkg
