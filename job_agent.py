#!/usr/bin/env python3
"""
Job alert agent.

Checks job feeds, skips jobs it has already seen, asks Claude to score each
new job against your profile, and sends the good ones to Telegram.

    python job_agent.py                 normal run
    python job_agent.py --check-feeds   test every feed, change nothing
    python job_agent.py --dry-run       print Telegram messages instead of sending
"""

import calendar
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests


# =====================================================================
# SETTINGS YOU CAN EDIT
# =====================================================================

# Job feeds to check: ("Name shown in Telegram", "Feed link")
RSS_FEEDS = [
    ("MyJobMag", "https://www.myjobmag.com/feeds/ng/jobsxml.xml"),
    ("MyJobMag", "https://www.myjobmag.com/feeds/ng/jobsxml_by_categories.xml"),
    ("HotNigerianJobs", "https://www.hotnigerianjobs.com/feed/rss.xml"),
    ("Jobzilla", "https://www.jobzilla.ng/feed"),
    ("We Work Remotely", "https://weworkremotely.com/remote-jobs.rss"),
]

# Company career pages hosted on Greenhouse: ("Company name", "board name").
# The board name is the last part of the link, e.g. boards.greenhouse.io/moniepoint
GREENHOUSE_BOARDS = [
    ("Moniepoint", "moniepoint"),
]

# Only send jobs that Claude scores this high or higher (1 to 10)
MIN_SCORE = 6

# Ignore jobs posted longer ago than this many hours
MAX_AGE_HOURS = 48

# The Claude model that scores the jobs
MODEL = "claude-haiku-4-5-20251001"

# How many jobs to send to Claude in one go
BATCH_SIZE = 30

# Skip any job whose title contains one of these words
SKIP_WORDS = [
    "driver", "nanny", "housekeeper", "cleaner", "laundry", "cook", "chef",
    "security guard", "welder", "plumber", "electrician", "mechanic",
    "nurse", "medical officer", "pharmacist", "doctor", "teacher",
    "lecturer", "tutor", "caregiver", "director", "head of", "chief",
]

# Backup plan: if Claude can't be reached, send jobs whose title
# contains one of these words instead
BACKUP_KEYWORDS = [
    "product designer", "ux", "ui designer", "product design",
    "graduate trainee", "management trainee", "graduate programme", "trainee",
    "food scientist", "food technologist", "quality control",
    "quality assurance", "production", "customer experience",
    "business analyst", "analyst", "fmcg", "research", "product development",
]

# Your profile. Claude reads this to score every job.
PROFILE = """
WHO I AM
Ebun, 24, based in Lagos, Nigeria. NYSC completed March 2026.
B.Tech Food Science and Technology, FUTA, CGPA 4.30/5.0 (First Class range).
Student internship at a vegetable oil manufacturing company (production and quality exposure).
About 2 years as a UI/UX and Product Designer: fintech (Nexapay), B2B SaaS (Cellcore), freelance.
Taught students for 6 months during NYSC. VP of my CDS group.

MY STRENGTHS
Research and analysis, user empathy, problem solving, clear communication,
Figma and design systems, working with product and engineering teams,
science background with lab and quality control basics.

WHAT I WANT
A full-time job as soon as possible, at a corporate, structured company that pays well.
Location: Lagos (on-site or hybrid) or fully remote.
Experience level: graduate, entry level, or up to 3 years.

SCORE HIGH (8 to 10)
- Graduate trainee or management trainee programmes at banks, Big 4, consulting firms,
  FMCGs, telecoms, oil and gas, or multinationals
- Product Designer, UX Designer, UI/UX Designer, UX Researcher (junior to mid level)
- FMCG roles: quality control, quality assurance, production, R&D, product development

SCORE MEDIUM (6 to 7)
- Customer experience, business analyst, operations analyst, consulting analyst
- Investment or client advisory trainee roles
- Product or project coordinator roles at established companies
- Research or insights roles

SCORE LOW (1 to 5)
- Small or unknown businesses with no clear structure
- Roles asking for 4+ years of experience
- Commission-only or field sales roles
- Graphic design only roles (flyers, social media graphics)
- Drivers, domestic staff, clinical roles, school teaching, artisan work
- On-site roles outside Lagos

BONUS POINTS
+1 if the company is a well-known name.
+1 if it mentions salary, HMO, or good benefits.
+1 if the deadline is soon.
""".strip()

# =====================================================================
# END OF SETTINGS. You should not need to change anything below.
# =====================================================================

SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen.json")
MAX_SEEN = 6000
SUMMARY_LENGTH = 400
TELEGRAM_LIMIT = 3800
LIVE_MESSAGE = "Job agent is live. I'll message you when new jobs fit."

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
ANTHROPIC_API = "https://api.anthropic.com/v1/messages"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
IN_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"


def log(message):
    print(message, flush=True)


# ---------------------------------------------------------------------
# Fetching jobs
# ---------------------------------------------------------------------

def clean_text(raw, limit=None):
    """Strip HTML tags and extra spaces. Cut to `limit` characters."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


def make_job(link, title, summary, location, source, posted):
    return {
        "id": link,
        "title": title,
        "link": link,
        "summary": summary,
        "location": location,
        "source": source,
        "posted": posted,  # datetime in UTC, or None if the feed gives no date
    }


def rss_date(entry):
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
    return None


def rss_location(entry):
    for key in ("location", "job_location", "joblocation", "region", "country", "city", "state"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return clean_text(value, 100)
    return ""


def fetch_rss(name, url):
    response = requests.get(url, headers=BROWSER_HEADERS, timeout=30)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    if not feed.entries:
        raise ValueError("the feed has no jobs in it")

    jobs = []
    for entry in feed.entries:
        link = (entry.get("link") or entry.get("id") or "").strip()
        title = clean_text(entry.get("title"))
        if not link or not title:
            continue
        raw_summary = entry.get("summary") or entry.get("description") or ""
        if not raw_summary and entry.get("content"):
            raw_summary = entry.content[0].get("value", "")
        jobs.append(make_job(
            link=link,
            title=title,
            summary=clean_text(raw_summary, SUMMARY_LENGTH),
            location=rss_location(entry),
            source=name,
            posted=rss_date(entry),
        ))
    return jobs


def iso_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def fetch_greenhouse(company, board):
    response = requests.get(GREENHOUSE_API.format(board=board), headers=BROWSER_HEADERS, timeout=30)
    response.raise_for_status()
    jobs = []
    for item in response.json().get("jobs", []):
        link = (item.get("absolute_url") or "").strip()
        title = clean_text(item.get("title"))
        if not link or not title:
            continue
        # Greenhouse sends the job text as escaped HTML, so unescape it once first
        jobs.append(make_job(
            link=link,
            title=title,
            summary=clean_text(html.unescape(item.get("content") or ""), SUMMARY_LENGTH),
            location=clean_text((item.get("location") or {}).get("name", ""), 100),
            source=f"{company} careers",
            posted=iso_date(item.get("first_published") or item.get("updated_at")),
        ))
    return jobs


def describe_error(error):
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return f"HTTP {error.response.status_code}"
    return f"{type(error).__name__}: {str(error)[:150]}"


def fetch_all_sources():
    """Fetch every source. One failing source never stops the others.

    Returns a list of (label, jobs, error) with jobs=None when it failed.
    """
    sources = [(name, url, fetch_rss, (name, url)) for name, url in RSS_FEEDS]
    sources += [
        (f"{company} (Greenhouse)", GREENHOUSE_API.format(board=board), fetch_greenhouse, (company, board))
        for company, board in GREENHOUSE_BOARDS
    ]
    results = []
    for name, url, fetch, args in sources:
        label = f"{name} [{url}]"
        try:
            jobs = fetch(*args)
            log(f"  OK    {label}: {len(jobs)} jobs")
            results.append((label, jobs, None))
        except Exception as error:
            reason = describe_error(error)
            log(f"  FAIL  {label}: {reason}")
            results.append((label, None, reason))
    return results


def remove_duplicates(jobs):
    ids, unique = set(), []
    for job in jobs:
        if job["id"] not in ids:
            ids.add(job["id"])
            unique.append(job)
    return unique


def fetch_all_jobs():
    log("Checking job sources...")
    results = fetch_all_sources()
    jobs = [job for _, source_jobs, _ in results if source_jobs for job in source_jobs]
    unique = remove_duplicates(jobs)
    log(f"Found {len(unique)} unique jobs ({len(jobs) - len(unique)} duplicates removed).")
    return unique


# ---------------------------------------------------------------------
# Remembering seen jobs
# ---------------------------------------------------------------------

def load_seen():
    """Return the list of seen job IDs, or None if this is the first run."""
    if not os.path.exists(SEEN_FILE):
        return None
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(x) for x in data]
    except (OSError, ValueError):
        pass
    log("seen.json is damaged, so starting fresh like a first run.")
    return None


def save_seen(ids):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(ids[-MAX_SEEN:], f, indent=0)
        f.write("\n")


# ---------------------------------------------------------------------
# Filtering and scoring
# ---------------------------------------------------------------------

def found_words(text, words):
    """Words from the list that appear in the text (matching from the start of a word)."""
    text = text.lower()
    return [w for w in words if re.search(r"\b" + re.escape(w.lower()), text)]


def is_recent(job, now):
    # A job with no date is new to us, so give it the benefit of the doubt
    if job["posted"] is None:
        return True
    return now - job["posted"] <= timedelta(hours=MAX_AGE_HOURS)


def format_date(value):
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "unknown"


SYSTEM_PROMPT = f"""You score job posts for one job seeker, using their profile below.

