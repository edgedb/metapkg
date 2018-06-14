import logging

import cleo

from poetry.console.commands import command as poetry_command
from poetry.console.styles import poetry as poetry_style


class Command(cleo.Command):

    _loggers = []

    def run(self, i, o) -> int:
        self.input = i
        self.output = poetry_style.PoetryStyle(i, o)

        for logger in self._loggers:
            self.register_logger(logging.getLogger(logger))

        return super().run(i, o)

    def register_logger(self, logger):
        handler = poetry_command.CommandHandler(self)
        handler.setFormatter(poetry_command.CommandFormatter())
        logger.handlers = [handler]
        logger.propagate = False

        output = self.output
        level = logging.WARNING
        if output.is_debug():
            level = logging.DEBUG
        elif output.is_very_verbose() or output.is_verbose():
            level = logging.INFO

        logger.setLevel(level)
