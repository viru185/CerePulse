# Running and debugging

## Setup

```bash
uv sync --all-extras
```

Python **3.13** — not 3.14, because PySide6 has no 3.14 wheels yet.

## The commands

```bash
uv run pytest -q                                   # 1,216 tests, ~35s
uv run ruff check . && uv run ruff format --check .
uv run mypy                                        # strict, must stay clean
```

```bash
uv run cerepulse                                   # the GUI
uv run cerepulse sync                              # headless refresh; the end-to-end smoke test
uv run cerepulse paths                             # where config, cache and logs live
uv run cerepulse capture --out Research/captures    # re-dump portal HTML
```

```bash
uv run python tools/build_all.py                   # app folder + portable zip + installer
uv run python tools/build_icons.py                 # regenerate icons from assets/icon.svg
```

All four checks must pass before a commit. `uv run cerepulse sync` is the one that exercises
the real portal end to end.

## Where everything lives

`uv run cerepulse paths` prints it, but:

```
%LOCALAPPDATA%\CerePulse\
  cache/cerepulse.db      the SQLite cache (plus -wal, -shm)
  cache/glyphs/           themed arrow SVGs for the styled controls
  config/cerepulse.toml   settings
  logs/                   one file per day, scrubbed
  updates/                staged installers
  state.json              update history, releases already seen
```

Override the whole root with `CEREPULSE_DATA_DIR`, which is what tests and throwaway runs
use so they never touch your real data:

```bash
CEREPULSE_DATA_DIR=/tmp/cp uv run cerepulse sync
```

A `portable.marker` beside the executable moves the root to `<exe dir>/Data`, which is what
makes the portable zip self-contained.

## Debugging the UI without a screen

Qt tests run headless, and so can you:

```bash
QT_QPA_PLATFORM=offscreen uv run python -c "
from PySide6.QtWidgets import QApplication
app = QApplication([])
from cerepulse.app import build_app
from cerepulse.ui.main_window import MainWindow
ctx = build_app(); win = MainWindow(ctx)
print(win.attendance.current_period())
ctx.close()
"
```

This is how most of the UI bugs in this codebase were actually found. Two that only a
headless probe could have caught:

- **Counting widgets, not layout items.** `takeAt` removes a row from a layout but leaves it
  parented and painting until `deleteLater` is serviced. `layout.count()` said one; four were
  on screen. Count `findChildren`.
- **`findData` matches by identity.** A `QComboBox` storing a tuple as user data cannot be
  found with `findData`, because Qt compares the wrapped Python object by identity. A test
  using tuple *literals* passes anyway — CPython folds identical literals into one object.
  **Build your test data at runtime** or the test cannot see the bug.

## Reading the logs

One file per day in `logs/`, and the sink scrubs cookies, passwords, `__VIEWSTATE`, `hEnSa`
and `?key=` before anything reaches disk — so a log is safe to paste into an issue.

Useful greps:

```bash
grep -E "WARNING|ERROR" logs/cerepulse_$(date +%F).log
grep "Refreshing\|Session\|Applying database migration" logs/*.log
```

`logger.complete()` before asserting on a log file in a test: loguru's sink is `enqueue=True`,
so writes are asynchronous.

## Capturing fixtures

```bash
uv run cerepulse capture --out Research/captures
```

Needs `.secrets.toml` with dev credentials. Both `Research/` and `.secrets.toml` are
gitignored in full — captures contain a live session cookie and real attendance records.

Tests use **synthetic** fixtures in `tests/fixtures/`, hand-written to match a capture's
shape. When adding one, open the real capture beside it and copy the structure exactly,
including the columns that look pointless.

## Inspecting the cache safely

Read-only, so it cannot disturb a running app:

```bash
uv run python -c "
import os, sqlite3
db = os.path.join(os.environ['LOCALAPPDATA'], 'CerePulse', 'cache', 'cerepulse.db')
c = sqlite3.connect('file:%s?mode=ro' % db.replace(os.sep, '/'), uri=True)
print([r[0] for r in c.execute('select version from schema_version order by version')])
"
```

Copying the file to inspect it needs the `-wal` and `-shm` too, or you get a torn snapshot —
better to use `sqlite3`'s own `.backup()`. A cache that fails `PRAGMA integrity_check` is
quarantined on the next launch and a fresh one is built; every byte in it is re-fetchable, so
refusing to start would be the worse outcome.
