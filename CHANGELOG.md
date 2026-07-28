# Changelog

All notable changes to CerePulse are documented here.

## [0.1.0] - 2026-07-28

### CI/CD
- Add CI workflow, changelog config, and commit message linting

### Chores
- Scaffold uv project with lint, type-check, and test tooling
- Normalize line endings with gitattributes
- Explore single-form icon marks, with a monochrome test
- Explore variations on the clock-and-pulse mark
- Explore a six-o'clock dial with the name in the mark
- Six-o'clock clock marks in two colours

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

