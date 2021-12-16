from __future__ import annotations
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Literal,
    Mapping,
    TypeVar,
    overload,
)

import collections
import copy
import dataclasses
import enum
import glob
import os
import pathlib
import pprint
import shlex
import sys
import textwrap

from poetry.core import vcs
from poetry.core.packages import dependency as poetry_dep
from poetry.core.packages import dependency_group as poetry_depgroup
from poetry.core.packages import package as poetry_pkg
from poetry.core.semver import version as poetry_version

from . import repository
from . import sources as af_sources

if TYPE_CHECKING:
    from cleo.io import io as cleo_io
    from metapkg import targets


class Dependency(poetry_dep.Dependency):  # type: ignore
    pass


class DummyPackage(poetry_pkg.Package):  # type: ignore
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


class BasePackage(poetry_pkg.Package):  # type: ignore
    def get_requirements(self) -> list[Dependency]:
        return []

    def get_build_requirements(self) -> list[Dependency]:
        return []

    def get_license_files_pattern(self) -> str:
        return "{LICENSE*,COPYING,NOTICE,COPYRIGHT}"

    def get_configure_script(self, build: targets.Build) -> str:
        raise NotImplementedError(f"{self}.configure()")

    def get_build_script(self, build: targets.Build) -> str:
        raise NotImplementedError(f"{self}.build()")

    def get_build_install_script(self, build: targets.Build) -> str:
        script = ""

        licenses = self.get_license_files_pattern()
        if licenses:
            sdir = build.get_source_dir(self, relative_to="pkgbuild")
            legaldir = build.get_install_path("legal").relative_to("/")
            lic_dest = (
                build.get_install_dir(self, relative_to="pkgbuild") / legaldir
            )
            prefix = str(lic_dest / self.name)
            script += textwrap.dedent(
                f"""\
                mkdir -p "{lic_dest}"
                for _lic_src in "{sdir}"/{licenses}; do
                    if [ -e "$_lic_src" ]; then
                        cp "$_lic_src" "{prefix}-$(basename "$_lic_src")"
                    fi
                done
                """
            )

        return script

    def get_build_tools(self, build: targets.Build) -> dict[str, pathlib.Path]:
        return {}

    def get_patches(
        self,
    ) -> dict[str, list[tuple[str, str]]]:
        return {}

    def _get_file_list_script(
        self,
        build: targets.Build,
        listname: str,
        *,
        entries: list[str],
    ) -> str:
        if entries:
            script = self.write_file_list_script(build, listname, entries)
        else:
            script = ""

        return script

    def get_file_install_entries(self, build: targets.Build) -> list[str]:
        entries = []
        if self.get_license_files_pattern():
            entries.append("{legaldir}/*")
        return entries

    def get_install_list_script(self, build: targets.Build) -> str:
        entries = self.get_file_install_entries(build)
        entries += [
            str(p.relative_to("/")) for p in self.get_service_scripts(build)
        ]
        return self._get_file_list_script(build, "install", entries=entries)

    def get_file_no_install_entries(self, build: targets.Build) -> list[str]:
        return []

    def get_no_install_list_script(self, build: targets.Build) -> str:
        entries = self.get_file_no_install_entries(build)
        return self._get_file_list_script(build, "no_install", entries=entries)

    def get_file_ignore_entries(self, build: targets.Build) -> list[str]:
        return []

    def get_ignore_list_script(self, build: targets.Build) -> str:
        entries = self.get_file_ignore_entries(build)
        return self._get_file_list_script(build, "ignore", entries=entries)

    def get_private_libraries(self, build: targets.Build) -> list[str]:
        return []

    def get_extra_system_requirements(
        self, build: targets.Build
    ) -> dict[str, list[str]]:
        return {}

    def get_before_install_script(self, build: targets.Build) -> str:
        return ""

    def get_after_install_script(self, build: targets.Build) -> str:
        return ""

    def get_service_scripts(
        self, build: targets.Build
    ) -> dict[pathlib.Path, str]:
        return {}

    def get_bin_shims(self, build: targets.Build) -> dict[str, str]:
        return {}

    def get_exposed_commands(self, build: targets.Build) -> list[pathlib.Path]:
        return []

    def get_shlib_paths(self, build: targets.Build) -> list[pathlib.Path]:
        return []

    def get_shlibs(self, build: targets.Build) -> list[str]:
        return []

    def get_include_paths(self, build: targets.Build) -> list[pathlib.Path]:
        return []

    def write_file_list_script(
        self, build: targets.Build, listname: str, entries: list[str]
    ) -> str:
        installdest = build.get_install_dir(self, relative_to="pkgbuild")

        paths: dict[str, str | pathlib.Path] = {}
        for aspect in ("systembin", "bin", "data", "include", "lib", "legal"):
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


BundledPackage_T = TypeVar("BundledPackage_T", bound="BundledPackage")