<profile>
{PROFILE}
</profile>

For each job, give a score from 1 to 10 using the SCORE HIGH, SCORE MEDIUM and SCORE LOW
rules and the BONUS POINTS in the profile. The score can never go above 10.
If a post is vague, judge it from the title, company and location.

Reply with ONLY a JSON array and nothing else, in exactly this form:
[{{"i": 0, "score": 8, "why": "one short plain sentence"}}]

Include every job exactly once. "i" is the job's number in brackets.
"why" is one short, plain sentence on why the job does or does not fit."""


def parse_scores(text, count):
    """Turn Claude's reply into {job number: (score, why)}."""
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("Claude's reply had no JSON array")
    scores = {}
    for item in json.loads(text[start : end + 1]):
        try:
            number = int(item["i"])
            score = int(round(float(item["score"])))
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= number < count:
            scores[number] = (max(1, min(10, score)), str(item.get("why") or "").strip())
    return scores


def ask_claude(batch):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    listing = "\n\n".join(
        f"[{i}] {job['title']}\n"
        f"Source: {job['source']}\n"
        f"Location: {job['location'] or 'not stated'}\n"
        f"Posted: {format_date(job['posted'])}\n"
        f"Summary: {job['summary'] or 'none'}"
        for i, job in enumerate(batch)
    )
    body = {
        "model": MODEL,
        "max_tokens": 4000,
        "temperature": 0,
        "system": SYSTEM_PROMPT,
        "messages": [{
            "role": "user",
            "content": f"Today is {today}. Score these {len(batch)} jobs.\n\n{listing}",
        }],
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    # Try up to 3 times if the API is busy or the network hiccups
    for attempt in range(3):
        if attempt:
            time.sleep(5 * attempt)
        try:
            response = requests.post(ANTHROPIC_API, headers=headers, json=body, timeout=120)
        except requests.RequestException as error:
            problem = f"network error ({type(error).__name__})"
            continue
        if response.status_code == 200:
            reply = response.json()
            text = "".join(
                block.get("text", "") for block in reply.get("content", []) if block.get("type") == "text"
            )
            return parse_scores(text, len(batch))
        problem = f"HTTP {response.status_code}: {response.text[:300]}"
        if response.status_code not in (408, 429, 500, 502, 503, 504, 529):
            break
    raise RuntimeError(problem)


def keyword_matches(batch):
    matches = []
    for job in batch:
        words = found_words(job["title"], BACKUP_KEYWORDS)
        if words:
            matches.append({**job, "score": None, "why": "Matched keyword: " + ", ".join(words[:3])})
    return matches


def score_jobs(jobs):
    """Return the jobs worth sending, each with a score and a reason."""
    if not ANTHROPIC_API_KEY:
        log("ANTHROPIC_API_KEY is not set, so using backup keywords instead of Claude.")
    matches = []
    for start in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[start : start + BATCH_SIZE]
        if ANTHROPIC_API_KEY:
            try:
                scores = ask_claude(batch)
            except Exception as error:
                log(f"Claude scoring failed ({error}). Using backup keywords for these {len(batch)} jobs.")
            else:
                log(f"Claude scored {len(scores)} of {len(batch)} jobs:")
                for i, job in enumerate(batch):
                    score, why = scores.get(i, (0, ""))
                    log(f"  {score:>2}/10  {job['title']}  ({job['source']})")
                    if score >= MIN_SCORE:
                        matches.append({**job, "score": score, "why": why})
                continue
        matches.extend(keyword_matches(batch))
    # Highest score first. Keyword matches (no score) go last.
    matches.sort(key=lambda m: (m["score"] is not None, m["score"] or 0), reverse=True)
    return matches


# ---------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------

def telegram_ready():
    return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)


