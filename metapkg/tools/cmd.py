import logging
import subprocess
import sys


logger = logging.getLogger(__name__)


def cmd(*cmd, errors_are_fatal=True, hide_stderr=False, **kwargs):
    default_kwargs = {
        'stderr': subprocess.DEVNULL if hide_stderr else sys.stderr,
        'stdout': subprocess.PIPE,
        'universal_newlines': True,
    }

    default_kwargs.update(kwargs)

    try:
        p = subprocess.run(cmd, check=True, **default_kwargs)
    except subprocess.CalledProcessError as e:
        if errors_are_fatal:
            msg = '{} failed with exit code {}'.format(
                ' '.join(cmd), e.returncode)
            logger.error(msg)
            sys.exit(1)
        else:
            raise

    return p.stdout
