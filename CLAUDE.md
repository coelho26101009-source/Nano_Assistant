# NANO: rules for every change

NANO is a desktop assistant that acts on a real Windows PC. Read `SECURITY.md`
and `CONTRIBUTING.md` before changing anything that touches tools, files,
permissions, the network or the phone link.

## Before anything reaches `main`

The 2026-11-25 total review found 35 bugs, almost all in code merged less than
a week earlier. Every new feature therefore goes through these steps before it
is merged, in this order:

1. **Tests that fail first.** Each fix or feature has a test that fails on the
   code before the change and passes after it.
2. **A second review pass of the whole diff**, read as a stranger would:
   correctness (wrong branch, lost state, races, timeouts), then security
   (paths, URLs, approvals, what a web page or the model could make it do).
   Use the `code-review` and `security-review` skills where available.
3. **The suites the change touches** (see `CONTRIBUTING.md` → Testing):
   Python `pytest -m "not chromium"`, `-m chromium` for UI, Electron
   `npm test`, frontend `tsc` + build, `mobile/` typecheck + tests, `relay/`
   tests. Re-run only what failed while fixing.
4. **Documentation in the same change:** the feature list in `README.md`,
   `CHANGELOG.md`, and a numbered item in `docs/TESTAR_NO_WINDOWS.md` (or
   `docs/TESTAR_NO_TELEMOVEL.md`) for anything that needs a real PC or phone.
5. Say plainly what was NOT tried on real Windows or a real phone.

## How work lands

The owner (Simão, pt-PT, not a GitHub power user) wants finished work
straight on `main` once the tests pass: no pull requests left waiting. A pull
request is only a vehicle for CI and is merged as soon as it is green. Never
force-push and never rewrite `main`'s history. The owner's Obsidian vault
(`Nano-Brain` repository) is updated in the same piece of work.
