"""Report what a long-running workflow is doing, so the CLI can show it.

Workflows call ``report`` at each step. Nothing is shown unless the caller has
set a callback with ``reporting_to``, so tests and other callers are unaffected.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

ProgressCallback = Callable[[str], None]

_callback: ContextVar[ProgressCallback | None] = ContextVar("progress_callback", default=None)


def report(step: str) -> None:
    callback = _callback.get()
    if callback is not None:
        callback(step)


@contextmanager
def reporting_to(callback: ProgressCallback) -> Iterator[None]:
    token = _callback.set(callback)
    try:
        yield
    finally:
        _callback.reset(token)
