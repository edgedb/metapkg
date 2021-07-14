from __future__ import annotations
from typing import *

import collections
import copy
import dataclasses
import enum
import glob
import os
import pathlib
import pprint
import sys
import textwrap

from poetry.core.packages import dependency as poetry_dep
from poetry.core.packages import dependency_group as poetry_depgroup
from poetry.core.packages import package as poetry_pkg
from poetry.core import vcs

from . import repository
from . import sources as af_sources


class Dependency(poetry_dep.Dependency):
    pass


class DummyPackage(poetry_pkg.Package):
    def __repr__(self) -> str:
        return "<DummyPackage {}>".format(self.unique_name)


class PackageFileLayout(enum.IntEnum):

    REGULAR = enum.auto()
    FLAT = enum.auto()


@dataclasses.dataclass
class MetaPackage:

    name: str
    description: str
    dependencies: dict[str, str]


class BasePackage(poetry_pkg.Package):
    def get_requirements(self) -> typing.List[Dependency]:
        return []

    def get_build_requirements(self) -> typing.List[Dependency]:
        return []

    def get_configure_script(self, build) -> str:
        raise NotImplementedError(f"{self}.configure()")

    def get_build_script(self, build) -> str:
        raise NotImplementedError(f"{self}.build()")

    def get_build_install_script(self, build) -> str:
        return ""

    def get_install_script(self, build) -> str:
        return ""

    def get_build_tools(self, build) -> dict:
        return {}

    def get_patches(
        self,
    ) -> dict[str, list[tuple[str, str]]]:
        return {}

    def get_install_list_script(self, build) -> str:
        return ""

    def get_no_install_list_script(self, build) -> str:
        return ""

    def get_ignore_list_script(self, build) -> str:
        return ""

    def get_private_libraries(self, build) -> list:
        return []

    def get_extra_system_requirements(self, build) -> dict:
        return {}

    def get_before_install_script(self, build) -> str:
        return ""

    def get_after_install_script(self, build) -> str:
        return ""

    def get_service_scripts(self, build) -> dict:
        return {}

    def get_bin_shims(self, build) -> dict:
        return {}

    def get_exposed_commands(self, build) -> list:
        return []

    def get_shlib_paths(self, build) -> List[pathlib.Path]:
        return []

    def get_include_paths(self, build) -> List[pathlib.Path]:
        return []


class BundledPackage(BasePackage):

    title = None
    name = None
    aliases = None
    description = None
    license = None
    group = None
    url = None
    identifier = None

    build_required: list[poetry_dep.Dependency]
    source_version: str

    artifact_requirements: list[Union[str, poetry_dep.Dependency]] = []
    artifact_build_requirements: list[Union[str, poetry_dep.Dependency]] = []

    @property
    def slot(self) -> str:
        return ""

    @property
    def slot_suffix(self) -> str:
        if self.slot:
            return f"-{self.slot}"
        else:
            return ""

    @property
    def name_slot(self):
        return f"{self.name}{self.slot_suffix}"

    @classmethod
    def get_source_url_variables(cls, version: str) -> dict[str, str]:
        return {}

    @classmethod
    def _get_sources(cls, version: str) -> list[af_sources.BaseSource]:
        sources = []

        if version is None:
            version = "HEAD"
        underscore_v = version.replace(".", "_")
        dash_v = version.replace(".", "-")
        for source in cls.sources:
            if isinstance(source, dict):
                url = source["url"].format(
                    version=version,
                    underscore_version=underscore_v,
                    dash_version=dash_v,
                    **cls.get_source_url_variables(version),
                )
                extras = source.get("extras")
                if extras:
                    extras = {
                        k.replace("-", "_"): v for k, v in extras.items()
                    }

                    if "version" not in extras:
                        extras["version"] = version
                else:
                    extras = {"version": version}

                if "vcs_version" not in extras:
                    extras["vcs_version"] = cls.to_vcs_version(
                        extras["version"]
                    )

                src = af_sources.source_for_url(url, extras)

                csum = source.get("csum")
                csum_url = source.get("csum_url")
                csum_algo = source.get("csum_algo")

                if csum_algo:
                    if csum_url:
                        csum_url = csum_url.format(
                            version=version,
                            underscore_version=underscore_v,
                            dash_version=dash_v,
                        )
                    csum_verify = af_sources.HashVerification(
                        csum_algo, hash_url=csum_url, hash_value=csum
                    )
                    src.add_verification(csum_verify)

            else:
                src = af_sources.source_for_url(source)

            sources.append(src)

        return sources

    @classmethod
    def to_vcs_version(cls, version):
        return version

    @classmethod
    def get_package_repository(cls, target, io):
        return repository.bundle_repo

    @classmethod
    def resolve_vcs_source(cls, io, *, ref=None) -> pathlib.Path:
        sources = cls._get_sources(version=ref)
        if len(sources) == 1 and isinstance(sources[0], af_sources.GitSource):
            repo_dir = sources[0].download(io)
        else:
            raise ValueError("Unable to resolve non-git bundled package")

        return repo_dir

    @classmethod
    def resolve_version(cls, io) -> str:
        repo_dir = cls.resolve_vcs_source(io)
        return vcs.Git(repo_dir).rev_parse("HEAD").strip()

    @classmethod
    def resolve(cls, io, *, version=None) -> "BundledPackage":
        if version is None:
            version = cls.resolve_version(io)
        return cls(version=version)

    def get_sources(self) -> list[af_sources.BaseSource]:
        return self._get_sources(version=self.source_version)

    def get_patches(
        self,
    ) -> dict[str, list[tuple[str, str]]]:
        modpath = pathlib.Path(sys.modules[self.__module__].__path__[0])
        patches_dir = modpath / "patches"

        patches = collections.defaultdict(list)
        if patches_dir.exists():
            for path in patches_dir.glob("*.patch"):
                with open(path, "r") as f:
                    pkg, _, rest = path.stem.partition("__")
                    patches[pkg].append((rest, f.read()))

            for pkg, plist in patches.items():
                plist.sort(key=lambda i: i[0])

        return patches

    def __init__(
        self,
        version: str,
        pretty_version: Optional[str] = None,
        *,
        source_version: Optional[str] = None,
        requires=None,
        name: Optional[str] = None,
        aliases: Optional[list[str]] = None,
    ) -> None:

        if self.title is None:
            raise RuntimeError(
                f"{type(self)!r} does not define the required "
                f"title attribute"
            )

        if name is not None:
            self.name = name
        elif self.name is None:
            self.name = self.title.lower()

        if aliases is not None:
            self.aliases = aliases

        super().__init__(self.name, version)

        if requires is not None:
            reqs = list(requires)
        else:
            reqs = []

        reqs.extend(self.get_requirements())

        if reqs:
            if "default" not in self._dependency_groups:
                self._dependency_groups[
                    "default"
                ] = poetry_depgroup.DependencyGroup("default")
        for req in reqs:
            self._dependency_groups["default"].add_dependency(req)

        self.build_requires = self.get_build_requirements()
        self.description = type(self).description
        if source_version is None:
            self.source_version = self.pretty_version
        else:
            self.source_version = source_version

        repository.bundle_repo.add_package(self)

        if self.aliases:
            for alias in self.aliases:
                pkg = DummyPackage(name=alias, version=self.version)
                pkg.add_dependency(
                    poetry_dep.Dependency(self.name, self.version)
                )
                repository.bundle_repo.add_package(pkg)

    def get_requirements(self) -> list[Dependency]:
        reqs = []
        for item in self.artifact_requirements:
            if isinstance(item, str):
                reqs.append(poetry_dep.Dependency.create_from_pep_508(item))
            else:
                reqs.append(item)
        return reqs

    def get_build_requirements(self) -> list[Dependency]:
        reqs = []
        for item in self.artifact_build_requirements:
            if isinstance(item, str):
                reqs.append(poetry_dep.Dependency.create_from_pep_508(item))
            else:
                reqs.append(item)
        return reqs

    def clone(self):
        clone = self.__class__(self.version)
        clone.__dict__ = copy.deepcopy(self.__dict__)
        return clone

    def is_root(self):
        return False

    def write_file_list_script(self, build, listname, entries) -> str:
        installdest = build.get_install_dir(self, relative_to="pkgbuild")

        paths = {}
        for aspect in ("systembin", "bin", "data", "include", "lib"):
            path = build.get_install_path(aspect).relative_to("/")
            paths[f"{aspect}dir"] = path

        paths["prefix"] = build.get_full_install_prefix().relative_to("/")
        paths["exesuffix"] = build.get_exe_suffix()

        processed_entries = []
        for entry in entries:
            processed_entries.append(
                entry.strip().format(**paths).replace("/", os.sep)
            )

        pyscript = textwrap.dedent(
            """\
            import glob
            import pathlib

            tmp = pathlib.Path({installdest!r})

            patterns = {patterns}

            for pattern in patterns:
                for path in glob.glob(str(tmp / pattern), recursive=True):
                    p = pathlib.Path(path)
                    if p.exists():
                        print(p.relative_to(tmp))
        """
        ).format(
            installdest=str(installdest),
            patterns=pprint.pformat(processed_entries),
        )

        scriptfile_name = f"_gen_{listname}_list_{self.unique_name}.py"

        return build.sh_write_python_helper(
            scriptfile_name, pyscript, relative_to="pkgbuild"
        )

    def read_support_files(self, build, file_glob, binary=False) -> dict:

        mod = sys.modules[type(self).__module__]
        path = pathlib.Path(mod.__file__).parent / file_glob

        result = {}

        for pathname in glob.glob(str(path)):
            path = pathlib.Path(pathname)
            mode = "rb" if binary else "r"
            with open(path, mode) as f:
                content = f.read()
                name = path.name
                if not binary and name.endswith(".in"):
                    content = build.format_package_template(content, self)
                    name = name[:-3]
                    name = name.replace("SLOT", self.slot)
                    name = name.replace(
                        "IDENTIFIER",
                        build.target.get_package_system_ident(build, self),
                    )
                result[name] = content

        return result

    def _get_file_list_script(
        self, build, listname, *, extra_files=None
    ) -> str:
        mod = sys.modules[type(self).__module__]
        path = pathlib.Path(mod.__file__).parent / f"{listname}.list"

        entries: list[str] = []

        if path.exists():
            with open(path, "r") as f:
                entries.extend(f)

        if extra_files:
            entries.extend(str(p.relative_to("/")) for p in extra_files)

        if entries:
            script = self.write_file_list_script(build, listname, entries)
        else:
            script = ""

        return script

    def get_install_list_script(self, build) -> str:
        extra_files = list(self.get_service_scripts(build))
        return self._get_file_list_script(
            build, "install", extra_files=extra_files
        )

    def get_no_install_list_script(self, build) -> str:
        return self._get_file_list_script(build, "no_install")

    def get_ignore_list_script(self, build) -> str:
        return self._get_file_list_script(build, "ignore")

    def get_build_install_script(self, build) -> str:
        service_scripts = self.get_service_scripts(build)
        if service_scripts:
            install = build.sh_get_command("cp", relative_to="pkgbuild")
            extras_dir = build.get_extras_root(relative_to="pkgbuild")
            install_dir = build.get_install_dir(self, relative_to="pkgbuild")
            ensuredir = build.target.get_action("ensuredir", build)

            commands = []

            for path, content in service_scripts.items():
                path = path.relative_to("/")
                commands.append(
                    ensuredir.get_script(path=(install_dir / path).parent)
                )
                args = {
                    str(extras_dir / path): None,
                    str(install_dir / path): None,
                }
                cmd = build.sh_format_command(install, args)
                commands.append(cmd)

            return "\n".join(commands)
        else:
            return ""

    def get_resources(self, build) -> dict:
        return self.read_support_files(build, "resources/*", binary=True)

    def get_service_scripts(self, build) -> dict:
        return build.target.service_scripts_for_package(build, self)

    def get_bin_shims(self, build) -> dict:
        return self.read_support_files(build, "shims/*")

    def get_package_layout(self, build) -> PackageFileLayout:
        return PackageFileLayout.REGULAR

    def __repr__(self):
        return "<BundledPackage {}>".format(self.unique_name)

    def get_meta_packages(
        self,
        build,
        root_version,
    ) -> list[MetaPackage]:
        return []

    def get_conflict_packages(
        self,
        build,
        root_version,
    ) -> list[str]:
        return []

    def get_provided_packages(
        self,
        build,
        root_version,
    ) -> list[tuple[str, str]]:
        return []

    def get_artifact_metadata(self, build) -> typing.Dict[str, str]:
        if self.slot:
            return {"version_slot": self.slot}
        else:
            return {}
