from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Mapping


@contextmanager
def temporary_env(overrides: Mapping[str, object] | None) -> Iterator[None]:
    """Temporarily apply process env overrides and restore exact previous state."""
    if not overrides:
        yield
        return
    previous: dict[str, str | None] = {}
    try:
        for key, value in overrides.items():
            key_text = str(key)
            previous[key_text] = os.environ.get(key_text)
            os.environ[key_text] = str(value)
        yield
    finally:
        for key, prior in previous.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior
