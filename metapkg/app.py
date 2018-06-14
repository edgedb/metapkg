import cleo
import cleo.formatters
import cleo.inputs
import cleo.outputs

import metapkg

from . import commands as metapkg_commands


class App(cleo.Application):
    def __init__(self):
        super().__init__(metapkg.__name__, metapkg.__version__)
        self._formatter = cleo.formatters.Formatter(True)
        self._formatter.add_style('error', 'red', options=['bold'])
        self.set_catch_exceptions(False)

    def run(self, i=None, o=None) -> int:
        if i is None:
            i = cleo.inputs.ArgvInput()

        if o is None:
            o = cleo.outputs.ConsoleOutput()
            self._formatter.with_colors(o.is_decorated())
            o.set_formatter(self._formatter)

        return super().run(i, o)

    def get_default_commands(self) -> list:
        commands = super().get_default_commands()

        for cmd in metapkg_commands.__all__:
            commands.append(cmd())

        return commands


def main():
    return App().run()
