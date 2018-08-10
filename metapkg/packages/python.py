import pathlib
import shlex
import tempfile
import textwrap
import typing

from poetry import packages as poetry_pkg
from poetry.repositories import pypi_repository

from metapkg import tools

from . import base
from . import repository as af_repository
from . import sources as af_sources


PythonPackage = poetry_pkg.Package


python_dependency = poetry_pkg.Dependency(name='python', constraint='>=3.6')
wheel_dependency = poetry_pkg.Dependency(name='wheel', constraint='*')


def set_python_runtime_dependency(dep):
    global python_dependency
    python_dependency = dep


class PyPiRepository(pypi_repository.PyPiRepository):

    def __init__(self, io) -> None:
        super().__init__()
        self._io = io

    def package(
            self,
            name: str,
            version: str,
            extras=None) -> poetry_pkg.Package:

        try:
            orig_package = super().package(
                name=name, version=version, extras=extras)
        except ValueError as e:
            raise af_repository.PackageNotFoundError(
                f'package not found: {name}-{version}') from e

        pypi_info = self.get_pypi_info(name, version)

        package = PythonPackage(
            pypi_info['info']['name'], orig_package.version,
            pretty_version=pypi_info['info']['version'])

        for attr in ('category', 'optional', 'python_versions',
                     'platform', 'extras', 'source_type', 'source_url',
                     'source_reference', 'description', 'hashes'):
            v = getattr(orig_package, attr)
            setattr(package, attr, v)

        # Some packages like to hard-depend on PyPI version
        # of typing, which is out-of-date at this moment, so
        # filter it out.
        package.requires = [dep for dep in orig_package.requires
                            if dep.name != 'typing']

        package.requires.append(python_dependency)
        package.source = self.get_sdist_source(pypi_info)
        package.source_type = 'pypi'
        package.source_url = package.source.url
        package.build_requires = self._get_build_requires(package)
        if package.name != wheel_dependency.name:
            package.build_requires.append(wheel_dependency)

        return package

    def get_sdist_source(self, pypi_info: dict) -> af_sources.BaseSource:
        sdist_info = self._get_sdist_info(pypi_info)
        source = af_sources.source_for_url(sdist_info['url'])
        md5_digest = sdist_info.get('md5_digest')
        if md5_digest:
            source.add_verification(af_sources.HashVerification(
                algorithm='md5', hash_value=md5_digest
            ))
        sha256_digest = sdist_info.get('sha256')
        if sha256_digest:
            source.add_verification(af_sources.HashVerification(
                algorithm='sha256', hash_value=sha256_digest
            ))

        return source

    def get_pypi_info(
            self,
            name: str,
            version: str) -> dict:

        if self._disable_cache:
            pypi_info = self._get_pypi_info(name, version)
        else:
            pypi_info = self._cache.remember_forever(
                f'{name}:{version}:pypi-info',
                lambda: self._get_pypi_info(name, version)
            )

        return pypi_info

    def _get_pypi_info(
            self,
            name: str,
            version: str) -> dict:
        json_data = self._get(f'pypi/{name}/{version}/json')
        if json_data is None:
            raise af_repository.PackageNotFoundError(
                f'Package [{name}] not found.')

        return json_data

    def _get_sdist_info(
            self,
            pypi_info: dict) -> dict:

        name = pypi_info['info']['name']
        version = pypi_info['info']['version']
        sdist_info = None

        try:
            version_info = pypi_info['releases'][version]
        except KeyError:
            version_info = []

        for file_info in version_info:
            if file_info['packagetype'] == 'sdist':
                sdist_info = file_info
                break

        if sdist_info is None:
            raise LookupError(f'No sdist URL for {name}')

        return sdist_info

    def _get_build_requires(
            self, package) -> typing.List[poetry_pkg.Dependency]:
        tarball = package.source.tarball(package, io=self._io)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = pathlib.Path(tmpdir)

            af_sources.unpack(tarball, dest=tmpdir, io=self._io)

            setup_py = tmpdir / 'setup.py'
            if not setup_py.exists():
                raise LookupError(f'No setup.py in {package.name} tarball')

            build_requires = []
            breqs = tools.python.get_build_requires(setup_py)
            for breq in breqs:
                build_requires.append(
                    poetry_pkg.dependency_from_pep_508(breq))

        return build_requires


