import distro
import platform

from .base import Build, Target  # noqa
from .package import SystemPackage  # noqa

from . import deb, rpm


def detect_target(io):
    system = platform.system()

    if system == 'Linux':
        distro_info = distro.info()
        like = distro_info['like']
        if not like:
            like = distro_info['id']

        like_set = set(like.split(' '))

        if like_set & {'rhel', 'fedora', 'centos'}:
            target = rpm.get_specific_target(distro_info)
        elif like_set & {'debian', 'ubuntu'}:
            target = deb.get_specific_target(distro_info)
        else:
            io.error(f'Linux distro not supported: {distro_info["id"]}')

    else:
        io.error(f'System not supported: {system}')

    return target
