#!/usr/bin/env python3
"""
Daily Opportunity Digest
-------------------------
Reads sources.json (the 351-institution list), runs a keyword search per
source via Google News RSS (no API key needed), keeps track of what's
already been seen (seen.json) so nothing repeats day to day, and emails a
single, plain-language digest of everything new to the client every day.

Filters applied to every item before it's included:
  - Country/region: Africa + French-speaking African countries only.
  - Date: only opportunities published in the last MAX_ITEM_AGE_DAYS days
    (default 60).
  - Keyword relevance: every tier needs a broad opportunity-shaped match;
    Tier 3/4 (noisier, diffuse sources) need a stricter, unambiguous match
    on top of that.

Run manually:   python daily_scan.py
Run in CI:       see .github/workflows/daily-scan.yml
"""

import json
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import feedparser

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES_PATH = os.path.join(HERE, "sources.json")
SEEN_PATH = os.path.join(HERE, "seen.json")

# ---------------------------------------------------------------------------
# Tier behaviour: the whole script runs once a week (see the GitHub Actions
# schedule), scanning Tiers 1-4 every run. Whether a hit needs to match a
# strong keyword to be worth emailing still varies by tier below.
# ---------------------------------------------------------------------------
TIER_LABEL = {
    1: "Primary Band",
    2: "Secondary Band",
    3: "Diffuse Band",
    4: "Early Signal Band",
    5: "Background Band",
}
# Tier 1/2 sources are curated and trusted: surface anything reasonably
# opportunity-shaped. Tier 3/4 are noisy/broad: only surface strong,
# unambiguous matches. Tier 5 is excluded from email alerts entirely.
REQUIRE_STRONG_MATCH_TIERS = {3, 4}
EXCLUDE_FROM_EMAIL_TIERS = {5}

# Broad, catches most genuine opportunity mentions. Required for EVERY tier
# now, so a Tier 1/2 source can't slip an unrelated press release into the
# digest just because it came from a trusted institution.
OPPORTUNITY_KEYWORDS = [
    "call for tender", "call for tenders", "invitation to bid",
    "request for proposal", "request for proposals", "expression of interest",
    "call for consultant", "call for consultants", "terms of reference",
    "notice of procurement", "consultancy", "technical assistance",
    "capacity building", "concept note",
    "avis d'appel d'offres", "appel d'offres", "appel à propositions",
    "appel a propositions", "manifestation d'intérêt", "manifestation d'interet",
    "demande de propositions", "avis de recrutement", "termes de référence",
    "termes de reference", "appel à candidatures", "assistance technique",
]

# Narrower, unambiguous set. Required (on top of the above) for Tier 3/4,
# since those sources are diffuse/noisy and need a stricter bar.
CORE_KEYWORDS = [
    "call for tender", "call for tenders", "invitation to bid",
    "request for proposal", "request for proposals", "expression of interest",
    "call for consultant", "call for consultants",
    "avis d'appel d'offres", "appel d'offres", "appel à propositions",
    "appel a propositions", "manifestation d'intérêt", "manifestation d'interet",
    "demande de propositions", "avis de recrutement", "appel à candidatures",
]

# --- Country/region filter -------------------------------------------------
# Client asked to focus specifically on Africa and French-speaking African
# countries. Sources whose own "geo" tag already names an African country or
# region are trusted automatically (that IS their mandate). Sources tagged
# with something broader (a donor's home country, "International", "Global",
# etc.) only pass through if the actual opportunity text mentions Africa or
# a specific African country.
AFRICAN_COUNTRIES_EN = [
    "algeria", "angola", "benin", "botswana", "burkina faso", "burundi",
    "cabo verde", "cape verde", "cameroon", "central african republic", "chad",
    "comoros", "congo", "democratic republic of the congo", "drc", "djibouti",
    "egypt", "equatorial guinea", "eritrea", "eswatini", "ethiopia", "gabon",
    "gambia", "ghana", "guinea", "guinea-bissau", "ivory coast", "cote d'ivoire",
    "côte d'ivoire", "kenya", "lesotho", "liberia", "libya", "madagascar",
    "malawi", "mali", "mauritania", "mauritius", "morocco", "mozambique",
    "namibia", "niger", "nigeria", "rwanda", "sao tome", "senegal", "seychelles",
    "sierra leone", "somalia", "south africa", "south sudan", "sudan",
    "tanzania", "togo", "tunisia", "uganda", "zambia", "zimbabwe",
    "africa", "african", "sub-saharan", "west africa", "central africa",
    "east africa", "north africa", "southern africa",
    "uemoa", "waemu", "cemac", "ecowas", "sadc", "african union",
]
FRANCOPHONE_AFRICAN_TERMS_FR = [
    "afrique", "afrique de l'ouest", "afrique centrale", "sénégal", "senegal",
    "bénin", "benin", "togo", "côte d'ivoire", "cote d'ivoire", "cameroun",
    "gabon", "rdc", "congo", "tchad", "niger", "mali", "burkina faso",
    "guinée", "guinee", "madagascar", "maroc", "tunisie", "algérie", "algerie",
    "rwanda", "burundi", "djibouti", "comores", "mauritanie",
    "uemoa", "cemac", "cedeao", "union africaine",
]
RELEVANT_TERMS = AFRICAN_COUNTRIES_EN + FRANCOPHONE_AFRICAN_TERMS_FR