class PythonMixin:

    def get_configure_script(self, build) -> str:
        return ''

    def get_bdist_wheel_command(self, build) -> list:
        bdir = build.get_build_dir(self, relative_to='pkgsource')
        return ['bdist_wheel', '-d', bdir]

    def get_build_script(self, build) -> str:
        sdir = build.get_source_dir(self, relative_to='pkgbuild')
        src_python = build.sh_get_command('python', relative_to='pkgsource')
        build_python = build.sh_get_command('python')
        dest = (
            build.get_temp_root(relative_to='pkgbuild') /
            build.get_install_prefix().relative_to('/')
        )

        sitescript = (
            f'import site; '
            f'print(site.getsitepackages(["{dest}"])[0])')

        wheeldir_script = 'import pathlib; print(pathlib.Path(".").resolve())'

        bdist_wheel = ' '.join(
            shlex.quote(str(c)) for c in self.get_bdist_wheel_command(build))

        return textwrap.dedent(f'''\
            (cd "{sdir}"; \\
             env SETUPTOOLS_SCM_PRETEND_VERSION="{self.pretty_version}" \\
                 "{src_python}" setup.py --verbose {bdist_wheel})
            _wheeldir=$("{build_python}" -c '{wheeldir_script}')
            _target=$("{build_python}" -c '{sitescript}')
            "{build_python}" -m pip install \\
                --no-warn-script-location \\
                --no-index --no-deps --upgrade \\
                -f "file://${{_wheeldir}}" \\
                --only-binary :all: "{self.name}" --target "${{_target}}"
        ''')

    def get_build_install_script(self, build) -> str:
        common_script = super().get_build_install_script(build)

        python = build.sh_get_command('python')
        root = build.get_install_dir(self, relative_to='pkgbuild')
        wheeldir_script = 'import pathlib; print(pathlib.Path(".").resolve())'

        wheel_install = textwrap.dedent(f'''\
            _wheeldir=$("{python}" -c '{wheeldir_script}')
            "{python}" -m pip install \\
                --no-index --no-deps --upgrade --force-reinstall \\
                --no-warn-script-location -f "file://${{_wheeldir}}" \\
                --only-binary :all: --root "{root}" "{self.name}"
        ''')

        if common_script:
            return f'{common_script}\n{wheel_install}'
        else:
            return wheel_install

    def get_install_script(self, build) -> str:
        return ''

    def get_install_list_script(self, build) -> str:
        common_script = super().get_install_list_script(build)

        prefix = build.get_install_prefix()
        dest = build.get_install_dir(self, relative_to='pkgbuild')

        dist_name = self.pretty_name.replace('-', '_')

        pyscript = textwrap.dedent(f'''\
            import pathlib
            import site

            sitepackages = pathlib.Path(site.getsitepackages(["{prefix}"])[0])
            abs_sitepackages = (
                pathlib.Path("{dest}") /
                sitepackages.relative_to('/')
            )

            record = (
                abs_sitepackages /
                f'{dist_name}-{self.pretty_version}.dist-info' /
                'RECORD'
            )

            if not record.exists():
                raise RuntimeError(f'no wheel RECORD for {self.name}')

            with open(record) as f:
                for entry in f:
                    filename = entry.split(',')[0]
                    install_path = (sitepackages / filename).resolve()
                    print(install_path.relative_to('/'))
        ''')

        scriptfile_name = f'_gen_install_list_from_wheel_{self.unique_name}.py'

        wheel_files = build.sh_write_python_helper(
            scriptfile_name, pyscript, relative_to='pkgbuild')

        if common_script:
            return f'{common_script}\n{wheel_files}'
        else:
            return wheel_files


class PythonPackage(PythonMixin, base.BasePackage):

    def get_sources(self) -> typing.List[af_sources.BaseSource]:
        if getattr(self, 'source', None) is None:
            raise RuntimeError(f'no source information for {self!r}')

        return [self.source]

    def __repr__(self):
        return "<PythonPackage {}>".format(self.unique_name)


class BundledPythonPackage(PythonMixin, base.BundledPackage):

    @classmethod
    def get_package_repository(cls, target, io):
        return PyPiRepository(io=io)

    @classmethod
    def resolve(cls, io) -> 'BundledPythonPackage':
        repo_dir = cls.resolve_vcs_source(io)
        setup_py = repo_dir / 'setup.py'

        if not setup_py.exists():
            raise RuntimeError(f'{cls}/setup.py does not exist')

        dist = tools.python.get_dist(repo_dir)

        requires = []
        for req in dist.metadata.run_requires:
            requires.append(
                poetry_pkg.dependency_from_pep_508(req))

        package = cls(dist.version, requires=requires)

        build_requires = tools.python.get_build_requires(setup_py)
        for breq in build_requires:
            package.build_requires.append(
                poetry_pkg.dependency_from_pep_508(breq))

        return package

    def get_requirements(self) -> typing.List[poetry_pkg.Dependency]:
        reqs = super().get_requirements()
        reqs.append(python_dependency)
        reqs.append(wheel_dependency)
        return reqs

    def get_install_list_script(self, build) -> str:
        static_list = base.BundledPackage.get_install_list_script(self, build)
        wheel_list = super().get_install_list_script(build)

        if static_list:
            return f'{static_list}\n{wheel_list}'
        else:
            return wheel_list
