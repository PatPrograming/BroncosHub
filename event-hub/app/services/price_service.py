"""Parse event prices exactly, without rounding a submitted fraction of a cent."""

import re


PRICE = re.compile(r"(?:[0-9]{1,4}(?:\.[0-9]{0,2})?|\.[0-9]{1,2})\Z")


def parse_price_cents(value):
    """Accept ordinary dollar amounts from 0 through 9999, with at most 2 decimals."""
    if not isinstance(value, str) or not PRICE.fullmatch(value.strip()):
        raise ValueError("Enter a price from 0 to 9,999 with no more than two decimal places.")
    whole, separator, fraction = value.strip().partition(".")
    cents = int(whole or "0") * 100 + int(fraction.ljust(2, "0") or "0")
    if cents > 999900:
        raise ValueError("Enter a price from 0 to 9,999 with no more than two decimal places.")
    return cents