def send_telegram(text, dry_run):
    """Send one message. Returns True if it worked (or if this is a dry run)."""
    if dry_run:
        log("----- Telegram message (not sent, dry run) -----")
        log(text)
        log("------------------------------------------------")
        return True
    try:
        response = requests.post(
            TELEGRAM_API.format(token=TELEGRAM_TOKEN),
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            },
            timeout=30,
        )
        result = response.json()
    except Exception as error:
        # Only print the error type: the full error can contain the bot token
        log(f"Telegram error: {type(error).__name__}")
        return False
    if not result.get("ok"):
        log(f"Telegram error: {result.get('description', response.status_code)}")
        return False
    log("Telegram message sent.")
    return True


def format_job(match):
    score = f"{match['score']}/10" if match["score"] is not None else "Keyword match"
    title = html.escape(match["title"][:200], quote=False)
    source = html.escape(match["source"], quote=False)
    why = html.escape((match["why"] or "No reason given.")[:300], quote=False)
    link = html.escape(match["link"], quote=True)
    return f'<b>{title}</b>\n{score} · {source}\n<i>{why}</i>\n<a href="{link}">Open job</a>'


def build_messages(matches):
    """One message for the whole run, split only if it gets too long."""
    count = len(matches)
    messages = []
    current = f"<b>{count} new job{'s' if count != 1 else ''} for you</b>"
    for match in matches:
        block = format_job(match)
        if len(current) + 2 + len(block) > TELEGRAM_LIMIT:
            messages.append(current)
            current = block
        else:
            current += "\n\n" + block
    messages.append(current)
    return messages


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def check_feeds():
    log("Testing every source. Nothing is saved or sent.\n")
    results = fetch_all_sources()
    now = datetime.now(timezone.utc)
    log("")
    for label, jobs, error in results:
        if jobs is None:
            log(f"DEAD     {label}\n         {error}")
            continue
        dates = [job["posted"] for job in jobs if job["posted"]]
        recent = sum(1 for job in jobs if is_recent(job, now))
        newest = format_date(max(dates)) if dates else "no dates in feed"
        log(f"WORKING  {label}\n         {len(jobs)} jobs, {recent} from the last {MAX_AGE_HOURS} hours, newest: {newest}")
        for job in jobs[:3]:
            log(f"         e.g. {job['title']}  |  {job['location'] or 'no location'}")


def first_run(jobs, dry_run):
    if not jobs:
        log("First run, but no jobs could be fetched from any source. Saving nothing. Will try again next run.")
        sys.exit(1)
    save_seen([job["id"] for job in jobs])
    log(f"First run: saved {len(jobs)} current jobs as seen, so you don't get flooded with old posts.")
    if not send_telegram(LIVE_MESSAGE, dry_run):
        sys.exit(1)


def normal_run(jobs, seen, dry_run):
    seen_ids = set(seen)
    new_jobs = [job for job in jobs if job["id"] not in seen_ids]
    now = datetime.now(timezone.utc)
    recent = [job for job in new_jobs if is_recent(job, now)]
    to_score = [job for job in recent if not found_words(job["title"], SKIP_WORDS)]
    log(
        f"{len(new_jobs)} new jobs. {len(recent)} posted in the last {MAX_AGE_HOURS} hours. "
        f"{len(to_score)} left after skipping misfit titles."
    )

    matches = score_jobs(to_score) if to_score else []
    if matches:
        log(f"{len(matches)} jobs fit. Sending to Telegram.")
        for message in build_messages(matches):
            if not send_telegram(message, dry_run):
                log("Could not send to Telegram. Not saving, so these jobs are tried again next run.")
                sys.exit(1)
    else:
        log("No new jobs fit this time.")

    save_seen(seen + [job["id"] for job in new_jobs])
    log(f"Saved {len(new_jobs)} new job IDs to seen.json.")


def main():
    if "--check-feeds" in sys.argv:
        check_feeds()
        return

    dry_run = "--dry-run" in sys.argv or not telegram_ready()
    if not telegram_ready() and IN_GITHUB_ACTIONS and "--dry-run" not in sys.argv:
        log(
            "TELEGRAM_TOKEN or TELEGRAM_CHAT_ID is missing. Add both in your repo under "
            "Settings > Secrets and variables > Actions, then run again."
        )
        sys.exit(1)
    if dry_run:
        log("Dry run: Telegram messages will be printed here, not sent.")

    jobs = fetch_all_jobs()
    seen = load_seen()
    if seen is None:
        first_run(jobs, dry_run)
    else:
        normal_run(jobs, seen, dry_run)


if __name__ == "__main__":
    main()