class BundledPackage(BasePackage):

    name: ClassVar[str]
    title: ClassVar[str | None] = None
    aliases: ClassVar[list[str] | None] = None
    description: ClassVar[str | None] = None
    license: ClassVar[str | None] = None
    group: ClassVar[str]
    url: ClassVar[str | None] = None
    identifier: ClassVar[str]

    build_required: list[poetry_dep.Dependency]
    source_version: str

    artifact_requirements: list[str | poetry_dep.Dependency] = []
    artifact_build_requirements: list[str | poetry_dep.Dependency] = []

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
    def name_slot(self) -> str:
        return f"{self.name}{self.slot_suffix}"

    def version_includes_revision(self) -> bool:
        return False

    @classmethod
    def get_source_url_variables(cls, version: str) -> dict[str, str]:
        return {}

    @classmethod
    def _get_sources(cls, version: str | None) -> list[af_sources.BaseSource]:
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
    def to_vcs_version(cls, version: str) -> str:
        return version

    @classmethod
    def get_package_repository(
        cls, target: targets.Target, io: cleo_io.IO
    ) -> repository.BundleRepository:
        return repository.bundle_repo

    @classmethod
    def resolve_vcs_source(
        cls, io: cleo_io.IO, *, ref: str | None = None
    ) -> pathlib.Path:
        sources = cls._get_sources(version=ref)
        if len(sources) == 1 and isinstance(sources[0], af_sources.GitSource):
            repo_dir = sources[0].download(io)
        else:
            raise ValueError("Unable to resolve non-git bundled package")

        return repo_dir

    @classmethod
    def resolve_version(cls, io: cleo_io.IO) -> str:
        repo_dir = cls.resolve_vcs_source(io)
        return vcs.Git(repo_dir).rev_parse("HEAD").strip()  # type: ignore

    @classmethod
    def resolve(
        cls,
        io: cleo_io.IO,
        *,
        ref: str | None = None,
        version: str | None = None,
        revision: str | None = None,
        is_release: bool = False,
        target: targets.Target,
    ) -> BundledPackage:
        if version is None:
            version = cls.resolve_version(io)
        return cls(version=version)

    def get_sources(self) -> list[af_sources.BaseSource]:
        return self._get_sources(version=self.source_version)

    def get_patches(
        self,
    ) -> dict[str, list[tuple[str, str]]]:
        modpath = pathlib.Path(
            sys.modules[self.__module__].__path__[0]  # type: ignore
        )
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
        pretty_version: str | None = None,
        *,
        source_version: str | None = None,
        requires: list[poetry_pkg.Package] | None = None,
    ) -> None:

        if self.title is None:
            raise RuntimeError(
                f"{type(self)!r} does not define the required "
                f"title attribute"
            )

        super().__init__(self.name, version, pretty_version=pretty_version)

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
        self.description = type(self).description  # type: ignore
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

    def clone(self: BundledPackage_T) -> BundledPackage_T:
        clone = self.__class__(self.version)
        clone.__dict__ = copy.deepcopy(self.__dict__)
        return clone

    def is_root(self) -> bool:
        return False

    @overload
    def read_support_files(
        self, build: targets.Build, file_glob: str, binary: Literal[False]
    ) -> dict[str, str]:
        ...

    @overload
    def read_support_files(
        self, build: targets.Build, file_glob: str
    ) -> dict[str, str]:
        ...

    @overload
    def read_support_files(
        self, build: targets.Build, file_glob: str, binary: Literal[True]
    ) -> dict[str, bytes]:
        ...

    def read_support_files(
        self, build: targets.Build, file_glob: str, binary: bool = False
    ) -> dict[str, str] | dict[str, bytes]:

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

    def _read_install_entries(
        self,
        build: targets.Build,
        listname: str,
    ) -> list[str]:
        mod = sys.modules[type(self).__module__]
        path = pathlib.Path(mod.__file__).parent / f"{listname}.list"

        entries: list[str] = []

        if path.exists():
            with open(path, "r") as f:
                entries.extend(f)

        return entries

    def get_file_install_entries(self, build: targets.Build) -> list[str]:
        entries = super().get_file_install_entries(build)
        return entries + self._read_install_entries(build, "install")

    def get_file_no_install_entries(self, build: targets.Build) -> list[str]:
        entries = super().get_file_no_install_entries(build)
        return entries + self._read_install_entries(build, "no_install")

    def get_file_ignore_entries(self, build: targets.Build) -> list[str]:
        entries = super().get_file_ignore_entries(build)
        return entries + self._read_install_entries(build, "ignore")

    def get_build_install_script(self, build: targets.Build) -> str:
        service_scripts = self.get_service_scripts(build)
        if service_scripts:
            install = build.sh_get_command("cp", relative_to="pkgbuild")
            extras_dir = build.get_extras_root(relative_to="pkgbuild")
            install_dir = build.get_install_dir(self, relative_to="pkgbuild")
            ensuredir = build.target.get_action("ensuredir", build)
            if TYPE_CHECKING:
                assert isinstance(ensuredir, targets.EnsureDirAction)

            commands = []

            for path, _content in service_scripts.items():
                path = path.relative_to("/")
                commands.append(
                    ensuredir.get_script(path=str((install_dir / path).parent))
                )
                args: dict[str, str | None] = {
                    str(extras_dir / path): None,
                    str(install_dir / path): None,
                }
                cmd = build.sh_format_command(install, args)
                commands.append(cmd)

            return "\n".join(commands)
        else:
            return ""

    def get_resources(self, build: targets.Build) -> dict[str, bytes]:
        return self.read_support_files(build, "resources/*", binary=True)

    def get_service_scripts(
        self, build: targets.Build
    ) -> dict[pathlib.Path, str]:
        return build.target.service_scripts_for_package(build, self)

    def get_bin_shims(self, build: targets.Build) -> dict[str, str]:
        return self.read_support_files(build, "shims/*")

    def get_package_layout(self, build: targets.Build) -> PackageFileLayout:
        return PackageFileLayout.REGULAR

    def __repr__(self) -> str:
        return "<BundledPackage {}>".format(self.unique_name)

    def get_meta_packages(
        self,
        build: targets.Build,
        root_version: str,
    ) -> list[MetaPackage]:
        return []

    def get_conflict_packages(
        self,
        build: targets.Build,
        root_version: str,
    ) -> list[str]:
        return []

    def get_provided_packages(
        self,
        build: targets.Build,
        root_version: str,
    ) -> list[tuple[str, str]]:
        return []

    def get_artifact_metadata(self, build: targets.Build) -> dict[str, Any]:
        pv = poetry_version.Version.parse(self.pretty_version)

        prerelease = []
        if pv.pre is not None:
            prerelease.append(
                {
                    "phase": pv.pre.phase,
                    "number": pv.pre.number,
                }
            )

        if pv.dev is not None:
            prerelease.append(
                {
                    "phase": pv.dev.phase,
                    "number": pv.dev.number,
                }
            )

        if pv.local:
            local: tuple[str, ...]
            if not isinstance(pv.local, tuple):
                local = (pv.local,)
            else:
                local = pv.local

            ver_metadata = self.parse_version_metadata(build, local)
        else:
            ver_metadata = {}

        metadata = {
            "name": self.name,
            "version": self.version.to_string(short=False),
            "version_details": {
                "major": pv.major,
                "minor": pv.minor,
                "patch": pv.patch,
                "prerelease": prerelease,
                "metadata": ver_metadata,
            },
            "revision": build.revision,
            "target": build.target.triple,
            "architecture": build.target.machine_architecture,
            "dist": build.target.ident,
            "channel": build.channel,
        }

        if self.slot:
            metadata["version_slot"] = self.slot

        return metadata

    def parse_version_metadata(
        self, build: targets.Build, segments: tuple[str, ...]
    ) -> dict[str, str]:
        result = {}
        pfx_map = self.get_version_metadata_fields()
        for segment in segments:
            for pfx_len in (1, 2):
                key = pfx_map.get(segment[:pfx_len])
                if key is not None:
                    result[key] = segment[pfx_len:]
                    break
            else:
                raise RuntimeError(
                    f"unrecognized version metadata field `{segment}`"
                )

        return result

    def get_version_metadata_fields(self) -> dict[str, str]:
        return {
            "r": "build_revision",
            "d": "source_date",
            "g": "scm_revision",
            "t": "target",
            "s": "build_hash",
            "b": "build_type",
        }


