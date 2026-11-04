"""Load one explicitly selected counterfactual price book without changing baseline prices."""

import os
import stat

from .metrics import Pricing


class _CapturedBook:
    """Reuse the existing pricing validator on one bounded file read."""

    def __init__(self, text):
        """Retain one bounded UTF-8 source for validation."""
        self.text = text

    def read_text(self):
        """Supply captured text to the existing price validator."""
        return self.text


def read_scenario(path):
    """Accept a selected regular price file up to one MiB and 1000 rates."""
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("Select a regular counterfactual price book")
        raw = source.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Counterfactual price book exceeds one MiB")
    try:
        pricing = Pricing(_CapturedBook(raw.decode("utf-8")))
    except UnicodeDecodeError:
        raise ValueError("Counterfactual price book must be UTF-8 JSON") from None
    if len(pricing.rates) > 1000:
        raise ValueError("Counterfactual price book exceeds 1000 rates")
    return pricing
