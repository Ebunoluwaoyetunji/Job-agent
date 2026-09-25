#!/usr/bin/env python3
"""
Job alert agent.

Checks job feeds, skips jobs it has already seen, gives each new job a score
with a simple points system, and sends the good ones to Telegram.

    python job_agent.py                 normal run
    python job_agent.py --preview       score today's jobs and show the top 15, change nothing
    python job_agent.py --check-feeds   test every feed, change nothing
    python job_agent.py --dry-run       print Telegram messages instead of sending
"""

import calendar
import html
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import feedparser
import requests


# =====================================================================
# SETTINGS YOU CAN EDIT
# =====================================================================
#
# All word lists ignore capital letters and match whole words only,
# so "ey" does not match "money". Keywords also match their plurals
# ("graduate trainee" also finds "Graduate Trainees").

# ----- Where to look -----------------------------------------------

# Job feeds to check: ("Name shown in Telegram", "Feed link")
RSS_FEEDS = [
    ("MyJobMag", "https://www.myjobmag.com/feeds/ng/jobsxml.xml"),
    ("MyJobMag", "https://www.myjobmag.com/feeds/ng/jobsxml_by_categories.xml"),
    ("HotNigerianJobs", "https://www.hotnigerianjobs.com/feed/rss.xml"),
    ("We Work Remotely", "https://weworkremotely.com/remote-jobs.rss"),
]
# Removed on 25 Sep 2026: Jobzilla (https://www.jobzilla.ng/feed) blocks GitHub's servers.

# Company career pages hosted on Greenhouse: ("Company name", "board name").
# The board name is the last part of the link, e.g. boards.greenhouse.io/moniepoint
GREENHOUSE_BOARDS = [
    ("Moniepoint", "moniepoint"),
]

# Ignore jobs posted longer ago than this many hours
MAX_AGE_HOURS = 48

# Skip any job whose title has one of these words. Skipped jobs are never scored.
SKIP_WORDS = [
    "driver", "nanny", "housekeeper", "cleaner", "laundry", "cook", "chef",
    "security guard", "welder", "plumber", "electrician", "mechanic",
    "nurse", "medical officer", "pharmacist", "doctor", "teacher",
    "lecturer", "tutor", "caregiver", "director", "head of", "chief",
]

# ----- Scoring -----------------------------------------------------

# Send jobs that score this much or more
MIN_SCORE = 6

# Keywords looked for in the job TITLE. The first list that matches gives the points.
HIGH_KEYWORDS = [
    "graduate trainee", "management trainee", "graduate programme", "graduate program",
    "trainee programme", "graduate intern", "product designer", "ux designer",
    "ui designer", "ui/ux", "ux/ui", "ux researcher", "product design",
    "quality control", "quality assurance", "qc officer", "qa officer",
    "food scientist", "food technologist", "research and development", "r&d",
    "product development", "production officer", "production analyst",
    "production supervisor",
]
HIGH_POINTS = 8

MEDIUM_KEYWORDS = [
    "customer experience", "cx", "business analyst", "operations analyst",
    "consulting analyst", "analyst", "investment", "client advisory", "advisory",
    "product coordinator", "project coordinator", "research", "insights",
    "customer success",
]
MEDIUM_POINTS = 6

# If the title has no keyword, the summary is checked instead, for this many points less
SUMMARY_ONLY_PENALTY = 2

# Bonus: a well-known company is named (in the title, location, summary or source)
WELL_KNOWN_COMPANIES = [
    "Zenith", "GTBank", "GTCO", "Access Bank", "First Bank", "FirstBank", "UBA",
    "Stanbic", "Fidelity", "Wema", "Sterling", "FCMB", "Union Bank", "Ecobank",
    "Standard Chartered", "Citibank", "KPMG", "PwC", "Deloitte", "EY",
    "Ernst & Young", "Accenture", "McKinsey", "BCG", "Nestle", "Unilever",
    "Nigerian Breweries", "Guinness", "Diageo", "Cadbury", "Dangote", "Flour Mills",
    "FMN", "Olam", "PZ Cussons", "Seven Up", "Coca-Cola", "Nigerian Bottling",
    "Friesland", "Promasidor", "Arla", "Tolaram", "MTN", "Airtel", "Glo", "9mobile",
    "Shell", "Chevron", "TotalEnergies", "Seplat", "NNPC", "ExxonMobil",
    "Moniepoint", "Flutterwave", "Paystack", "Interswitch", "Kuda", "OPay",
    "PalmPay", "Andela", "Meristem", "Leadway", "AXA Mansard",
]
COMPANY_BONUS = 1

