import string


class Template(string.Template):
    delimiter = '@@'


def format_template(tpltext, **kwargs):
    template = Template(tpltext)
    return template.substitute(kwargs)
