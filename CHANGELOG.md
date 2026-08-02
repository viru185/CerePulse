# Changelog

All notable changes to CerePulse are documented here.

## [0.10.0-beta.1] - 2026-08-02

### Bug Fixes
- Arm the session truce, and stop the portal hiding three quarters of every request list

## [0.9.0-beta.1] - 2026-08-02

### Bug Fixes
- Make a length of time look like one, and draw the arrows
- Stop losing outdoor duty, and stop owing August its whole month

### Chores
- 0.9.0-beta.1

### Features
- Draw a break as a quantity, and give the day a journey
- Give the session up rather than fight the browser for it
- Make the week worth opening, and merge leave and requests into records

## [0.8.0-beta.1] - 2026-08-02

### Features
- Group insights, assert leave rules, and stop rewriting what has not changed

## [0.6.0-beta.1] - 2026-08-01

### Bug Fixes
- Ask for a day's punches against its own month

## [0.5.0-beta.2] - 2026-08-01

### Bug Fixes
- Give the install helper a console, or it waits forever

## [0.5.0-beta.1] - 2026-08-01

### Chores
- 0.5.0-beta.1

### Features
- Lead with what to do, and stop calling lunch an early exit

## [0.4.0] - 2026-08-01

### Chores
- 0.4.0

### Documentation
- Regenerate changelog for 0.4.0

## [0.4.0-beta.1] - 2026-08-01

### Bug Fixes
- Package a version that carries a pre-release label

### Chores
- 0.4.0-beta.1

### Features
- Download in the background, install on request, roll back if wrong

## [0.3.0] - 2026-08-01

### Bug Fixes
- Make notifications actually arrive, and say so when they cannot

### Documentation
- Regenerate changelog for 0.3.0

### Features
- Make the explanatory text readable, and the tables consistent
- Allow only one copy to run
- Sync one thing at a time, and one day at a time
- Say what is loading, and stop swallowing what failed

### Refactor
- Split the window into controllers

### Tests
- Use synthetic fixtures, never captured ones

## [0.2.0] - 2026-08-01

### Bug Fixes
- Repair month selection, sign-in persistence, and day accounting
- Stop charging today for hours it does not owe yet
- Two things six months of real data made obvious
- Stop reporting "nothing logged" for a day with hours

### Documentation
- Regenerate changelog for 0.2.0

### Features
- Fetch past months and make them reachable
- Lead with what the day means, not what it measures
- Give the app a voice that cannot lie
- Show what the history says, and how much history there is
- Swipe status per day, a needs-attention filter, and a heatmap
- Plan the cheapest breaks, and export the calendar

### Tests
- Use synthetic fixtures, never captured ones

## [0.1.1] - 2026-07-28

### Bug Fixes
- Survive having no console in a windowed build

### Documentation
- Regenerate changelog for 0.1.1

## [0.1.0] - 2026-07-28

### Bug Fixes
- Stop the installer disabling start-with-Windows on update

### CI/CD
- Add CI workflow, changelog config, and commit message linting

### Chores
- Scaffold uv project with lint, type-check, and test tooling
- Normalize line endings with gitattributes
- Explore single-form icon marks, with a monochrome test
- Explore variations on the clock-and-pulse mark
- Explore a six-o'clock dial with the name in the mark
- Six-o'clock clock marks in two colours

### Documentation
- Add CLAUDE.md, release workflow, and changelog

### Features
- Add configuration, paths, logging, and credential storage
- Add HTTP client and ASP.NET WebForms state handling
- Authenticate using the portal's client-side password scheme
- Resolve portal pages through the navigation menu
- Add capture command for generating parser fixtures
- Support clicking submit inputs, not just link buttons
- Populate the leave register before capturing it
- Add attendance, leave, and swipe domain models
- Parse attendance, punch, leave, holiday, and swipe grids
- Analyze a working day from its punch log
- Add week and month rollups, anomalies, and leave expiry
- Add SQLite cache, migrations, and offline history
- Add cache-first workflows and session-expiry recovery
- Add composition root and headless sync command
- Add theme, background task runner, and shared widgets
- Add the application shell and all seven screens
- Add app icon, system tray, and desktop notifications
- Add Windows build, update check, and What's New
- Ship the negative-space mark as the app icon

### Refactor
- Rebuild Settings as a two-column card grid

### Tests
- Make the test suite a package