# Bonus: pay or benefits are mentioned
BENEFIT_WORDS = ["salary", "hmo", "pension", "benefits"]
BENEFITS_BONUS = 1

# Bonus: Lagos or remote is mentioned
GOOD_LOCATIONS = [
    "lagos", "ikeja", "lekki", "victoria island", "remote", "anywhere in the world",
]
LOCATION_BONUS = 1

# Penalty: a senior title. Titles with a NOT_SENIOR phrase are never penalised.
SENIOR_WORDS = ["senior", "lead", "manager"]
NOT_SENIOR = ["management trainee"]
SENIOR_PENALTY = 3

# Penalty: asks for this many years of experience or more
TOO_MANY_YEARS = 4
EXPERIENCE_PENALTY = 3

# Penalty: commission-based pay
COMMISSION_WORDS = ["commission-based", "commission only"]
COMMISSION_PENALTY = 3

# Penalty: a graphic design title
GRAPHIC_DESIGN_WORDS = ["graphic designer"]
GRAPHIC_DESIGN_PENALTY = 3

# Penalty: names a place outside Lagos, and doesn't mention a GOOD_LOCATION
OTHER_STATES = [
    "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue",
    "Borno", "Cross River", "Delta", "Ebonyi", "Edo", "Ekiti", "Enugu", "Gombe",
    "Imo", "Jigawa", "Kaduna", "Kano", "Katsina", "Kebbi", "Kogi", "Kwara",
    "Nasarawa", "Niger", "Ogun", "Ondo", "Osun", "Oyo", "Plateau", "Rivers",
    "Sokoto", "Taraba", "Yobe", "Zamfara", "FCT", "Abuja", "Port Harcourt",
    "Ibadan", "Benin City", "Warri", "Calabar", "Uyo", "Owerri", "Onitsha",
    "Abeokuta", "Ilorin", "Jos", "Akure", "Asaba",
]
OTHER_STATE_PENALTY = 2

# =====================================================================
# END OF SETTINGS. You should not need to change anything below.
# =====================================================================

SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen.json")
MAX_SEEN = 6000
SUMMARY_LENGTH = 400
TELEGRAM_LIMIT = 3800
PREVIEW_COUNT = 15
LIVE_MESSAGE = "Job agent is live. I'll message you when new jobs fit."

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
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
        where = f"{name} [{url}]"
        try:
            jobs = fetch(*args)
            log(f"  OK    {where}: {len(jobs)} jobs")
            results.append((where, jobs, None))
        except Exception as error:
            reason = describe_error(error)
            log(f"  FAIL  {where}: {reason}")
            results.append((where, None, reason))
    return results


def job_keys(job):
    """How a job is recognised: its link, and its title on that site.

    The title key matters because MyJobMag lists some jobs in both of its
    feeds under two different links.
    """
    title = re.sub(r"\s+", " ", job["title"].lower())
    return [job["id"], f"{job['source']}|{title}"]


def remove_duplicates(jobs):
    """Keep one copy of each job. The copy we keep remembers every key."""
    kept_by_key, unique = {}, []
    for job in jobs:
        keys = job_keys(job)
        kept = next((kept_by_key[k] for k in keys if k in kept_by_key), None)
        if kept is None:
            kept = job
            kept["keys"] = keys
            unique.append(kept)
        else:
            kept["keys"] += [k for k in keys if k not in kept["keys"]]
            kept["location"] = kept["location"] or job["location"]
        for k in keys:
            kept_by_key[k] = kept
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
    """Return the list of seen job keys, or None if this is the first run."""
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


def save_seen(keys):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(keys[-MAX_SEEN:], f, indent=0)
        f.write("\n")


# ---------------------------------------------------------------------
# Matching words
# ---------------------------------------------------------------------

HYPHENS = set("-\u2010\u2011\u2012\u2013\u2014")  # all the kinds of dash


def normalize(text):
    """Lowercase, drop accents (Nestlé becomes nestle) and treat hyphens as spaces.

    Keeps the same length as the input, so a match can be cut out of the
    original text with its capitals intact.
    """
    chars = []
    for ch in text:
        base = unicodedata.normalize("NFKD", ch)[:1].lower()[:1]
        chars.append(" " if base in HYPHENS else base)
    return "".join(chars)


