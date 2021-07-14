from cleo.application import Application as BaseApplication

import metapkg

from . import commands as metapkg_commands


class App(BaseApplication):
    def __init__(self) -> None:
        super().__init__(metapkg.__name__, metapkg.__version__)


def main():
    app = App()
    for cmd_name in metapkg_commands.__all__:
        cmd = getattr(metapkg_commands, cmd_name)
        app.add(cmd())

    return app.run()
