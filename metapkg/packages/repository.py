from __future__ import annotations
from typing import (
    TYPE_CHECKING,
)

import copy
import itertools
import pathlib

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
from poetry.vcs import git

from . import sources as mpkg_sources

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

    def _search_for_vcs(
        self,
        dependency: poetry_vcsdep.VCSDependency,
    ) -> poetry_pkg.Package:
        from . import python

        pkg = self.get_package_from_vcs(
            dependency.vcs,
            dependency.source,
            branch=dependency.branch,
            tag=dependency.tag,
            rev=dependency.rev,
            subdirectory=dependency.source_subdirectory,
            source_root=(
                self._source_root
                or (self._env.path.joinpath("src") if self._env else None)
            ),
        )

        pkg.develop = dependency.develop

        package = python.PythonPackage(
            f"pypkg-{pkg.name}",
            version=pkg.version,
            pretty_version=pkg.pretty_version,
        )
        package.__dict__.update(
            {
                k: copy.deepcopy(v)
                for k, v in pkg.__dict__.items()
                if k not in {"_name", "_pretty_name"}
            }
        )

        for dep in package.all_requires:
            dep._name = f"pypkg-{dep.name}"
            dep._pretty_name = f"pypkg-{dep.pretty_name}"

        source = git.Git.clone(
            url=dependency.source,
            source_root=(
                self._source_root
                or (self._env.path.joinpath("src") if self._env else None)
            ),
            branch=dependency.branch,
            tag=dependency.tag,
            revision=dependency.rev,
        )
        path = pathlib.Path(source.path)
        if dependency.source_subdirectory:
            path = path.joinpath(dependency.source_subdirectory)
        package.build_requires = python.get_build_requires_from_srcdir(
            package, path
        )
        package.source = mpkg_sources.source_for_url(f"file://{path}")

        return package

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
        chain = [package.all_requires]
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
