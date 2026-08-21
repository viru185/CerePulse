# Releasing

## Version numbers

One source of truth: `__version__` in `src/cerepulse/__about__.py`. Hatchling reads it at
build time, and the About dialog, the updater and the InnoSetup build read it at runtime.

A tag containing a hyphen is a **pre-release**; anything else is stable. That single rule
drives the release workflow's channel decision, so `v0.15.0-beta.1` reaches beta users only
and `v0.15.0` reaches everyone.

## Cutting a beta

```bash
# 1. Everything green first.
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy

# 2. Bump.
#    __about__.py: __version__ = "0.15.0-beta.1"

# 3. Changelog, from the commits.
git-cliff --config cliff.toml --tag v0.15.0-beta.1 -o CHANGELOG.md

# 4. Commit, push, tag.
git commit -am "chore: bump to 0.15.0-beta.1"
git commit -am "chore: regenerate the changelog for 0.15.0-beta.1"
git push origin dev
git tag -a v0.15.0-beta.1 -m "..." && git push origin v0.15.0-beta.1
```

Conventional Commits, enforced by commitlint on PRs. The body explains *why*.

## Promoting to stable

Stable tags live on `main`.

```bash
# On dev: drop the pre-release suffix, regenerate, push.
# Then:
git checkout main && git merge --ff-only dev && git push origin main
git tag -a v0.15.0 -m "..." && git push origin v0.15.0
```

Confirm it published as a full release rather than a prerelease:

```bash
gh release view v0.15.0 --json isPrerelease,assets
```

## What CI does

`.github/workflows/release.yml`, on a tag:

1. Decides the channel from the tag (hyphen → prerelease).
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
