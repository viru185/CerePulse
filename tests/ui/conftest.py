"""UI test fixtures.

Qt runs offscreen so these tests need no display and work unchanged in CI. Both the
platform setting and the ``qapp`` fixture live in the root conftest, because the tray tests
outside this package need them too.
"""

from __future__ import annotations
