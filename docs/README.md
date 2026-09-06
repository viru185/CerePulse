# Developing CerePulse

The codebase is about 18,000 lines across twelve packages. It is not complicated, but it is
large enough that changing something safely means knowing which of the twelve you are in and
what that layer is allowed to do.

**Start here if you want to change something.**

| I want to… | Read |
|---|---|
| Understand how a click becomes a number on screen | [architecture.md](architecture.md) |
| Add a field, a screen, a setting, a column, a parser | [recipes.md](recipes.md) |
| Run it, debug it, capture fixtures, find the logs | [running.md](running.md) |
| Cut a release, or understand updates and rollback | [releasing.md](releasing.md) |

## The one thing to read first

[`CLAUDE.md`](../CLAUDE.md) in the repository root holds two things this guide deliberately
does **not** repeat:

- **The protocol facts.** Twelve things about SpineHR that were reverse-engineered from HTTP
  captures, each of which silently breaks the app if forgotten. The password is encrypted
  client-side with a key scraped fresh per page load; `Tot. Hrs.` is `HH.MM` and not a
  decimal; a valid session is not enough to reach a page. None of it is documented by the
  vendor.
- **The load-bearing rules.** Roughly twenty invariants that each exist because breaking them
  cost something real — "saving a month must never discard punch detail", "parsers raise
  rather than returning empty", "today's punch log is never finished".

Those are the constraints. This guide is the map and the how-to.

Two copies of a rule is one copy that goes stale, so where this guide touches a rule it links
to `CLAUDE.md` rather than restating it.

## The shape of it

```
src/cerepulse/
  core/          597 lines   config, paths, logging, errors, secrets
  models/        593         frozen dataclasses — no behaviour, no storage
  transport/     551         httpx client, retries, ASP.NET WebForms state
  auth/          597         password crypto, session state machine, browser handover
  parsers/       820         HTML → models
  intelligence/  4,359       pure analysis: days, months, trends, insights, voice
  repository/    1,656       SQLite, migrations, one repo per entity
  services/      2,219       cache-first workflows, sync orchestration
  commute/       669         TomTom: routing, geocoding, pasted-pin parsing
  notify/        609         tray, toasts, notification policy, Windows startup
  update/        979         release checks, downloads, install, rollback
  ui/            4,844       PySide6 views and controllers — renders only
  app.py                     composition root: builds the whole graph
```

1,172 tests, ~35 seconds. The intelligence layer has the deepest coverage because it is pure
and it is where correctness actually lives.

## The four things that will bite you

1. **Never touch the network or SQLite on the GUI thread.** Everything goes through
   `ui/workers.TaskRunner`, whose pool is deliberately single-slot.
2. **The intelligence layer takes injected time.** No `datetime.now()` inside it, ever —
   that is what makes an in-progress day testable at eleven in the morning.
3. **The app is read-only against SpineHR.** It detects that a swipe request is needed and
   deep-links to the portal. It never files one.
4. **Nothing secret goes in a file.** Passwords and the TomTom key go to the Windows
   Credential Manager; the log sink scrubs cookies, `__VIEWSTATE`, `hEnSa` and `?key=`.
