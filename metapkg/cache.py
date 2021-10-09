import os
import pathlib


CACHEHOME = os.environ.get("XDG_CACHE_HOME", pathlib.Path.home() / ".cache")
CACHEDIR = pathlib.Path(CACHEHOME) / "metapkg"


def cachedir() -> pathlib.Path:
    if not CACHEDIR.exists():
        CACHEDIR.mkdir(parents=True)

    return CACHEDIR
