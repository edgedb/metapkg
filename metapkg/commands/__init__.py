from .build import Build
from .copytree import CopyTree

commands = [
    Build,
    CopyTree,
]

__all__ = [cmd.__name__ for cmd in commands]
