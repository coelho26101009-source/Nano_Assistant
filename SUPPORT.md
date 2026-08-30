# Support

Nano is in active development and has **not had a public release**. There is no
installer, no released build and no support contract. What follows is where to
put each kind of message so it reaches the right place.

## Choose the right channel

| You want to… | Go here |
| --- | --- |
| Report something broken | [Bug report issue](../../issues/new?template=bug_report.yml) |
| Suggest a capability or change | [Feature request issue](../../issues/new?template=feature_request.yml) |
| Report a **security vulnerability** | **Security tab → Report a vulnerability** — never a public issue |
| Ask a question or get help | [Discussions](../../discussions), if enabled; otherwise a bug report is fine |
| Contribute code | [CONTRIBUTING.md](CONTRIBUTING.md) |

## Security comes first

**If it is exploitable, do not open a public issue.** A public issue is visible
to everyone the moment you file it, including before there is a fix. Use
GitHub's private vulnerability reporting on the **Security** tab. Full
instructions are in [SECURITY.md](SECURITY.md).

If you are unsure whether something is a security issue, treat it as one. A
private report that turns out to be an ordinary bug costs nothing; a public
report of a real vulnerability cannot be taken back.

## Before you file a bug

Most reports are resolved faster with a little of this:

* Check whether an [existing issue](../../issues) already covers it.
* Note what you expected and what happened instead.
* Say which mode you were in — **AUTO**, **CLOUD** or **LOCAL** — since Nano
  routes to a different provider in each and many behaviours differ.
* Include your Windows version and how you started Nano (`NANO_DESKTOP.bat` or
  `NANO.bat`).

## What never to include

Nano touches your machine, your microphone and your files, so a report can leak
far more than you intend. Please do **not** paste:

* API keys, tokens, or the contents of `.env`
* raw log files (`logs/nano.log` records your activity — read it and quote only
  the relevant lines)
* clipboard contents
* screenshots that show your desktop, your documents, your messages, or anything
  you would not publish
* personal files, or paths that reveal private information

A short description of what happened is more useful than a capture of your
machine. If a maintainer needs more, they will ask for something specific.

## What to expect

This is a small project maintained in spare time. There is no response-time
commitment, because one that cannot be kept is worse than none. Issues are read,
and reproducible reports are the ones that get fixed.

## Documentation

* [README.md](README.md) — what Nano is and how to run it
* [PRIVACY.md](PRIVACY.md) — what is stored, and what leaves your computer
* [SECURITY.md](SECURITY.md) — the security model and how to report a flaw
* [docs/architecture/](docs/architecture/) — how the system fits together
* [docs/SECURITY_POLICY.md](docs/SECURITY_POLICY.md) — the permission and
  policy model in detail
