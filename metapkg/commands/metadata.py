from __future__ import annotations

import importlib
import json
import pathlib
import sys

from poetry.utils import env as poetry_env

from metapkg import targets

from . import base


class Metadata(base.Command):
    """Show metadata for a given package and version.

    metadata
        { name : Package to show metadata for. }
        { --generic : Build a generic target. }
        { --libc= : Libc to target. }
        { --release : Whether this build is a release. }
        { --source-ref= : Source version to build (VCS ref or tarball version). }
        { --pkg-revision= : Override package revision number (defaults to 1). }
    """

    help = """Returns metadata for a given package and version."""

    _loggers = ["metapkg.metadata"]

    def handle(self) -> int:
        pkgname = self.argument("name")
        generic = self.option("generic")
        libc = self.option("libc")
        version = self.option("source-ref")
        revision = self.option("pkg-revision")
        is_release = self.option("release")

        target = targets.detect_target(self.io, portable=generic, libc=libc)
        target.prepare()

        modname, _, clsname = pkgname.rpartition(":")

        mod = importlib.import_module(modname)
        pkgcls = getattr(mod, clsname)
        root_pkg = pkgcls.resolve(
            self.io,
            version=version,
            revision=revision,
            is_release=is_release,
            target=target,
        )

        env = poetry_env.SystemEnv(pathlib.Path(sys.executable))

        build = target.get_builder_instance(
            targets.BuildRequest(
                io=self.io,
                env=env,
                root_pkg=root_pkg,
                revision=revision or "1",
            ),
        )

        print(json.dumps(root_pkg.get_artifact_metadata(build)))

        return 0
