import itertools

from poetry import packages as poetry_pkg
from poetry import repositories as poetry_repo
from poetry.puzzle import provider as poetry_provider

from metapkg import tools

from . import utils


class PackageNotFoundError(LookupError):
    pass


class Pool(poetry_repo.Pool):

    def package(self, name, version, extras=None):
        for repository in self.repositories:
            try:
                package = repository.package(name, version, extras=extras)
            except PackageNotFoundError:
                continue

            if package:
                self._packages.append(package)

                return package

        raise PackageNotFoundError(f'package not found: {name}-{version}')


class Repository(poetry_repo.Repository):

    def package(self, name, version, extras=None):
        package = super().package(name, version)
        if package is None:
            raise PackageNotFoundError(f'package not found: {name}-{version}')
        return package


class BundleRepository(Repository):

    def add_package(self, package):
        if not self.has_package(package):
            super().add_package(package)


bundle_repo = BundleRepository()


class Provider(poetry_provider.Provider):

    def __init__(self, package, pool, io, *,
                 include_build_reqs=False, extras=None) -> None:
        super().__init__(package, pool, io)
        self.include_build_reqs = include_build_reqs
        self._active_extras = set(extras) if extras else set()

    def search_for_vcs(self, dependency):
        path = tools.git.repodir(dependency.source)
        setup_py = path / 'setup.py'

        if setup_py.exists():
            dist = tools.python.get_dist(path)
            package = poetry_pkg.Package(dependency.name, dist.version)
            package.build_requires = []

            build_requires = tools.python.get_build_requires(setup_py)
            for breq in build_requires:
                dep = utils.python_dependency_from_pep_508(breq)
                package.build_requires.append(dep)

            for req in dist.metadata.run_requires:
                dep = utils.python_dependency_from_pep_508(req)
                package.requires.append(dep)

        else:
            raise RuntimeError('non-Python git packages are not supported')

        return [package]

    def incompatibilities_for(
            self,
            package: poetry_pkg.Package):
        if self.include_build_reqs:
            old_requires = list(package.requires)

            try:
                breqs = list(getattr(package, 'build_requires', []))
                breqs = [req for req in breqs if req.is_activated()]
                package.requires = old_requires + breqs
                return super().incompatibilities_for(package)
            finally:
                package.requires = old_requires
        else:
            return super().incompatibilities_for(package)

    def complete_package(self, package) -> poetry_pkg.Package:
        chain = [package.requires]
        build_requires = getattr(package, 'build_requires', None)
        if build_requires:
            chain.append(build_requires)

        for dep in itertools.chain.from_iterable(chain):
            if not dep.is_activated() and dep.in_extras:
                if not (set(dep.in_extras) - self._active_extras):
                    dep.activate()

        return super().complete_package(package)
