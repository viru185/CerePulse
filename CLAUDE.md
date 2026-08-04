# CerePulse — working notes for Claude Code

A Windows desktop client that reads attendance and leave from the company's SpineHR portal,
analyses the working day, and answers one question: **when can I leave?**

Everything below is either non-obvious or was learned the hard way. The obvious parts —
what a repository is, how pytest works — are left out on purpose.

## Commands

```bash
uv sync --all-extras          # install, including dev tools
uv run pytest -q              # 435 tests, ~3s
uv run ruff check . && uv run ruff format --check .
uv run mypy                   # strict, must stay clean
```

```bash
uv run cerepulse              # launch the GUI
uv run cerepulse sync         # headless refresh; also the end-to-end smoke test
uv run cerepulse paths        # where config, cache and logs live
uv run cerepulse capture --out Research/captures   # re-dump portal HTML fixtures
```

```bash
uv run python tools/build_icons.py     # regenerate icons from assets/icon.svg
uv run python tools/preview_icons.py   # compare icon candidates at 16/32px
uv run python tools/build_all.py       # app folder + portable zip + installer
```

## The protocol facts that matter

These were reverse-engineered from HTTP captures. Nothing in the vendor documentation says
any of it, and each one silently breaks the app if forgotten.

**1. The password is encrypted client-side.** The portal never receives plaintext. Its login
page runs CryptoJS before posting, and the client has to reproduce it exactly:

```
txtPassword = b64encode(AES-128-CBC(PKCS7(password), key=IV=hEnSa))
```

`hEnSa` is a 16-digit hidden field rendered fresh on every page load, so it must be scraped
per attempt and never cached. See `auth/crypto.py`.

**2. A valid session is not enough to reach a page.** Requesting `/Atten/MyAttendanceReport.aspx`
directly returns *"You do not have sufficient privileges (ROLE) to view this page"*. The real
menu links carry a privilege token (`?mnusr=menu__10101`), and some carry further opaque
server-side blobs. Pages are therefore resolved **by menu label**, never by URL —
`parsers/menu.py`, used through `PortalGateway`.

**3. `Tot. Hrs.` is HH.MM, not decimal.** `9.01` means nine hours and one minute. Confirmed
against live data: a 9:50 AM → 6:51 PM span renders as `9.01`. Reading it as a decimal
corrupts every downstream number. All parsing goes through `Duration.from_hhmm`; the rest of
the codebase works in whole minutes.

**4. Login success is a 302, not a 200.** A failed login returns 200 and re-renders the login
page, so status alone cannot distinguish them. The transport layer deliberately does not
follow redirects for this reason.

**5. Day detail costs one postback per day.** The monthly grid carries no punches. Fetching
a month's detail is ~20 async postbacks, which is why `backfill_detail` is paced and bounded.

**6. Every request list shows one status at a time.** The swipe list, and the leave,
outdoor-duty and comp-off lists, all render a single `cboReports` selection and open on one
that is frequently empty — In Process for swipes, Recent Applications for the rest. A plain
GET therefore describes a quarter of the data while looking like all of it. Worse, a decided
request *leaves* the pending grid rather than changing status inside it, so diffing two
fetches of the default view can never see an approval: the 0.6 "notify me when a request is
decided" feature could not fire, by construction. `_status_views` posts back once per state.

**7. `Approve Date` really is the approval date.** It looked like a lapse deadline — seven
still-pending July requests all carrying 31-Jul-26 — but a capture taken after the fact shows
all seven Approved on exactly that date. It is one approver clearing a month's backlog in a
sitting. The lesson is the general one: capture, then decide. Guessing the meaning from the
column name is how it nearly got renamed to something false.

**8. Leave, outdoor duty and comp-off are one page.** `OutdoorDutyList.aspx` serves outdoor
duty and comp-off under different `odtype` tokens, and `LeaveList.aspx` is the same grid plus
a `Leave Type` column. Two cells carry more than their heading says: `From Date` appends a
half-day marker to the weekday (`14-Jun-26 Sun2nd Half`) and `Apply Days` appends the type
(`5.50 OD`). Unlike the swipe grid these carry the portal's own `App. Id`, so identity does
not have to be synthesised.

**9. A swipe request cannot be filed, or even pre-filled, from a link.** "Add New" is
`__doPostBack('ctl00$BodyContentPlaceHolder$Menu1','Add New')` against the list page itself.
There is no entry URL in any capture or in `menu.json`, the Type / Category / Request Date
controls do not exist in the HTML until that postback renders them, and `__VIEWSTATEENCRYPTED`
is set. A browser opened with `open_url` cannot post a postback. The ceiling is: land on the
list with a real `?mnusr=` token and put the date on the clipboard as `14-Jul-26`.

