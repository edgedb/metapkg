import datetime
import mimetypes
import os
import pathlib
import plistlib
import shutil
import subprocess
import textwrap

from xml.dom import minidom

from metapkg import targets
from metapkg.targets import generic
from metapkg import tools


class Build(generic.Build):

    def _build(self):
        # super()._build()
        srcdir = self.get_image_root(relative_to=None)
        dirs = srcdir / 'Library' / 'Frameworks' / 'EdgeDB'
        dirs.mkdir(parents=True)
        with open(dirs / 'foo.txt', 'w') as f:
            print('Hey', file=f)
        self._build_installer()

    def _build_installer(self):
        name = self._root_pkg.name
        title = self._root_pkg.title
        version = self._root_pkg.pretty_version
        major = self._root_pkg.version.major
        ident = f'{self._root_pkg.identifier}.{title}-{major}'

        temp_root = self.get_temp_root(relative_to=None)
        installer = temp_root / 'installer'
        installer.mkdir(parents=True)

        pkgname = f'{title}-{major}.pkg'
        pkgpath = installer / pkgname

        srcdir = self.get_image_root(relative_to=None)
        tools.cmd('pkgbuild', '--root', srcdir, '--identifier', ident,
                  '--version', version, '--install-location', '/',
                  pkgpath)

        rsrcdir = installer / 'Resources'
        rsrcdir.mkdir(parents=True)

        resources = self._root_pkg.get_resources(self)

        for name, data in resources.items():
            with open(rsrcdir / name, 'wb') as f:
                f.write(data)

        distribution = installer / 'Distribution.xml'

        tools.cmd('productbuild', '--package', pkgpath,
                  '--resources', rsrcdir,
                  '--identifier', f'{self._root_pkg.identifier}-{major}',
                  '--version', version,
                  '--synthesize', distribution)

        dist_xml = minidom.parse(str(distribution))
        gui_xml = dist_xml.documentElement

        for name in resources:
            res_type = pathlib.Path(name).stem.lower()
            if res_type in ('welcome', 'readme', 'license', 'conclusion',
                            'background'):
                mimetype = mimetypes.guess_type(name)
                element = dist_xml.createElement(res_type)
                element.setAttribute('file', name)
                if mimetype[0] is not None:
                    element.setAttribute('mime-type', mimetype[0])
                if res_type == 'background':
                    element.setAttribute('alignment', 'left')

                gui_xml.appendChild(element)

        options = gui_xml.getElementsByTagName('options')
        if options:
            options = options[0]
        else:
            options = dist_xml.createElement('options')
            gui_xml.appendChild(options)

        options.setAttribute('customize', 'never')
        options.setAttribute('rootVolumeOnly', 'true')

        with open(distribution, 'w') as f:
            f.write(dist_xml.toprettyxml())

        self._outputroot.mkdir(parents=True, exist_ok=True)
        tools.cmd('productbuild',
                  '--package-path', pkgpath.parent,
                  '--resources', rsrcdir,
                  '--identifier', f'{self._root_pkg.identifier}-{major}',
                  '--version', version,
                  '--distribution', distribution,
                  self._outputroot / f'{title}-{version}.pkg')
