from poetry import packages as poetry_pkg
from poetry import repositories as poetry_repo
from poetry.puzzle import provider as poetry_provider

from metapkg import tools


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

    def __init__(self, package, pool, io, *, include_build_reqs=False) -> None:
        super().__init__(package, pool, io)
        self.include_build_reqs = include_build_reqs

    def search_for_vcs(self, dependency):
        path = tools.git.update_repo(dependency.source, self._io)
        setup_py = path / 'setup.py'

        if setup_py.exists():
            dist = tools.python.get_dist(path)
            package = poetry_pkg.Package(dist.name, dist.version)
            package.build_requires = []

            build_requires = tools.python.get_build_requires(setup_py)
            for breq in build_requires:
                package.build_requires.append(
                    poetry_pkg.dependency_from_pep_508(breq))

            for req in dist.metadata.run_requires:
                package.requires.append(
                    poetry_pkg.dependency_from_pep_508(req))

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
                package.requires = old_requires + breqs
                return super().incompatibilities_for(package)
            finally:
                package.requires = old_requires
        else:
            return super().incompatibilities_for(package)