# Sources whose own geo tag already names Africa/a specific African country —
# built at runtime from each source's "geo" field, so this stays in sync with
# sources.json automatically. A source only needs the *item text* checked if
# its own geo tag doesn't already confirm African relevance.


def source_is_africa_anchored(source):
    geo = (source.get("geo") or "").lower()
    return any(term in geo for term in RELEVANT_TERMS)


def is_africa_relevant(source, text):
    if source_is_africa_anchored(source):
        return True
    text_l = text.lower()
    return any(term in text_l for term in RELEVANT_TERMS)


# --- Date filter ------------------------------------------------------------
# Only include opportunities published within the last N days, so nothing
# stale keeps resurfacing.
MAX_ITEM_AGE_DAYS = int(os.environ.get("MAX_ITEM_AGE_DAYS", "60"))


def item_within_date_window(entry, now):
    parsed = entry.get("published_parsed")
    if not parsed:
        # No parseable date — keep it rather than silently drop a possibly
        # genuine, recent item just because the feed didn't supply one.
        return True
    published_dt = datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)
    age_days = (now - published_dt).days
    return age_days <= MAX_ITEM_AGE_DAYS


SEARCH_QUERY_EN = '("call for proposals" OR "expression of interest" OR "request for proposals" OR "terms of reference" OR "call for tenders" OR consultancy)'
SEARCH_QUERY_FR = '("appel à propositions" OR "manifestation d\u2019intérêt" OR "appel d\u2019offres" OR "termes de référence" OR consultant)'

REQUEST_DELAY_SECONDS = float(os.environ.get("SCAN_DELAY_SECONDS", "0.4"))
LOOKBACK_DAYS_FOR_FIRST_RUN = 5  # don't flood the first email with the whole backlog


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def domain_of(url):
    try:
        return urllib.parse.urlparse(url).hostname.replace("www.", "")
    except Exception:
        return ""


def build_feed_url(source, lang="en"):
    query = SEARCH_QUERY_EN if lang == "en" else SEARCH_QUERY_FR
    dom = domain_of(source.get("website", ""))
    scope = f"site:{dom} " if dom else f'"{source["name"]}" '
    q = scope + query
    hl = "en-US" if lang == "en" else "fr"
    gl = "US" if lang == "en" else "FR"
    ceid = f"{gl}:{hl.split('-')[0]}"
    return f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl={hl}&gl={gl}&ceid={ceid}"


def matches_keywords(text, keywords):
    text_l = text.lower()
    return any(kw in text_l for kw in keywords)


def scan_source(source, seen_ids, now):
    """Return list of new items (dicts) found for this source, across EN+FR."""
    found = []
    for lang in ("en", "fr"):
        url = build_feed_url(source, lang)
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"  ! fetch error for {source['name']} ({lang}): {e}", file=sys.stderr)
            continue
        for entry in feed.entries[:12]:
            item_id = entry.get("id") or entry.get("link")
            if not item_id or item_id in seen_ids:
                continue
            if not item_within_date_window(entry, now):
                continue
            title = entry.get("title", "").strip()
            link = entry.get("link", "")
            published = entry.get("published", "")
            found.append({
                "id": item_id,
                "title": title,
                "link": link,
                "published": published,
                "lang": lang,
            })
        time.sleep(REQUEST_DELAY_SECONDS)
    return found


def run_scan():
    sources = load_json(SOURCES_PATH, [])
    seen = load_json(SEEN_PATH, {})  # {item_id: iso_seen_timestamp}
    seen_ids = set(seen.keys())
    first_run = len(seen_ids) == 0

    now = datetime.now(timezone.utc)
    results_by_tier = {t: [] for t in range(1, 6)}
    scanned_count = 0

    for source in sources:
        tier = int(source.get("tier", 5))
        if tier in EXCLUDE_FROM_EMAIL_TIERS:
            continue

        scanned_count += 1
        hits = scan_source(source, seen_ids, now)

        for hit in hits:
            seen[hit["id"]] = now.isoformat()
            seen_ids.add(hit["id"])

            # Country/region relevance: Africa + French-speaking African
            # countries only.
            if not is_africa_relevant(source, hit["title"]):
                continue

            # Baseline: every tier must match at least a broad opportunity
            # keyword, so trusted sources can't slip unrelated news through.
            if not matches_keywords(hit["title"], OPPORTUNITY_KEYWORDS):
                continue

            # Tier 3/4 (diffuse/early-signal, noisier) need the stricter,
            # unambiguous keyword set on top of the baseline above.
            if tier in REQUIRE_STRONG_MATCH_TIERS and not matches_keywords(hit["title"], CORE_KEYWORDS):
                continue

            results_by_tier[tier].append({
                **hit,
                "source_name": source["name"],
                "source_category": source.get("category", ""),
                "source_geo": source.get("geo", ""),
            })

    save_json(SEEN_PATH, seen)

    print(f"Scanned {scanned_count} sources.")
    for t in range(1, 6):
        print(f"  Tier {t}: {len(results_by_tier[t])} new item(s)")

    if first_run:
        print("First run: state file was empty, so this run only seeded the "
              "'seen' list. No email will be sent to avoid flooding the inbox "
              "with the entire backlog. Tomorrow's run will alert on genuinely new items.")
        return results_by_tier, True

    return results_by_tier, False


