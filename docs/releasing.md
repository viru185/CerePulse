# Releasing

## Version numbers

One source of truth: `__version__` in `src/cerepulse/__about__.py`. Hatchling reads it at
build time, and the About dialog, the updater and the InnoSetup build read it at runtime.

From 0.17 every version is a **plain number**, bumped by what changed since the last tag:

| Commits since the last tag | Bump | Example |
|---|---|---|
| only `fix:` (and chores, docs, tests) | patch | 0.17.0 → 0.17.1 |
| any `feat:` | minor | 0.17.1 → 0.18.0 |
| any breaking change (`feat!:`, `BREAKING CHANGE:`) | major | 0.18.0 → 1.0.0 |

`[bump]` in `cliff.toml` encodes that table — including below 1.0, where semver would
otherwise let a feature bump only the patch — and `tools/bump.py` applies it. The
`-beta.N` suffixes of 0.15 and 0.16 are retired.

**Every release is published as a beta** (a GitHub pre-release), whatever its number. A
release becomes stable only when it has been tested and is promoted — see below. The updater
has always decided by GitHub's pre-release flag, not by the version text, so beta users see
both channels and stable users see only promoted releases.

## Cutting a release

```powershell
# 1. Everything green first.
uv run pytest -q; uv run ruff check .; uv run ruff format --check .; uv run mypy

# 2. Work out the number and write it, with the changelog.
uv run python tools/bump.py            # says what it would be
uv run python tools/bump.py --write    # writes __about__.py and CHANGELOG.md

# 3. Commit, push dev, tag.
git commit -am "chore: bump to 0.18.0"
git push origin dev
git tag -a v0.18.0 -m "CerePulse 0.18.0"; git push origin v0.18.0
```

Development happens on a branch and is merged into `dev` before each release; the release is
cut from `dev`. Conventional Commits, enforced by commitlint on PRs. The body explains *why*.

## Promoting to stable

Only once the release has been used and checked — "fully tested, QC'd and bug free" is the
bar. Promotion does not rebuild or renumber anything: the exact build beta users have been
running is what stable users get.

```powershell
git checkout main; git merge --ff-only v0.18.0; git push origin main
gh release edit v0.18.0 --prerelease=false --latest
gh release view v0.18.0 --json isPrerelease,isLatest
```

A tag cannot be reused, so a fix found during testing is a new patch release (0.18.1), tested
in turn, and that is the one promoted.

## What CI does

`.github/workflows/release.yml`, on a tag:

1. Publishes every tag as a pre-release (beta); promotion is a separate, manual step.
2. Builds the app folder, the portable zip and the InnoSetup installer.
3. Writes `SHA256SUMS.txt`.
4. Publishes the release with all three attached.

Assets are named **version last** — `CerePulse-Setup-0.15.0.exe` — so a folder of releases
sorts readably. If you change that naming, the globs in `release.yml` must move with it: they
matched `*-Setup.exe` once, which would silently publish a release with no assets at all.

## How updating works

- `update/checker.py` asks GitHub for releases and picks the installer asset by
  `name.endswith(".exe") and "setup" in name` — deliberately loose, so a rename does not
  strand installed builds.
- `update/downloader.py` streams it to a `.part` file and only renames it once complete
  **and** checksum-verified. Nothing partial is ever runnable, and a published checksum that
  does not match fails closed: this is a file about to be executed.
- `update/installer.py` launches it with `CREATE_NO_WINDOW` — not `DETACHED_PROCESS`, which
  gives the child no console and hangs the console-based wait loop forever.

## Rollback

Staged installers live in `updates/`. On startup, `clear_spent_installers` deletes what the
running build has superseded — **except the newest one below it**, which is kept precisely so
`rollback_candidates` has something to offer. Anything newer is a pending update and stays.

That exception is the whole feature. A cleanup that deleted everything at or below the
running version shipped in 0.14 and silently disabled rollback for two releases, because the
previous build is by definition at a lower version.

Both installer namings are readable, so a build can still see the installer it was upgraded
*from*.

## The schema

`SCHEMA_VERSION` in `repository/schema.py`, forward-only, one numbered migration per step.
An older build meeting a newer database refuses to run rather than corrupting it.

A migration that fails on a corrupt page quarantines the cache and starts fresh rather than
preventing the app from launching — every byte in there is re-fetchable, and refusing to
start is the worse outcome.