**10. The day-detail postback target is a grid row, not a date.** `GridView1$ctl17$LnkDate`
means "the seventeenth row of whatever month is on screen". The attendance page opens on the
current month, so `fetch_day_detail` **must select the day's own month first** — otherwise a
June day requested in August posts June's row index against August's grid and comes back
with an empty punch log. Silently, because an empty log is a legitimate answer for a day
someone did not work. Five months of history cached zero punches this way before anyone
noticed, because every screen fell back to the grid-derived first-in/last-out pair and simply
looked like a day with one In and one Out.

## Architecture

Dependency flow is strictly one way:

```
ui → services → {repository, intelligence, transport} → core
```

| Layer | Responsibility |
|---|---|
| `core/` | config, paths, logging, errors, credential storage |
| `transport/` | httpx client, retries, ASP.NET WebForms state |
| `auth/` | password crypto, session state machine |
| `parsers/` | HTML → domain models |
| `models/` | frozen dataclasses, storage-agnostic |
| `intelligence/` | **pure** analysis — no network, no DB, no Qt |
| `repository/` | SQLite, migrations, per-entity repos |
| `services/` | cache-first workflows, sync orchestration |
| `notify/` | tray, notification policy, Windows startup |
| `ui/` | PySide6 views; renders only |
| `app.py` | composition root — builds the whole graph |

## Rules that are load-bearing

**Never touch the network or SQLite on the GUI thread.** Everything goes through
`ui/workers.TaskRunner`. Its pool is deliberately single-slot: the portal is a stateful
WebForms session where every request carries `__VIEWSTATE`, so concurrent postbacks would
invalidate each other. Serialising is correctness, not politeness.

**Saving a month must never discard punch detail.** The grid carries no punches, so a routine
refresh writes days with empty punch lists. Letting that overwrite stored punches would erase
the most expensive data in the cache on every sync. `save_month` preserves it and
`detail_loaded` is never downgraded. Tests pin both.

**Injected time, never `datetime.now()` inside the intelligence layer.** Every entry point
takes `now` or `today`, which is what makes in-progress days testable.

**Today is never "complete" while it still owes work.** A day whose last punch is an Out
means "at lunch" at one o'clock and "went home early" at seven, and the punches cannot tell
the two apart. Treating them the same made the app declare an early exit and demand a swipe
request in the middle of the working day. `analyze_day` therefore holds today at
`INCOMPLETE` while `work_remaining` is non-zero — and `clocked_in` is the separate flag for
"the last punch was an In", which is what `Presence` uses to tell working from on-break.
`find_attention` skips today for the same reason, so the two agree.

**Week and month totals compare completed days only.** Today's four hours against a full
eight-hour target reports a deficit that exists solely because it is lunchtime and shrinks
by itself as the afternoon passes. `WeekAnalysis.in_progress` carries today separately;
`progress` includes it, because "how far through the week am I" is a different question
from "am I behind".

**An empty punch log is "loaded", not "unfetched".** Those two states drive different
decisions and the sync backlog depends on telling them apart.

**Today's punch log is never finished.** `days_missing_detail` asked which days were
*missing* detail, and today needs asking whether its detail is *stale*: the 08:30 fetch
stores the arrival, one punch row satisfies "has detail", and today drops out of the backlog
for good — so lunch and the evening out were never read and every automatic path re-rendered
the morning. Today is always eligible while it is still today, and `load_day` treats a live
day as unsettled. `drain_detail` still terminates, because today stops being today.

**The leave ledger records credits, never consumption.** Every row this portal writes has
`consumed_days = 0.0`, for every leave type, across the whole year. Anything that needs to
know when leave was actually *taken* reads the muster — a day marked leave or half-day — not
the ledger. A feature built on `consumed_days` is silent forever and looks like a threshold
set too high.

**Replay expired sessions exactly once.** A retry loop turns a rejected credential into an
authentication storm against the employer's HR system. A second expiry is surfaced.

**Standing down has to have a way back.** The truce measures a theft by how long since the
app's own last request — but a paused app makes none, so that clock ends up timing the
user's trip to the browser, and coming back promptly read as a *second* eviction. The app
paused again and Refresh looked broken; leaving it longer than the idle timeout always
worked, which made it look random rather than wrong. `expect_reclaim` authorises exactly one
sign-in, spent by the next expiry. Every Refresh control must route through
`MainWindow.refresh`: the ones wired straight to a service could not end a pause, and one of
them sat on the screen the portal is opened from.