class BundledCPackage(BundledPackage):
    def sh_configure(
        self,
        build: targets.Build,
        path: str | pathlib.Path,
        args: Mapping[str, str | pathlib.Path | None],
    ) -> str:
        conf_args = dict(args)
        shlib_paths = self.get_shlib_paths(build)
        ldflags = []
        for shlib_path in shlib_paths:
            ldflags.extend(
                build.target.get_shlib_path_run_time_ldflags(
                    build, shlex.quote(str(shlib_path))
                )
            )
        if ldflags:
            build.sh_append_flags(conf_args, "LDFLAGS", ldflags)

        if "--prefix" not in args:
            conf_args["--prefix"] = str(build.get_full_install_prefix())

        conf_args = build.sh_append_global_flags(conf_args)
        return build.sh_format_command(path, conf_args, force_args_eq=True)

    def get_shlib_paths(self, build: targets.Build) -> list[pathlib.Path]:
        return [build.get_full_install_prefix() / "lib"]

    def get_include_paths(self, build: targets.Build) -> list[pathlib.Path]:
        return [build.get_full_install_prefix() / "include"]

    def get_configure_script(self, build: targets.Build) -> str:
        sdir = build.get_source_dir(self, relative_to="pkgbuild")
        configure = sdir / "configure"
        return self.sh_configure(build, configure, {})

    def get_build_script(self, build: targets.Build) -> str:
        make = build.sh_get_command("make")

        return textwrap.dedent(
            f"""\
            {make}
        """
        )

    def get_build_install_script(self, build: targets.Build) -> str:
        script = super().get_build_install_script(build)
        installdest = build.get_install_dir(self, relative_to="pkgbuild")
        make = build.sh_get_command("make")

        script += textwrap.dedent(
            f"""\
            {make} DESTDIR=$(pwd)/"{installdest}" install
            """
        )

        return script
