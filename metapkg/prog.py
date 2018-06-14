import os
import os.path
import sys


CACHEHOME = os.environ.get(
    'XDG_CACHE_HOME', os.path.expandvars('$HOME/.cache'))

CACHEDIR = os.path.join(CACHEHOME, 'metapkg')


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)