**A taken session is nobody's to swallow.** `SessionTakenError` subclasses
`SessionExpiredError` so existing handlers keep working, and that is exactly how the 0.9
truce shipped disarmed: `_step_value` recorded the eviction as a step failure, so it never
reached the UI and the app kept hammering a session the browser now owned. Every layer that
catches broadly — sync steps, the cache-on-failure guards in `services/leave.py`, the
per-scope `degraded` path — re-raises it. And an evicted session has a third shape beyond the
302 and the re-rendered login form: a landing page with no menu on it. `PortalGateway.menu`
reports that as a session error, not a `ParserError`, because a live session always gets a
menu.

**Parsers raise `ParserError` rather than returning empty.** A vendor UI change must surface
as a diagnostic, not as a silently blank screen. `parse_swipe_requests` broke this rule and
the cost was invisible: a failed fetch returned `[]`, `save_all([])` wrote nothing,
`mark_synced` blessed it, and the TTL then suppressed retrying — the screen showed "no
requests" over a portal holding nineteen. The distinction that makes it safe is the status
filter: a page with the filter and no grid is genuinely empty, a page with neither is not the
page we asked for.

**Trends report their own footing, and refuse to overstate.** `intelligence/trends.py` uses
medians throughout, so one 3 AM deployment night cannot become "your typical start"; it
carries the sample size behind every figure; it needs `MIN_SAMPLE` days before it will claim
a habit or a record at all; and the break figure comes only from punch logs, because the
grid has no break column to derive one from. Days the portal marks worked but holds nothing
for are dropped, not scored zero — the same reasoning as `unmeasured_days`.

**Today's cards share one grammar.** The value is what has been *done*; the caption names the
target and then the verdict — left, over, or met. Worked and Break used to be framed in
opposite directions, which made two figures side by side impossible to read as a pair, and
an overrun break reported the word "None" while never stating by how much.

**The browser handover must post at the top level.** Submitting the login form into a hidden
iframe lets the page survive and navigate onward, and silently stops signing anyone in: the
iframe is cross-origin, so the portal's cookies are third-party there and modern browsers
block or partition them. The login succeeds inside the frame and the auth cookie never
reaches the top-level context. This shipped in 0.11 and was caught by a user, not a test,
because it was verified with an HTTP client — which cannot reproduce the one behaviour that
matters. `tests/auth/test_handover.py` asserts the absence of the iframe for that reason.

**Voice appends, never substitutes.** `intelligence/voice.py` may only add a sentence to an
insight's detail. It cannot change a number, reword a warning, or drop a line, so no tone
setting can alter what the app actually reported. Warnings, expiring leave and anomalies get
no quip at any setting, and a day whose figures were repaired from a missing punch goes
entirely plain — congratulating someone on inferred overtime is worse than silence. Line
choice is seeded from the date via `crc32`, not `hash`: string hashing is salted per process,
so the built-in would reword the same day on every launch.

## Testing

Tests mirror the source tree. The intelligence layer has the deepest coverage because it is
pure and it is where correctness actually lives.

Fixtures in `tests/fixtures/` are **synthetic**. Real captures contain personal attendance
records and live session cookies, and stay in gitignored `Research/captures/`.

Qt tests run with `QT_QPA_PLATFORM=offscreen` (set in `tests/ui/conftest.py`) so they need no
display and work unchanged in CI.

## Security

`Research/` is gitignored in full: it holds live session cookies, a real credential, and the
vendor's binaries. `.secrets.toml` is gitignored and holds the dev credentials used by
`capture` and `sync`.

The logging sink scrubs cookies, passwords, `__VIEWSTATE` and `hEnSa` before anything reaches
disk. Passwords go to the Windows Credential Manager via `keyring`, never to a file.

The app is **read-only against SpineHR**. It detects that a swipe request is needed and deep
links to the portal; it never files one.

## Conventions

- Conventional Commits, enforced by commitlint on PRs. Explain *why* in the body.
- Python 3.13 — **not** 3.14; PySide6 has no 3.14 wheels yet.
- Icons are committed, not generated at build time: the installer needs a real `.ico` on disk
  before Python runs. Source is `assets/icon.svg`.
- Comments explain decisions and trade-offs, not mechanics.

## Known gaps

- **Comp-off expiry cannot be computed.** The portal's summary row is undated, so there is no
  earned date to count from. It reports `UNKNOWN` rather than inventing a deadline.
- **Leave-year end (31 Dec) and the 90-day comp-off window are defaults, not confirmed
  company policy.** Both are configurable in `LeavePolicy`.
- **Tray mode is read at startup.** Switching it in Settings saves but applies next launch.
- **Start-with-Windows refuses source runs**, since the registry entry would point into
  `.venv` and break on rebuild. It works from an installed build.
