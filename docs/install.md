# Installing the PMS for your company

This runs the PMS on a server of your own, for your company's real books. It takes about an hour
the first time. You bring the server, a web address and your keys (below); the PMS is free
(Apache-2.0) and sends nothing to us.

Just want to look around first? `docker compose -f compose.demo.yaml up --build` starts a demo
company with synthetic books on your own computer (README, "Try the demo").

## What you need

- **A Linux server** you control, with [Docker](https://docs.docker.com/engine/install/) and its
  compose plugin, and `git`. A small one is enough to start; the PMS was measured serving a
  1,000-unit company to ten staff at once (README, "Simulate people using it").
- **A web address** (say `pms.example.com`) pointing at the server, so staff, owners and
  tenants can reach it over HTTPS.
- **Your keys**, from the table below.

## Keys you bring

Every key goes in a file named `.env` next to `compose.selfhost.yaml` (step 2). Only the first
three are needed to start; add the others when you turn those parts on.

| Key | What it does | Where you get it | Needed? |
|---|---|---|---|
| `PMS_DB_PASSWORD` | The database's password | Make one: `openssl rand -hex 24` | Yes |
| `PMS_PUBLIC_URL` | The address people open the PMS at, like `https://pms.example.com` | Your web address | Yes |
| `PMS_DATA_KEY` | Seals owners' and vendors' bank account numbers and tax IDs | Make one: `openssl rand -base64 32`. **Keep a copy safe and never change it**: what it sealed can't be read without it | Yes, for ACH payout files and tax IDs |
| `PMS_EMAIL_SENDER=smtp`, `PMS_SMTP_HOST`, `PMS_SMTP_PORT`, `PMS_SMTP_USERNAME`, `PMS_SMTP_PASSWORD`, `PMS_EMAIL_FROM` | Sends invites, password resets, receipts, notices and owners' statements | Your email provider's SMTP settings (any provider that offers SMTP with STARTTLS) | Strongly recommended. Without it, emails wait in the PMS and you hand invite links over yourself |
| `PMS_PAYMENTS=stripe`, `PMS_STRIPE_SECRET_KEY`, `PMS_STRIPE_WEBHOOK_SECRET` | Tenants pay rent online by bank, with autopay; owners pay into their accounts | Easier: leave these out and enter your Stripe keys in Company setup (below). These are the fallback for a company that enters none | Optional |
| `PMS_GEOCODER=census` | Puts each property on a map from its address | Nothing: the U.S. Census Bureau's geocoder is free and needs no key | Optional |

Your bank's ACH details (its routing number and the IDs it gives your company, for payout files)
are not keys: you enter them in the PMS, on each trust bank account's Payouts tab.

`.env` holds secrets. It is never committed (git ignores it). Keep a copy in a password manager,
and above all `PMS_DATA_KEY`.

## 1. Get the code

```sh
git clone https://github.com/brandonoscar/Occupella-PMS.git
cd Occupella-PMS
git checkout v2026.11.0
```

Always run a tagged release (`git tag` lists them; CHANGELOG.md says what each changed), never
`main`.

## 2. Fill in your keys

```sh
cp .env.example .env
openssl rand -hex 24       # paste as PMS_DB_PASSWORD
openssl rand -base64 32    # paste as PMS_DATA_KEY
```

Open `.env` and fill in `PMS_DB_PASSWORD`, `PMS_PUBLIC_URL` and `PMS_DATA_KEY`, and email and
payments if you have them now. Each line in `.env.example` says what it is for.

## 3. Start it

```sh
docker compose -f compose.selfhost.yaml up -d --build
docker compose -f compose.selfhost.yaml ps        # db and app "healthy", migrate "exited (0)"
curl http://127.0.0.1:8000/health                 # {"status":"ok"}
```

The first build takes several minutes. Each start applies any database changes a release brings
before the app starts. The database's data lives in the Docker volume `pms-db`; `down` keeps it,
`down -v` deletes it.

## 4. Put HTTPS in front

The app listens on `127.0.0.1:8000` only. Put a web server with a certificate in front of it.
With [Caddy](https://caddyserver.com/docs/install), which gets the certificate by itself, the
whole configuration (`/etc/caddy/Caddyfile`) is:

```
pms.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Any proxy works (nginx, your host's load balancer) as long as it serves HTTPS and passes requests
to `127.0.0.1:8000`. Sign-in needs HTTPS: the session cookie is never sent over plain HTTP.

## 5. Make your company and its first admin

```sh
docker compose -f compose.selfhost.yaml exec app \
  python -m pms.admin setup "Your Company LLC" "Your Name" you@example.com
```

It prints your company's id, your staff id and an invite link. Open the link: you set a password
and add an authenticator app (any app that shows six-digit codes), which every staff sign-in
asks for. The link lasts 7 days; `python -m pms.admin invite` makes another.

## 6. Set up the books

In the PMS, as that admin:

1. **Settings, Company setup:** your name, mailing address and logo, and the late and NSF fee
   terms new properties start with.
2. **Banking, Add bank account:** your operating trust account and your security deposit trust
   account (each kind of money stays in its own).
3. **Settings, Staff:** invite your team from their rows and give them roles (admin, approve,
   disburse), and name each trust account's broker and signers.
4. **Your books:** bring them over from your old system with **Settings, Import** (CSV files,
   opening balances, the checks and deposits not yet cleared, a tie-out you sign off), or add
   properties, owners and leases by hand from Properties.
5. **Turn on what you use** in Company setup: online payments, the automatic emails, the monthly
   management fees.

## Payments online

An admin enters your company's own Stripe keys in Settings, Company setup, Stripe keys: the
secret key (Developers, API keys; a restricted key works too) and the signing secret of a webhook
you add in Stripe (Developers, Webhooks) at the address the card shows,
`https://pms.example.com/api/payments/webhook/stripe/<your company>`. The PMS seals both under
`PMS_DATA_KEY` (so set that first) and only ever shows the secret key's last four. Choose these
events for the webhook:

`checkout.session.completed`, `checkout.session.async_payment_succeeded`,
`checkout.session.async_payment_failed`, `checkout.session.expired`, `payment_intent.succeeded`,
`payment_intent.payment_failed`, `setup_intent.succeeded`, `charge.dispute.created`.

Or, for a server that runs one company, put the keys in `.env` instead (`PMS_PAYMENTS=stripe`,
`PMS_STRIPE_SECRET_KEY`, `PMS_STRIPE_WEBHOOK_SECRET`) with the webhook at
`https://pms.example.com/api/payments/webhook/stripe`; keys entered in Company setup take over
from them. A tenant's autopay bank account stays with the Stripe account it was saved at: after
changing accounts, tenants set autopay up again.

Then turn it on in Company setup, Online payments. Payments are by bank (ACH) for now. They were
tested against answers in the shape Stripe documents, not a live Stripe account: try one small
payment of your own first (NOT VERIFIED).

## Every day: backups

The install backs itself up. Its `backup` service (`scripts/backup-daily.sh`) dumps the whole
database when it starts, and then every day at 07:00 UTC, after the nightly run has posted the
day's rent and fees, into the `backups` folder next to `compose.selfhost.yaml`:

```sh
sudo ls -l backups/                                      # pms-2026-11-03.dump, one a day
docker compose -f compose.selfhost.yaml logs backup      # each dump, and any that failed
```

It keeps every daily dump for 35 days and each month's first for good, and a dump that fails is
tried again an hour later. The files are readable by root only: each holds everything the PMS
keeps. `PMS_BACKUP_AT` and `PMS_BACKUP_DIR` in `.env` change the time (UTC) and the folder.

These protect against a broken database or a mistake, not against losing the server. **Copy
each day's dump off the server**, encrypted, to storage another provider keeps, and **test a
restore** now and then: [docs/backup.md](backup.md) has both, and how to get the books back.

Also point an uptime check at `https://pms.example.com/health`; [docs/support.md](support.md)
says what to look at when something fails.

## Upgrading

Read [CHANGELOG.md](../CHANGELOG.md), back up, then:

```sh
git fetch --tags
git checkout v2026.12.0           # the new release's tag
docker compose -f compose.selfhost.yaml up -d --build
```

The database changes run before the new app starts. A release is never rolled back once money is
posted on it: a fix is the next release ([docs/release.md](release.md)).

## Not in this release

- **Texts**, **listing syndication**, **application fees** and **card payments** (rent online is
  by bank): coming in later releases.
- **Tenant screening**: no screening company is connected yet; screen applicants with your own
  service and record your decision on the application.
- **Occupella AI**: the PMS runs fully without it. Turning on AI from inside the PMS comes in a
  later release.
- **State rules**: the PMS warns where it knows a rule (docs/law.md), but your company answers for
  the rules it follows; no attorney has reviewed them.
