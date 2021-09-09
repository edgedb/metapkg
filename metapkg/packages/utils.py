from __future__ import annotations
from typing import *

from poetry.core.packages import dependency as poetry_dep


def python_dependency_from_pep_508(name: str) -> poetry_dep.Dependency:
    dep = poetry_dep.Dependency.create_from_pep_508(name)
    dep._name = f"pypkg-{dep.name}"
    dep._pretty_name = f"pypkg-{dep.pretty_name}"
    return dep
