# CerePulse

**Knows when you can leave, before you ask.**

A smart attendance and leave companion for Windows. It shows your full day breakdown —
worked, break, expected out, overtime, early exit — plus leave balances, expiry warnings and
offline history, without opening the HR portal in a browser.

CerePulse is the desktop successor to [NineToFive](https://github.com/viru185/ninetofive).
NineToFive did the math but you had to feed it: sign in, open attendance, copy the punch
log, paste it in. CerePulse removes every one of those steps — the data is already there
when you open the window, and the tray tells you when you can leave without opening anything
at all.

> **Status:** in active development, pre-first-release.

---

## What it does

**Attendance intelligence** — for any day: in time, out time, expected out, break taken,
break remaining, total worked, hours remaining, extra hours, and early-exit detection with a
swipe-request prompt (plus the status of a request you already filed).

**Leave intelligence** — planned, comp-off and carry-forward balances, expiry countdowns,
and upcoming approved leave.

**Smart, not just visual** — it tells you what the numbers mean and what to do next: short
of hours, overtime earned, swipe request needed, unusual patterns, and a monthly hours bank
with the daily average you need to finish the month even.

**Works offline** — full attendance history is cached locally, so past records are readable
with no connection.

**Explains itself** — every metric on the Today screen expands to show the exact punches and
arithmetic behind it, including any punch that was inferred or discarded, and why.

---

## Install

Grab the latest [release](https://github.com/viru185/CerePulse/releases):

- **`CerePulse-Setup-<version>.exe`** — per-user installer, no admin rights needed.
  Auto-updates itself.
- **`CerePulse-Portable-<version>.zip`** — unzip and run. Keeps all its data in a `Data`
  folder beside the executable, so it travels on a USB stick. Notifies about updates but
  does not self-install.

---

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```bash
uv sync --all-extras
```

Run the checks the CI runs:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q
```

Launch the app:

```bash
uv run cerepulse
```

Show where the app keeps its data:

```bash
uv run cerepulse paths
```

### Configuration

Settings live in `%LOCALAPPDATA%\CerePulse\config\cerepulse.toml` (or `Data\config\` in a
portable build). Every value has a safe default, so the file is optional.

Any setting can be overridden with an environment variable using
`CEREPULSE__<SECTION>__<KEY>`:

```bash
CEREPULSE__LOGGING__LEVEL=DEBUG
```

**Your password is never stored in this file.** It goes to the Windows Credential Manager
via `keyring`, and only if you tick "Remember me". Logs are passed through a redaction
filter that strips passwords, session cookies and page state before anything is written.

---

## Contributing

Commits follow [Conventional Commits](https://www.conventionalcommits.org/) and are enforced
by commitlint on every pull request:

```
feat: add monthly hours bank to the attendance view
fix: handle a day with a single unmatched punch
docs: explain the portable data directory
```

`CHANGELOG.md` and release notes are generated from those commits with
[git-cliff](https://git-cliff.org/), so a clear commit message becomes a clear release note.

---

## Credits

Built by **[Viren Hirpara](https://github.com/viru185)** —
[LinkedIn](https://www.linkedin.com/in/hirparaviren/).

Standing on two earlier projects: [NineToFive](https://github.com/viru185/ninetofive) for
the day-breakdown engine, and [ReportFlow](https://github.com/viru185/ReportFlow) for the
packaging and release tooling.

## License

MIT
