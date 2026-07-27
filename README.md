# Saturday Lineup — score cache scheduler

Keeps the app's game data fresh without every player's phone calling the
data provider directly.

A GitHub Actions job fetches the current week's games and betting lines
from [CollegeFootballData](https://collegefootballdata.com) and writes
them to a Firestore document that the app reads. One fetch serves every
user, so the number of API calls stays flat no matter how many people
are playing — instead of growing with them, which is what exhausted the
plan's monthly quota in July 2026.

## How often it runs

The workflow wakes every 5 minutes (GitHub's minimum), but only refreshes
when the cache is older than the day warrants:

| Day | Refresh interval |
|-----|------------------|
| Saturday | 5 minutes |
| Thursday, Friday, Sunday | 10 minutes |
| Monday–Wednesday | 60 minutes |

A skipped run costs one Firestore read and no provider calls at all.

## Setup

Two repository secrets are required
(**Settings → Secrets and variables → Actions**):

| Secret | Value |
|--------|-------|
| `FIREBASE_SERVICE_ACCOUNT` | The full contents of the Firebase service-account JSON |
| `CFBD_API_KEY` | CollegeFootballData API key |

This repository is public so the scheduled workflow gets unlimited free
Actions minutes. It contains no credentials — both are read from the
encrypted secrets above at runtime.

Run it by hand anytime from the **Actions** tab; set `FORCE_REFRESH=1`
to bypass the freshness check.
