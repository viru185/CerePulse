# Architecture

## Dependency flow, and why

```
ui → services → {repository, intelligence, transport} → core
```

One way, always. The rule is worth stating as what it *buys*, because that is what makes it
worth keeping:

- **`intelligence/` imports nothing but `models/` and `core/`.** No network, no database, no
  Qt. That is why 4,359 lines of the trickiest logic in the app are tested in a second with
  no fixtures, no mocks and no display.
- **`ui/` renders and nothing else.** A view receives an object and draws it. When a screen
  starts *deciding* something, that decision belongs one layer down — which is how the
  Records timeline ended up as a pure function in `intelligence/records.py` rather than a
  loop inside a widget.
- **`models/` are frozen dataclasses with no storage knowledge.** They do not know SQLite
  exists. `repository/mappers.py` is the only place that converts between a model and a row.

`commute/` sits *beside* `transport/` rather than inside it. `transport/` is the SpineHR
WebForms client — cookies, `__VIEWSTATE`, menu privilege tokens — and none of that applies to
a JSON maps API. Nothing in attendance depends on the commute, so a maps outage costs one
card on Today; `CommuteError` lives outside the transport/protocol hierarchy for exactly that
reason.

## What each layer is for

| Layer | Owns | Never |
|---|---|---|
| `core/` | Config, paths, logging, errors, the keyring | Knows about attendance |
| `models/` | The shape of the data | Persists itself |
| `transport/` | HTTP, retries, WebForms state | Knows what a page *means* |
| `auth/` | Crypto, the session state machine, browser handover | Fetches business data |
| `parsers/` | HTML → models | Fetches anything |
| `intelligence/` | Every judgement the app makes | Touches I/O or the clock |
| `repository/` | SQLite, migrations, queries | Decides what to fetch |
| `services/` | Cache-first workflows, orchestration | Draws |
| `ui/` | Widgets and wiring | Decides, fetches, or blocks |

## How one click becomes a number

Pressing **Refresh** on Today:

1. `TodayView.refresh_requested` — a signal. The view knows nothing else.
2. `MainWindow.refresh()` — decides what a click *means*: if paused, take the session back
   first. Every Refresh control routes through here for that reason.
3. `SyncController.refresh()` — submits to `TaskRunner`. This is the thread boundary; nothing
   past it may touch a widget.
4. `SyncCoordinator.sync_all()` — orchestrates the scopes and owns the **replay-once** rule:
   an expired session is re-authenticated and the operation retried exactly once. A second
   expiry is surfaced rather than retried, because a loop here is an authentication storm
   against the employer's HR system.
5. `PortalGateway` — resolves the page **by menu label**, never by URL, then fetches.
6. `parsers/` — HTML → `AttendanceDay` objects, raising rather than returning empty.
7. `AttendanceRepository.save_month()` — writes, preserving punch detail the grid does not
   carry.
8. `analyze_month` / `analyze_day` — pure. Given the days, produce the figures.
9. `SyncController.month_ready` — a signal back on the GUI thread.
10. `MainWindow._apply_month()` — hands the result to each view to draw.

Every arrow in that chain is a layer boundary, and each one is where a whole class of bug was
found and fixed. Step 4 is where a lost session recovers. Step 6 is where a vendor UI change
becomes a visible error instead of a blank screen. Step 7 is where a routine refresh stops
erasing the most expensive data in the cache.

## The threading model

One rule: **the GUI thread never blocks.**

`TaskRunner` (`ui/workers.py`) has a **single-slot** pool, and that is correctness rather than
caution: the portal is a stateful WebForms session where every request carries `__VIEWSTATE`,
so two concurrent postbacks invalidate each other.

The consequence catches people out: the pool **serialises** rather than drops. Ten clicks
become ten sequential calls, each answering a question the previous one already answered. Any
control that can be pressed repeatedly needs its own guard — see the commute service's
in-flight lock and freshness floor for the pattern.

## Where state lives

| State | Home |
|---|---|
| Everything fetched | SQLite, `%LOCALAPPDATA%\CerePulse\cache` |
| Settings | `config/cerepulse.toml` |
| Passwords, the TomTom key | Windows Credential Manager, via `keyring` |
| Update history, seen releases | `state.json` |
| Staged installers | `updates/` |
| Commute estimates | Memory only — TomTom's terms restrict caching them |

The database is the single source of truth: **screens render the cache; a sync writes the
cache.** Where a fetch covers a whole entity it *replaces* that entity's rows, which is only
safe because a partial fetch aborts before any save is attempted.
