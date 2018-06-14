from poetry import packages as poetry_pkg


class SystemPackage(poetry_pkg.Package):

    def __init__(self, name, version, pretty_version=None, system_name=None):
        super().__init__(name, version, pretty_version=pretty_version)
        self._system_name = system_name

    @property
    def system_name(self):
        return self._system_name

    def clone(self):
        clone = self.__class__(self.name, self.version, self.pretty_version,
                               self.system_name)
        for dep in self.requires:
            clone.requires.append(dep)

        return clone

    def __repr__(self):
        return "<SystemPackage {}>".format(self.unique_name)
