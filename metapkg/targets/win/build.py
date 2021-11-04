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
        tar = shutil.which("tar")
        assert tar is not None, "could not locate `tar`"
        self._system_tools["tar"] = tar
