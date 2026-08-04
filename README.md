# CerePulse

**Knows when you can leave, before you ask.**

A smart attendance and leave companion for Windows. It shows your full day breakdown —
worked, break, expected out, overtime, early exit — plus what your history says about your
habits, which days need a swipe request, and the cheapest leave to book for the longest
break, without opening the HR portal in a browser.

CerePulse is the desktop successor to [NineToFive](https://github.com/viru185/ninetofive).
NineToFive did the math but you had to feed it: sign in, open attendance, copy the punch
log, paste it in. CerePulse removes every one of those steps — the data is already there
when you open the window, and the tray tells you when you can leave without opening anything
at all.

> **Read-only by design.** CerePulse reads from SpineHR and never writes to it. When it
> detects that a swipe request is needed it tells you and opens the right portal page — it
> does not file anything on your behalf.

---

## What it does

**Answers the question first** — Today opens with when you can leave, then what that means:
how much break you can still take without moving it, whether the target is met, whether a
punch is missing. The numbers come after the sentence that interprets them.

**Reads your history** — typical start and finish per weekday, real median lunch length,
whether you have been drifting later lately, on-target streaks, longest day, earliest start,
and how this month compares with the last six. Every figure carries the sample behind it.

**Says where the month lands** — a projection to month end from your recent pace, what each
remaining day has to be to finish level, and whether a short day is affordable this week.

**Shows what needs doing** — a swipe column per day (filed, pending, approved, rejected), a
needs-attention filter, and a month heatmap tinted by hours worked. A day already covered by
a request is handled, not outstanding.

**Plans your leave** — the cheapest days to book for the longest break, using the company
holiday calendar and the balance you actually hold, plus the published holiday list with the
ones you have already had marked off.

**Tells you when you get home** — takes the leave time it already predicted, adds a
traffic-aware drive to an address you set, and says when the front door opens. Needs a free
TomTom key of your own; see [below](#the-journey-home).

**Talks like a person** — a light remark when you have earned one, and never when you have
not. Warnings, expiring leave and missing punches stay plain at every setting.

**Works offline** — attendance history is cached locally, so past records and every trend
above are readable with no connection.

**Explains itself** — every metric on the Today screen expands to show the exact punches and
arithmetic behind it, including any punch that was inferred or discarded, and why. Estimated
days are always counted and named rather than blended into a figure that looks exact.

---

## The journey home

Today can add one more line: **when you would actually get home**, using the leave time it
already predicts plus a traffic-aware estimate of the drive.

That needs a maps provider, and **CerePulse asks you for your own key rather than shipping
one.** Not to make life difficult — a key shipped inside the app would be a key published
with it. The releases are public downloads and a packaged Python app is a zip of bytecode, so
pulling a string out of it takes about a minute, and TomTom's terms require keys stay
confidential in any case. Obfuscating it would change how long that takes, not whether it
happens, and the first person to scrape it would be spending your allowance.

Getting one takes about three minutes and costs nothing:

1. Register at [developer.tomtom.com](https://developer.tomtom.com/) — free, no card.
2. Create a project or app. The free tier gives **20,000 requests a month** with live
   traffic, which is roughly a thousand times what this uses.
3. Copy the key it issues.
4. In CerePulse, open **Settings → Journey home**, paste it, and press **Check key**.

Then set your home address and press **Find this address**. The app shows you what it
matched, because an address that quietly resolves to the next city still produces a
perfectly believable travel time.

**On usage.** The app asks once when you are within half an hour of leaving, and again only
if that prediction moves by more than a quarter of an hour — roughly one lookup per working
day, about 22 a month. Refresh is always available and always answers; if the last answer is
under a minute old it simply re-uses it rather than buying an identical one. There is a hard
daily ceiling it cannot exceed, adjustable in Settings.

Your key goes to the Windows Credential Manager, alongside the portal password, and never
into a config file. Estimates are held in memory only and are never written to the local
database, which keeps CerePulse inside TomTom's terms on caching results.

Without a key, everything else works exactly as before — the card simply says how to set it
up.

---

## Install

Grab the latest [release](https://github.com/viru185/CerePulse/releases):

- **`CerePulse-<version>-Setup.exe`** — per-user installer, no admin rights needed.
- **`CerePulse-<version>-portable.zip`** — unzip and run. Keeps all its data in a `Data`
  folder beside the executable, so it travels on a USB stick.

Both check for updates and tell you when one is available, then open the download.
CerePulse never installs anything by itself.

Windows 10 or 11, 64-bit. No Python needed — the runtime is bundled.

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
