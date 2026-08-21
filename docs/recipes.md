# Recipes

Step-by-step for the changes actually made to this codebase. Each one names the files in the
order you will touch them.

---

## Add a setting

1. **`core/config/models.py`** — add the field to the right section dataclass, with a default
   that is safe when absent. Every field needs one: a malformed config degrades to defaults
   rather than refusing to start.
2. If it is a **new section**, add the dataclass, add it to `AppConfig`, and register it in
   `_SECTION_TYPES` — the loader resolves sections by name, not by annotation.
3. **`ui/views/settings.py`** — add the control, read it in `_load`, write it in `_save`.
4. Whoever consumes it reads through `self._config` **at call time**, never snapshotted at
   construction. That is the whole reason Settings applies without a restart.

Secrets do **not** go here. Use `core/secrets.py` (`store_secret` / `get_secret`), which
writes to the Windows Credential Manager.

## Add a column to the cache

1. **`repository/schema.py`** — append a `_migration_00N`, add it to `MIGRATIONS`, bump
   `SCHEMA_VERSION`. Never edit a migration that has shipped.
2. Use `connection.execute` per statement, **not `executescript`**. `executescript`
   implicitly commits, so the transaction wrapper in `migrate()` cannot protect it — a
   failure part-way leaves half the migration committed and the next launch dies on
   "table already exists". Migration 006 learned this the hard way.
3. **`repository/mappers.py`** — the row ↔ model conversion.
4. **`models/`** — the field on the frozen dataclass.
5. A migration that rebuilds a table must be **retryable**: drop any scratch table first.

## Add a parser for a new portal page

1. **Capture the real page first.** `uv run cerepulse capture --out Research/captures`.
   Never write a parser against a guess at the HTML — see fact 7 in `CLAUDE.md` for what
   guessing at a column's meaning nearly cost.
2. **`transport/pages.py`** — the path, and the menu label it is reached by. Pages are
   resolved **by menu label**, never by URL: a direct URL returns a privileges error.
3. **`parsers/yourpage.py`** — read column indices from the capture, not from the vendor's
   documentation, which describes none of them.
4. **Raise `ParserError` rather than returning empty.** A page that is not the page you
   asked for is a fault; a real page with an empty grid is a fact. Find the marker that
   tells them apart — usually a filter or control the portal renders whether or not there
   are rows.
5. **`tests/parsers/`** — a synthetic fixture in `tests/fixtures/`. Real captures stay in
   gitignored `Research/`; they contain a live session and real attendance.

## Add a screen

1. **`ui/views/yourscreen.py`** — a `QWidget` that renders and emits signals. No fetching,
   no decisions.
2. **`ui/main_window.py`** — add to `SCREENS`, construct it in `_build_ui`, wire its signals
   in `_build_controllers`.
3. The data comes from a **service** returning a single view object (`MonthView`,
   `TrendsView`, `CommuteView`) — one object with everything the screen needs, so the screen
   never assembles anything.
4. Anything the screen *computes* belongs in `intelligence/` as a pure function.

## Add a notification

1. **`intelligence/insights.py`** — the `InsightKind`.
2. Produce the `Insight` wherever it is judged — `intelligence/day.py` for a day's
   arithmetic, `intelligence/nudges.py` for an observation about a habit.
3. **`notify/policy.py`** — add it to `TOGGLES`.
4. **`core/config/models.py`** — the toggle field on `NotificationConfig`.
5. **`ui/views/settings.py`** — the checkbox.
6. Check something actually *constructs* it. `SHORT_HOURS` sat wired into the policy for
   several versions with no code anywhere producing it — a notification that could never
   fire.

## Add a metric to Today

1. **`intelligence/day.py`** — the field on `DayAnalysis`, plus an `Explanation` so the card
   can answer "why this number?".
2. **`ui/views/today.py`** — the card.
3. Mind the grammar: Today's cards state what has been **done**, then the caption names the
   target and gives the verdict. Worked and Break were once framed in opposite directions,
   which made two numbers side by side impossible to read as a pair.

## Change how a day is measured

This is the most dangerous change in the codebase, because every screen and every trend
reads it.

1. **`intelligence/segments.py`** if it is about pairing punches; **`intelligence/day.py`** if
   it is about what the pairs mean.
2. Take injected time. Every entry point already takes `now` or `today`.
3. Anything the app **inferred** must be marked — `end_inferred`, `estimated`, `grid_only`,
   `adjusted_gaps`. Screens render marked figures differently and the voice engine refuses
   to be playful about them. A repaired number presented as a measured one is the failure
   this app works hardest to avoid.
4. Today is not a finished day. Check `CLAUDE.md`'s rule on it before assuming otherwise.

## Add a call to an external API

1. A **new top-level package**, like `commute/`. Do not put it in `transport/`.
2. Its own error type outside the transport hierarchy, so its outage cannot read as a portal
   or session problem.
3. A key is the **user's**, stored in the Credential Manager, and validated when pasted —
   with four outcomes, not two: valid, rejected, rate-limited, and *unreachable*. Never
   report an unreachable provider as a bad key.
4. Guard anything the user can press repeatedly: an in-flight lock, a freshness floor, a
   daily ceiling. The single-slot pool serialises rather than drops.
5. Keys travel in query strings, so add the parameter to the log scrubber in
   `core/logging_setup.py` and raise errors that do not carry the URL.
