from django import template

register = template.Library()


@register.filter(name="replace")
def replace_filter(value: str, arg: str) -> str:
    """
    Replace all occurrences of the first character/string with the second.
    Usage: {{ value|replace:"_: " }}  →  replaces "_" with " "
    The arg is split on the first colon; both sides may be empty strings.
    """
    if ":" not in arg:
        return value
    old, new = arg.split(":", 1)
    return str(value).replace(old, new)
