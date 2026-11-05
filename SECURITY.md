# Security policy

Occupella PMS holds other people's money records: owners' trust balances, tenants' deposits,
bank account numbers and tax IDs. A security report gets our attention ahead of anything else.

## Reporting a vulnerability

Report it privately through GitHub: on this repository's **Security** tab, choose **Report a
vulnerability**. Please don't open a public issue, pull request or discussion about it.

Say what you found, which version or commit, and how to see it happen. Use synthetic data only,
as the repository does (`tools/check_synthetic_data.py` lists safe forms): never a real person's,
company's or bank's details, and never test against a server you don't run yourself.

What happens next:

- We confirm we have it within 3 business days.
- We say whether we can reproduce it, and how serious we judge it, within 10 business days.
- We fix it in a new release, and say so in `CHANGELOG.md` with an advisory, crediting you if you
  want to be named. We ask that you keep it private until the fix is released, or 90 days after
  your report, whichever comes first.

There is no paid bug bounty.

## Supported versions

Fixes go into the next release tag (`docs/release.md`). Only the latest release is supported:
upgrade to it (`docs/install.md`, "Upgrading") before reporting, if you can.

## What is in scope

- This repository's code: the API (`src/pms/api`), the database schema and its functions
  (`db/migrations`), the portals (`frontend/`), the command-line tools and the self-host files
  (`Dockerfile`, `compose.selfhost.yaml`).
- Anything that lets someone read or change another company's records, act as another person,
  move money or post to the ledger outside its write paths, or read what the AI's role
  (`trust_ai_agent`) must never read.

Out of scope: a server you don't run, denial of service by flooding, findings that need a
person's own compromised device, and the fake test partners (`src/pms/deployment.py`), which a
server reached over https refuses to run.

## What the project does already

Pull requests run CodeQL, dependency review and gitleaks. Every day the dependencies are audited
(pip-audit, npm audit), the self-host image is scanned with Grype, and OWASP ZAP runs against
the running PMS (`docs/CI.md`). Release images are signed with Sigstore (`docs/release.md`).