@lru_cache(maxsize=None)
def term_regex(term, plurals):
    words = normalize(term).split()
    body = r"\s+".join(re.escape(word) for word in words)
    ending = "(?:s|es)?" if plurals else ""
    return re.compile(rf"(?<![a-z0-9]){body}{ending}(?![a-z0-9])")


def find_first(terms, text, plurals=True):
    """The first term from the list found in the text, as written in the text. None if none."""
    normalized = normalize(text)
    for term in terms:
        match = term_regex(term, plurals).search(normalized)
        if match:
            return re.sub(r"\s+", " ", text[match.start() : match.end()])
    return None


def label(found):
    return found[:1].upper() + found[1:]


NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_NUMBER = r"(\d{1,2}|" + "|".join(NUMBER_WORDS) + r")"
_BRACKETED = r"(?:\s*\(\s*\d{1,2}\s*\))?"  # "five (5) years"
YEARS_PATTERN = re.compile(
    rf"(?<![a-z0-9]){_NUMBER}{_BRACKETED}\s*\+?\s*"
    rf"(?:(?:to|and)?\s*\d{{1,2}}{_BRACKETED}\s*\+?\s*)?"  # a range like "3 - 5 years"
    r"(?:years?|yrs?)(?![a-z])"
)
EXPERIENCE_HINTS = ("experience", "similar role", "similar position", "working")


def years_asked(text):
    """Years of experience the post asks for (the lower end of a range), or 0."""
    normalized = normalize(text)
    most = 0
    for match in YEARS_PATTERN.finditer(normalized):
        word = match.group(1)
        years = int(word) if word.isdigit() else NUMBER_WORDS[word]
        before = normalized[max(0, match.start() - 60) : match.start()]
        after = normalized[match.end() : match.end() + 60]
        if years > 15 or after.lstrip().startswith("old"):
            continue  # an age limit or company history, not experience
        if any(hint in before or hint in after for hint in EXPERIENCE_HINTS):
            most = max(most, years)
    return most


# ---------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------

def keyword_points(job):
    """Points for the best keyword: title first, then the summary for fewer points."""
    for text, minus, note in (
        (job["title"], 0, ""),
        (job["summary"], SUMMARY_ONLY_PENALTY, " (in summary)"),
    ):
        for keywords, points in ((HIGH_KEYWORDS, HIGH_POINTS), (MEDIUM_KEYWORDS, MEDIUM_POINTS)):
            found = find_first(keywords, text)
            if found:
                return points - minus, label(found) + note
    return 0, "No keyword match"


def score_job(job):
    """Return (score, reason) using the points in SETTINGS YOU CAN EDIT."""
    title = job["title"]
    everything = " | ".join([title, job["location"], job["summary"], job["source"]])

    score, reason = keyword_points(job)
    reasons = [reason]

    if find_first(WELL_KNOWN_COMPANIES, everything, plurals=False):
        score += COMPANY_BONUS
        reasons.append("Well-known company")
    if find_first(BENEFIT_WORDS, everything):
        score += BENEFITS_BONUS
        reasons.append("Salary/benefits")

    place = find_first(GOOD_LOCATIONS, everything, plurals=False)
    other_place = find_first(OTHER_STATES, everything, plurals=False)
    if place:
        score += LOCATION_BONUS
        reasons.append(label(place))
    elif other_place:
        score -= OTHER_STATE_PENALTY
        reasons.append(f"{label(other_place)} (-{OTHER_STATE_PENALTY})")

    senior = find_first(SENIOR_WORDS, title)
    if senior and not find_first(NOT_SENIOR, title):
        score -= SENIOR_PENALTY
        reasons.append(f"{label(senior)} (-{SENIOR_PENALTY})")

    years = years_asked(everything)
    if years >= TOO_MANY_YEARS:
        score -= EXPERIENCE_PENALTY
        reasons.append(f"{years}+ years experience (-{EXPERIENCE_PENALTY})")

    if find_first(COMMISSION_WORDS, everything):
        score -= COMMISSION_PENALTY
        reasons.append(f"Commission (-{COMMISSION_PENALTY})")
    if find_first(GRAPHIC_DESIGN_WORDS, title):
        score -= GRAPHIC_DESIGN_PENALTY
        reasons.append(f"Graphic design (-{GRAPHIC_DESIGN_PENALTY})")

    return score, " · ".join(reasons)


