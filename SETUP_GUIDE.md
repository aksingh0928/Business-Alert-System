# Opportunity Radar — Daily Scan &amp; Email Alerts: Setup Guide

This turns the source list into a fully automated process: every morning, a
script checks the sources that are due, and emails the client a digest of
anything new and worth their attention. It runs by itself, for free, using
GitHub Actions (a free "run this on a schedule" service). No server to
rent or maintain.

Total setup time: about 15–20 minutes, one time only.

---

## What you're setting up

- **Tier 1 & 2** sources: scanned every day.
- **Tier 3 & 4** sources: scanned every Monday, only strong keyword matches are emailed (keeps noise down).
- **Tier 5**: excluded from email alerts — stays in the Opportunity Radar board for monthly manual review.
- The first run only "seeds" its memory of what already exists online, so it doesn't dump years of backlog into the client's inbox. From the second run onward, only genuinely new items are emailed.

---

## Step 1 — Create a free GitHub account and repository

1. Go to [github.com](https://github.com) and sign up (free) if you don't already have an account.
2. Click **New repository**. Name it something like `opportunity-radar-automation`. Set it to **Private**. Click **Create repository**.

## Step 2 — Upload these files

You've been given a folder containing:
```
daily_scan.py
sources.json
seen.json
.github/workflows/daily-scan.yml
```

On the repository page, click **Add file → Upload files**, then drag the
whole folder in (most browsers preserve the subfolders, including
`.github/workflows/`). Commit the files to the `main` branch.

> If your browser drops the `.github` folder during upload: create the file
> manually instead. Click **Add file → Create new file**, type
> `.github/workflows/daily-scan.yml` as the filename (GitHub creates the
> folders automatically), and paste in the workflow file's contents.

## Step 3 — Create an email account to send from

The simplest option is a Gmail address (a dedicated one for this tool, or
your own).

1. In your Google Account, go to **Security → 2-Step Verification** and turn it on if it isn't already.
2. Still under Security, search for **App passwords**. Create one (name it "Opportunity Radar"). Google gives you a 16-character password — copy it, you'll only see it once.

(If you'd rather not use Gmail, any SMTP-capable provider works — Outlook,
Zoho, Resend, SendGrid, etc. You just need the host, port, username and
password.)

## Step 4 — Add your credentials as repository secrets

In your repository: **Settings → Secrets and variables → Actions → New repository secret**. Add each of these:

| Secret name | Value |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` (for Gmail) |
| `SMTP_PORT` | `465` |
| `SMTP_USER` | your sending email address |
| `SMTP_PASS` | the app password from Step 3 |
| `ALERT_EMAIL_FROM` | same as `SMTP_USER` (or leave this one out) |
| `ALERT_EMAIL_TO` | the client's email address (comma-separate for more than one) |

Nothing here is visible to anyone browsing the repo — secrets are encrypted and only used inside the automation.

## Step 5 — Turn it on and test it

1. Go to the **Actions** tab of your repository. If prompted, click **I understand my workflows, go ahead and enable them**.
2. Click **Daily Opportunity Scan** in the left list, then **Run workflow → Run workflow**. This triggers it immediately instead of waiting for the schedule.
3. Watch the run (takes 1–3 minutes). Green check = it worked. Click into it to read the log — it prints how many sources were scanned and how many new items were found per tier.
4. First run: no email is sent (by design — it's just building its memory of what already exists). Run it a second time (or just wait for tomorrow) to see a real digest.

From here it runs on its own every day at 07:00 UTC. To change the time,
edit the `cron:` line in `.github/workflows/daily-scan.yml` — the comment
above it explains how to adjust for your timezone.

---

## Adjusting things later

- **Add or remove a source**: edit `sources.json` directly on GitHub (small pencil icon), or regenerate it from an updated spreadsheet.
- **Change who gets the email**: update the `ALERT_EMAIL_TO` secret.
- **Change what counts as "strong" enough to alert on for Tier 3/4**: edit the `STRONG_KEYWORDS` list near the top of `daily_scan.py`.
- **Pause it**: Actions tab → the workflow → the "···" menu → Disable workflow.

## What this does *not* do

- It doesn't touch the Opportunity Radar web board — the two currently run independently: this emails a digest, the board is where you and the client browse, filter and log opportunities by hand. Keeping them in sync (so an emailed item shows up already logged on the board) is a further step — worth doing once this basic version is running reliably, since it needs a small shared database instead of email as the only channel.
- It relies on Google News indexing the source's content. Extremely obscure or very slow-to-index pages may lag by a day or two — that's inherent to using a free, no-API-key search method rather than paying for a dedicated indexing service.
