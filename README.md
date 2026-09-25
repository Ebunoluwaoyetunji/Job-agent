# Job Agent

A free, personal job alert bot. Every 30 minutes, GitHub runs `job_agent.py`, which:

1. Checks job feeds (MyJobMag, HotNigerianJobs, We Work Remotely, Moniepoint careers).
2. Skips jobs it has already seen (they are listed in `seen.json`).
3. Skips old posts and obvious misfits (driver, chef, nurse and so on).
4. Gives each new job a score with a simple points system (below).
5. Sends jobs that score 6 or more to your Telegram, in one message.

The very first run only saves the current jobs and sends
"Job agent is live. I'll message you when new jobs fit." so you don't get flooded with old posts.

Everything is free: GitHub runs it for free on public repos, and Telegram bots are free.

## How jobs are scored

| Rule | Points |
| --- | --- |
| Title has a HIGH keyword (graduate trainee, product designer, quality control...) | 8 |
| Title has a MEDIUM keyword (analyst, customer experience, research...) | 6 |
| Keyword only in the summary, not the title | 2 less |
| No keyword anywhere | 0 |
| A well-known company is named | +1 |
| Mentions salary, HMO, pension or benefits | +1 |
| Mentions Lagos or remote | +1 |
| Title has senior or lead | -3 |
| Title has manager (or the typo "manger"), except "management trainee" | -5 |
| Asks for 4 or more years of experience | -3 |
| Commission-based or commission only | -3 |
| Title is graphic designer | -3 |
| Names another Nigerian state, and not Lagos or remote | -2 |

For titles like "Financial Analyst at Landgate Investments", only the role part
("Financial Analyst") is checked for title keywords and title penalties, never the company name.

Each Telegram alert shows the score and why, for example:
*Graduate Trainee · Well-known company · Lagos*

## Setup (one time)

You need two secrets. Add them to the repo, never into the code.

| Secret | How to get it |
| --- | --- |
| `TELEGRAM_TOKEN` | In Telegram, message **@BotFather**, send `/newbot`, follow the steps. It gives you a token like `123456:ABC...`. |
| `TELEGRAM_CHAT_ID` | Message **@userinfobot**. It replies with your ID (a number). Then open your new bot and tap **Start**, or it can't message you. |

**Option A: on the GitHub website**
Repo page > **Settings** > **Secrets and variables** > **Actions** > **New repository secret**.
Add both with the exact names above.

**Option B: in a terminal with the GitHub CLI**

```
gh secret set TELEGRAM_TOKEN --repo YOUR-USERNAME/job-agent
gh secret set TELEGRAM_CHAT_ID --repo YOUR-USERNAME/job-agent
```

Each command asks you to paste the value. It stays hidden.

Then start the first run: **Actions** tab > **Job agent** > **Run workflow**.
You should get the "Job agent is live" message within a minute or two.

## Tuning it

Everything you might want to change is at the top of `job_agent.py`, under
**SETTINGS YOU CAN EDIT**. You can edit it right on GitHub: open the file, click the pencil icon, then **Commit changes**.

| I want to... | Change this |
| --- | --- |
| Get fewer, better matches | Raise `MIN_SCORE` (for example to `7` or `8`) |
| Get more matches | Lower `MIN_SCORE` (for example to `5`) |
| Look for a new kind of role | Add it to `HIGH_KEYWORDS` or `MEDIUM_KEYWORDS` |
| Add a company I like | Add it to `WELL_KNOWN_COMPANIES` |
| Change how much a rule counts | Change its number, like `HIGH_POINTS` or `MANAGER_PENALTY` |
| Stop seeing a type of job | Add a word to `SKIP_WORDS` |
| Add a job site | Add a line to `RSS_FEEDS`: `("Site name", "feed link"),` |
| Add a company on Greenhouse | Add a line to `GREENHOUSE_BOARDS`: `("Company", "board-name"),` |

Tips:

- Keep the quotes and commas exactly as they are when you edit a list.
- Words ignore capitals and match whole words only, so "ey" won't match "money".
- Your changes apply from the next run.

**Test your changes before they go live:** **Actions** tab > **Job agent** > **Run workflow**,
tick **Preview only**, then **Run workflow**. Open the run, then **check-jobs**, then **Run the job agent**.
It shows today's top 15 jobs with their scores and reasons. It sends and saves nothing.

## Checking that it works

- **Actions** tab: every run is listed. Green means it worked. Open a run, then **check-jobs**, then **Run the job agent** to see every new job and its score.
- On your own computer (optional): `pip install -r requirements.txt`, then
  `python job_agent.py --check-feeds` tests every feed, and
  `python job_agent.py --preview` shows the top 15 jobs right now.

## Good to know

- **Timing:** GitHub sometimes starts scheduled runs a few minutes late. That's normal.
- **Starting over:** delete `seen.json` from the repo. The next run acts like a first run again.
- **If a run fails:** GitHub emails you. Open the run in the **Actions** tab and read the red step. The most common causes are a missing or mistyped secret, or you haven't tapped **Start** on your bot.
- **HotNigerianJobs:** on 25 Sep 2026 their feed was stuck two days behind their website. The agent still checks it and will pick up new jobs when their feed catches up.
- **This repo is public**, so anyone can read the settings in `job_agent.py`. Your secrets stay private.
