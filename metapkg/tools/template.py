from typing import Any

import string


class Template(string.Template):
    delimiter = "@@"


def format_template(tpltext: str, **kwargs: Any) -> str:
    template = Template(tpltext)
    return template.substitute(kwargs)
