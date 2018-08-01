import collections
import pathlib
import pprint
import sys
import textwrap
import typing

from poetry import packages as poetry_pkg
from poetry import vcs

from . import repository
from . import sources as af_sources


class Dependency(poetry_pkg.Dependency):
    pass


class BasePackage(poetry_pkg.Package):

    def get_configure_script(self, build) -> str:
        raise NotImplementedError(f'{self}.configure()')

    def get_build_script(self, build) -> str:
        raise NotImplementedError(f'{self}.build()')

    def get_build_install_script(self, build) -> str:
        raise NotImplementedError(f'{self}.build_install()')

    def get_install_script(self, build) -> str:
        return ''

    def get_build_tools(self, build) -> dict:
        return {}

    def get_patches(self) -> \
            typing.Dict[str, typing.List[typing.Tuple[str, str]]]:
        return {}

    def get_install_list_script(self, build) -> str:
        return ''

    def get_no_install_list_script(self, build) -> str:
        return ''

    def get_ignore_list_script(self, build) -> str:
        return ''

    def get_private_libraries(self, build) -> list:
        return []


class BundledPackage(BasePackage):

    title = None
    name = None
    description = None
    license = None
    group = None
    url = None

    artifact_requirements = []
    artifact_build_requirements = []

    @classmethod
    def _get_sources(cls, version: str) -> typing.List[af_sources.BaseSource]:
        sources = []

        for source in cls.sources:
            if isinstance(source, dict):
                url = source['url'].format(version=version)
                src = af_sources.source_for_url(url)

                csum = source.get('csum')
                csum_url = source.get('csum_url')
                csum_algo = source.get('csum_algo')

                if csum_algo:
                    if csum_url:
                        csum_url = csum_url.format(version=version)
                    csum_verify = af_sources.HashVerification(
                        csum_algo, hash_url=csum_url, hash_value=csum)
                    src.add_verification(csum_verify)

            else:
                src = af_sources.source_for_url(source)

            sources.append(src)

        return sources

    @classmethod
    def get_package_repository(cls, target, io):
        return repository.bundle_repo

    @classmethod
    def resolve_vcs_source(cls, io) -> str:
        sources = cls._get_sources(version='git')
        if len(sources) == 1 and isinstance(sources[0], af_sources.GitSource):
            repo_dir = sources[0].download(io)
        else:
            raise ValueError('Unable to resolve non-git bundled package')

        return repo_dir

    @classmethod
    def resolve_version(cls, io) -> str:
        repo_dir = cls.resolve_vcs_source(io)
        return vcs.Git(repo_dir).rev_parse('HEAD').strip()

    @classmethod
    def resolve(cls, io) -> 'BundledPackage':
        version = cls.resolve_version(io)
        return cls(version=version)

    def get_sources(self) -> typing.List[af_sources.BaseSource]:
        return self._get_sources(version=self.pretty_version)

    def get_patches(self) -> \
            typing.Dict[str, typing.List[typing.Tuple[str, str]]]:
        modpath = pathlib.Path(sys.modules[self.__module__].__path__[0])
        patches_dir = modpath / 'patches'

        patches = collections.defaultdict(list)
        if patches_dir.exists():
            for path in patches_dir.glob('*.patch'):
                with open(path, 'r') as f:
                    pkg, _, rest = path.stem.partition('__')
                    patches[pkg].append((rest, f.read()))

            for pkg, plist in patches.items():
                plist.sort(key=lambda i: i[0])

        return patches

    def __init__(self, version: str,
                 pretty_version: typing.Optional[str]=None, *,
                 requires=None) -> None:

        if self.title is None:
            raise RuntimeError(f'{type(self)!r} does not define the required '
                               f'title attribute')

        if self.name is None:
            self.name = self.title.lower()

        super().__init__(self.name, version)

        if requires is not None:
            self.requires = set(requires)
        else:
            self.requires = set()

        extra_requires = self.get_requirements()
        self.requires.update(extra_requires)
        self.build_requires = self.get_build_requirements()
        self.description = type(self).description

        repository.bundle_repo.add_package(self)

    def get_requirements(self) -> typing.List[Dependency]:
        reqs = []
        for item in self.artifact_requirements:
            if isinstance(item, str):
                reqs.append(poetry_pkg.dependency_from_pep_508(item))
            else:
                reqs.append(item)
        return reqs

    def get_build_requirements(self) -> typing.List[Dependency]:
        reqs = []
        for item in self.artifact_build_requirements:
            if isinstance(item, str):
                reqs.append(poetry_pkg.dependency_from_pep_508(item))
            else:
                reqs.append(item)
        return reqs

    def clone(self):
        clone = self.__class__(self.version, requires=self.requires)
        clone.build_requires.extend(self.build_requires)
        return clone

    def is_root(self):
        return False

    def write_file_list_script(self, build, listname, entries) -> str:
        installdest = build.get_install_dir(self, relative_to='pkgbuild')

        paths = {}
        for aspect in ('bin', 'data', 'include', 'lib'):
            path = build.get_install_path(aspect).relative_to('/')
            paths[f'{aspect}dir'] = path

        paths['prefix'] = build.get_install_prefix().relative_to('/')

        processed_entries = []
        for entry in entries:
            processed_entries.append(entry.strip().format(**paths))

        pyscript = textwrap.dedent('''\
            import glob
            import pathlib

            tmp = pathlib.Path('{installdest}')

            patterns = {patterns}

            for pattern in patterns:
                for path in glob.glob(str(tmp / pattern), recursive=True):
                    print(pathlib.Path(path).relative_to(tmp))
        ''').format(
            installdest=installdest,
            patterns=pprint.pformat(processed_entries)
        )

        scriptfile_name = f'_gen_{listname}_list_{self.unique_name}.py'

        return build.sh_write_python_helper(
            scriptfile_name, pyscript, relative_to='pkgbuild')

    def _get_file_list_script(self, build, listname) -> str:
        mod = sys.modules[type(self).__module__]
        path = pathlib.Path(mod.__file__).parent / f'{listname}.list'

        if path.exists():
            with open(path, 'r') as f:
                entries = list(f)
            script = self.write_file_list_script(build, listname, entries)
        else:
            script = ''

        return script

    def get_install_list_script(self, build) -> str:
        return self._get_file_list_script(build, 'install')

    def get_no_install_list_script(self, build) -> str:
        return self._get_file_list_script(build, 'no_install')

    def get_ignore_list_script(self, build) -> str:
        return self._get_file_list_script(build, 'ignore')

    def __repr__(self):
        return "<BundledPackage {}>".format(self.unique_name)
