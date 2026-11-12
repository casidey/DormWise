from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


@register.filter
def peso(value):
    """1655 -> ₱1,655.00 and -200 -> -₱200.00"""
    amount = _decimal(value)
    if amount is None:
        return ""
    sign = "-" if amount < 0 else ""
    return f"{sign}₱{abs(amount):,.2f}"


@register.filter
def peso0(value):
    """Whole pesos for tight spots: 188.89 -> ₱189"""
    amount = _decimal(value)
    if amount is None:
        return ""
    sign = "-" if amount < 0 else ""
    return f"{sign}₱{abs(amount):,.0f}"


@register.filter
def plain(value):
    """A Decimal for data attributes and inputs: no symbol, no commas."""
    amount = _decimal(value)
    return "" if amount is None else f"{amount:.2f}"
