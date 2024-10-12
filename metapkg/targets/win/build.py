import os
import shutil

from metapkg.targets import generic


class Build(generic.Build):
    def define_tools(self) -> None:
        super().define_tools()
        # "realbash" below is to circumvent a dubious practice
        # of Windows intercepting bare invocations of "bash" to mean
        # "WSL", since make runs its shells using bare names even
        # if SHELL contains a fully-qualified path.
        self._system_tools["bash"] = "realbash"
        self._system_tools["python"] = "python"
        find = shutil.which("find")
        assert find is not None, "could not locate `find`"
        self._system_tools["find"] = find
        # Must use bsdtar, not msys tar, because the latter
        # chokes on Windows paths.
        windir = os.environ.get("WINDIR")
        assert windir, "WINDIR env var is not set"
        self._system_tools["tar"] = f"{windir}/System32/tar.exe"
        self._system_tools["meson"] = "meson"
        self._system_tools["cmake"] = "cmake"
        self._system_tools["ninja"] = "ninja"
