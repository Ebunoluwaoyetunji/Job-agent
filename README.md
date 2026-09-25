# Job Agent

A personal job alert bot. Every 30 minutes, GitHub runs `job_agent.py`, which:

1. Checks job feeds (MyJobMag, HotNigerianJobs, Jobzilla, We Work Remotely, Moniepoint careers).
2. Skips jobs it has already seen (they are listed in `seen.json`).
3. Skips old posts and obvious misfits (driver, chef, nurse and so on).
4. Asks Claude to score each new job from 1 to 10 against your profile.
5. Sends the good ones (score 6 or more) to your Telegram in one message.

The very first run only saves the current jobs and sends
"Job agent is live. I'll message you when new jobs fit." so you don't get flooded with old posts.

## Setup (one time)

You need three secrets. Add them to the repo, never into the code.

| Secret | How to get it |
| --- | --- |
| `TELEGRAM_TOKEN` | In Telegram, message **@BotFather**, send `/newbot`, follow the steps. It gives you a token like `123456:ABC...`. |
| `TELEGRAM_CHAT_ID` | Message **@userinfobot**. It replies with your ID (a number). Then open your new bot and tap **Start**, or it can't message you. |
| `ANTHROPIC_API_KEY` | Go to **console.anthropic.com**, add a small amount of credit (a few dollars is plenty), then create a key under **API keys**. |

**Option A: on the GitHub website**
Repo page > **Settings** > **Secrets and variables** > **Actions** > **New repository secret**.
Add each of the three with the exact names above.

**Option B: in a terminal with the GitHub CLI**

```
gh secret set TELEGRAM_TOKEN --repo YOUR-USERNAME/job-agent
gh secret set TELEGRAM_CHAT_ID --repo YOUR-USERNAME/job-agent
gh secret set ANTHROPIC_API_KEY --repo YOUR-USERNAME/job-agent
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
| Update what I'm looking for | Edit the text inside `PROFILE` |
| Stop seeing a type of job | Add a word to `SKIP_WORDS` |
| Add a job site | Add a line to `RSS_FEEDS`: `("Site name", "feed link"),` |
| Add a company on Greenhouse | Add a line to `GREENHOUSE_BOARDS`: `("Company", "board-name"),` |
| Look further back in time | Raise `MAX_AGE_HOURS` |

Tips:

- Keep the quotes and commas exactly as they are when you edit a list.
- `BACKUP_KEYWORDS` is only used when Claude can't be reached (no key, no credit, or an outage).
- Your changes apply from the next run.

## Checking that it works

- **Actions** tab: every run is listed. Green means it worked. Click a run, then **check-jobs**, then **Run the job agent** to see what it found and how Claude scored each job.
- To test all the feeds without saving or sending anything, run this on your own computer:
  `pip install -r requirements.txt` then `python job_agent.py --check-feeds`
- To see what would be sent without sending it: `python job_agent.py --dry-run`

## Good to know

- **Cost:** Claude Haiku is cheap. Expect roughly a few dollars a month, depending on how many jobs are posted.
- **Timing:** GitHub sometimes starts scheduled runs a few minutes late. That's normal.
- **Starting over:** delete `seen.json` from the repo. The next run acts like a first run again.
- **If a run fails:** GitHub emails you. Open the run in the **Actions** tab and read the red step. The most common causes are a missing or mistyped secret, or you haven't tapped **Start** on your bot.
- **This repo is public**, so anyone can read your profile in `job_agent.py`. Your secrets stay private.
