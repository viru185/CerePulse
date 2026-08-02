from __future__ import annotations

from collections.abc import Iterator

import pytest
from loguru import logger


@pytest.fixture(autouse=True)
def contained_loguru_sinks() -> Iterator[None]:
    """Keep sinks installed by a test from outliving it.

    loguru's handler table is process-global, so ``configure_logging`` reaches well past the
    test that calls it: without this, a file sink pointing at one test's ``tmp_path`` stays
    installed for every test that runs afterwards, still holding the log file open long after
    pytest wants to delete the directory.

    Only handlers added during the test are removed, so a test that never touches logging is
    left alone. loguru exposes no public way to enumerate handler ids — ``add`` hands one back
    and ``remove`` takes one — hence the read of ``_core.handlers``.
    """
    before = set(logger._core.handlers)
    yield
    for handler_id in set(logger._core.handlers) - before:
        logger.remove(handler_id)