def build_email_html(results_by_tier, now):
    total = sum(len(v) for v in results_by_tier.values())

    # Group into two plain, non-technical sections instead of exposing
    # tier numbers or internal band names.
    high_priority = results_by_tier.get(1, []) + results_by_tier.get(2, [])
    worth_a_look = results_by_tier.get(3, []) + results_by_tier.get(4, [])

    def render_group(items):
        rows = ""
        for it in items:
            rows += f"""
            <tr>
              <td style="padding:10px 12px;border-bottom:1px solid #e5e0d0;">
                <a href="{it['link']}" style="color:#8a6d1a;text-decoration:none;font-weight:600;font-size:14px;">{it['title']}</a><br>
                <span style="font-size:12px;color:#6b6449;">{it['source_name']} &middot; {it['source_geo']}</span>
              </td>
            </tr>"""
        return f"""<table style="width:100%;border-collapse:collapse;background:#F2ECDD;border-radius:4px;">{rows}</table>"""

    sections = []
    if high_priority:
        sections.append(f"""
        <h3 style="font-family:Georgia,serif;color:#1b2a40;margin:22px 0 8px;">High priority ({len(high_priority)})</h3>
        {render_group(high_priority)}""")
    if worth_a_look:
        sections.append(f"""
        <h3 style="font-family:Georgia,serif;color:#1b2a40;margin:22px 0 8px;">Worth a look ({len(worth_a_look)})</h3>
        {render_group(worth_a_look)}""")

    if not sections:
        body = """<p style="color:#444;">No new opportunities today. All caught up.</p>"""
    else:
        body = "".join(sections)

    return f"""
    <div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:0 auto;">
      <div style="background:#0F1B2B;color:#E9E4D6;padding:20px 24px;border-radius:6px 6px 0 0;">
        <div style="font-family:Georgia,serif;font-size:20px;font-weight:600;">Today's Opportunities</div>
        <div style="font-size:12px;color:#AEB8C9;margin-top:4px;">{now.strftime('%A, %d %B %Y')}</div>
      </div>
      <div style="padding:20px 24px;border:1px solid #e5e0d0;border-top:none;border-radius:0 0 6px 6px;">
        <div style="font-family:Georgia,serif;font-size:32px;font-weight:700;color:#1b2a40;">{total}</div>
        <div style="font-size:12px;color:#777;margin-bottom:6px;">total opportunities found today</div>
        {body}
      </div>
    </div>
    """


def send_email(html_body, subject):
    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    mail_from = os.environ.get("ALERT_EMAIL_FROM", smtp_user)
    mail_to = [addr.strip() for addr in os.environ["ALERT_EMAIL_TO"].split(",") if addr.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = ", ".join(mail_to)
    msg.attach(MIMEText(html_body, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
        server.login(smtp_user, smtp_pass)
        server.sendmail(mail_from, mail_to, msg.as_string())
    print(f"Email sent to {mail_to}")


def main():
    now = datetime.now(timezone.utc)
    results_by_tier, is_first_run = run_scan()
    total = sum(len(v) for v in results_by_tier.values())

    send_if_empty = os.environ.get("SEND_EMAIL_IF_NO_RESULTS", "false").lower() == "true"
    if is_first_run:
        return
    if total == 0 and not send_if_empty:
        print("No new items and SEND_EMAIL_IF_NO_RESULTS is false — skipping email.")
        return

    subject = f"Today's Opportunities: {total} new" if total else "Today's Opportunities: nothing new today"
    html = build_email_html(results_by_tier, now)

    # Save a copy of the digest so it can be reviewed on GitHub directly,
    # without depending on the email actually arriving/being found.
    digests_dir = os.path.join(HERE, "digests")
    os.makedirs(digests_dir, exist_ok=True)
    with open(os.path.join(digests_dir, "latest.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(digests_dir, f"{now.strftime('%Y-%m-%d')}.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved a copy of today's digest to digests/latest.html and digests/{now.strftime('%Y-%m-%d')}.html")

    if os.environ.get("DRY_RUN", "false").lower() == "true":
        print("DRY_RUN is true — not sending email. Preview:\n")
        print(html)
        return

    send_email(html, subject)


if __name__ == "__main__":
    main()
