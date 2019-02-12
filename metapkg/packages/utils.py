from poetry import packages as poetry_pkg


def python_dependency_from_pep_508(name):
    dep = poetry_pkg.dependency_from_pep_508(name)
    dep._name = f'pypkg-{dep.name}'
    dep._pretty_name = f'pypkg-{dep.pretty_name}'
    return dep