def score_all(jobs):
    """Every job with its score and reason, best first."""
    scored = []
    for job in jobs:
        score, reason = score_job(job)
        scored.append({**job, "score": score, "why": reason})
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    scored.sort(key=lambda j: (j["score"], j["posted"] or oldest), reverse=True)
    return scored


# ---------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------

def is_recent(job, now):
    # A job with no date is new to us, so give it the benefit of the doubt
    if job["posted"] is None:
        return True
    return now - job["posted"] <= timedelta(hours=MAX_AGE_HOURS)


def format_date(value):
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "unknown"


def recent_and_relevant(jobs):
    now = datetime.now(timezone.utc)
    recent = [job for job in jobs if is_recent(job, now)]
    relevant = [job for job in recent if not find_first(SKIP_WORDS, job["title"])]
    return recent, relevant


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
    title = html.escape(match["title"][:200], quote=False)
    source = html.escape(match["source"], quote=False)
    why = html.escape(match["why"][:300], quote=False)
    link = html.escape(match["link"], quote=True)
    return f'<b>{title}</b>\nScore {match["score"]} · {source}\n<i>{why}</i>\n<a href="{link}">Open job</a>'


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
    for where, jobs, error in results:
        if jobs is None:
            log(f"DEAD     {where}\n         {error}")
            continue
        dates = [job["posted"] for job in jobs if job["posted"]]
        recent = sum(1 for job in jobs if is_recent(job, now))
        newest = format_date(max(dates)) if dates else "no dates in feed"
        log(f"WORKING  {where}\n         {len(jobs)} jobs, {recent} from the last {MAX_AGE_HOURS} hours, newest: {newest}")
        for job in jobs[:3]:
            log(f"         e.g. {job['title']}  |  {job['location'] or 'no location'}")


def preview():
    """Score the current jobs and show the best ones. Nothing is saved or sent."""
    log("Preview: scoring the current jobs. Nothing is saved or sent.")
    recent, relevant = recent_and_relevant(fetch_all_jobs())
    scored = score_all(relevant)
    passing = sum(1 for job in scored if job["score"] >= MIN_SCORE)
    log(
        f"\n{len(recent)} jobs from the last {MAX_AGE_HOURS} hours. "
        f"{len(recent) - len(relevant)} skipped as misfits. "
        f"{passing} of the other {len(relevant)} score {MIN_SCORE} or more.\n"
    )
    log(f"Top {min(PREVIEW_COUNT, len(scored))}:")
    for rank, job in enumerate(scored[:PREVIEW_COUNT], 1):
        log(f"{rank:>2}. Score {job['score']:>2}  {job['title']}")
        log(f"              {job['why']}")
        log(f"              {job['source']}  |  {job['link']}")


def first_run(jobs, dry_run):
    if not jobs:
        log("First run, but no jobs could be fetched from any source. Saving nothing. Will try again next run.")
        sys.exit(1)
    save_seen([key for job in jobs for key in job["keys"]])
    log(f"First run: saved {len(jobs)} current jobs as seen, so you don't get flooded with old posts.")
    if not send_telegram(LIVE_MESSAGE, dry_run):
        sys.exit(1)


def normal_run(jobs, seen, dry_run):
    seen_keys = set(seen)
    new_jobs = [job for job in jobs if not any(key in seen_keys for key in job["keys"])]
    recent, relevant = recent_and_relevant(new_jobs)
    log(
        f"{len(new_jobs)} new jobs. {len(recent)} posted in the last {MAX_AGE_HOURS} hours. "
        f"{len(relevant)} left after skipping misfit titles."
    )

    scored = score_all(relevant)
    for job in scored:
        log(f"  {job['score']:>3}  {job['title']}  ({job['why']})")
    matches = [job for job in scored if job["score"] >= MIN_SCORE]

    if matches:
        log(f"{len(matches)} jobs score {MIN_SCORE} or more. Sending to Telegram.")
        for message in build_messages(matches):
            if not send_telegram(message, dry_run):
                log("Could not send to Telegram. Not saving, so these jobs are tried again next run.")
                sys.exit(1)
    else:
        log("No new jobs fit this time.")

    save_seen(seen + [key for job in new_jobs for key in job["keys"]])
    log(f"Saved {len(new_jobs)} new jobs to seen.json.")


def main():
    if "--check-feeds" in sys.argv:
        check_feeds()
        return
    if "--preview" in sys.argv:
        preview()
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
