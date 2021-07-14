import shutil
from metapkg.targets import generic


class Build(generic.Build):
    def prepare(self):
        super().prepare()
        # "realbash" below is to circumvent a dubious practice
        # of Windows intercepting bare invocations of "bash" to mean
        # "WSL", since make runs its shells using bare names even
        # if SHELL contains a fully-qualified path.
        self._system_tools["bash"] = "realbash"
        self._system_tools["python"] = "python"
        self._system_tools["find"] = shutil.which("find")
