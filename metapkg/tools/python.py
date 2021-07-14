import glob
import json
import pathlib
import subprocess
import sys
import tempfile
import textwrap

import distlib.database
import distlib.version

from metapkg import tools


def get_dist(path):

    with tempfile.TemporaryDirectory() as d:
        tools.cmd(
            sys.executable, "setup.py", "dist_info", "--egg-base", d, cwd=path
        )

        distinfos = glob.glob(str(pathlib.Path(d) / "*.dist-info"))

        if not distinfos:
            raise RuntimeError(
                f"{path.name}/setup.py dist_info did not produce "
                f"any distinfos"
            )
        elif len(distinfos) > 1:
            raise RuntimeError(
                f"{path.name}/setup.py dist_info produced "
                f"too many distinfos"
            )

        distinfo = distinfos[0]
        dist = distlib.database.InstalledDistribution(distinfo)

        return dist


SCRIPT = textwrap.dedent(
    """
    import contextlib
    import json
    import pathlib
    import sys

    setup_py = pathlib.Path(sys.argv[1])
    setup_args = {}

    def _patched_setup(**kwargs):
        if not setup_args:
            setup_args.update(kwargs)

    with open(setup_py, 'r') as f:
        source = f.read()

    setup_py_dir = str(setup_py.parent.resolve())

    try:
        import setuptools
        orig_setuptools_setup = setuptools.setup

        import distutils.core
        orig_distutils_setup = distutils.core.setup

        setuptools.setup = _patched_setup
        distutils.core.setup = _patched_setup
        sys.path.append(setup_py_dir)

        with contextlib.redirect_stdout(sys.stderr):
            exec(source)
    finally:
        setuptools.setup = orig_setuptools_setup
        distutils.core.setup = orig_distutils_setup

    print(json.dumps(setup_args.get('setup_requires', [])))
"""
)


def get_build_requires(setup_py):
    scriptfile = tempfile.NamedTemporaryFile(
        "w+t", delete=False, dir=str(setup_py.parent)
    )
    try:
        scriptfile.write(SCRIPT)
        scriptfile.close()
        process = subprocess.run(
            [sys.executable, scriptfile.name, str(setup_py)],
            check=True,
            stdout=subprocess.PIPE,
            universal_newlines=True,
            cwd=str(setup_py.parent),
        )
        return json.loads(process.stdout)
    finally:
        pathlib.Path(scriptfile.name).unlink()
