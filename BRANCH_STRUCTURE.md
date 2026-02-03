# Branch Structure

Two deployment options for the Appointment Checker Bot:

## Branch: `main` (Current)
**Deployment Target:** Fly.io (or any Docker-based host)

**Characteristics:**
- Infinite loop with 17-minute checks (every 1020 seconds)
- Includes Flask app to keep container alive
- Designed for Fly.io / Docker deployments
- Persistent process

**Deploy:**
```bash
git checkout main
flyctl deploy
```

**Files:**
- `bot.py` — infinite loop + Flask
- `Dockerfile`
- `fly.toml`

---

## Branch: `github-actions`
**Deployment Target:** GitHub Actions (free scheduled runs)

**Characteristics:**
- Runs once per invocation, exits cleanly
- No Flask (not needed)
- Scheduled via GitHub Actions cron (default: every 30 min)
- Completely free (first 2000 minutes/month)

**Deploy:**
```bash
git checkout github-actions
git push origin github-actions
# Then set GitHub Secrets (see GITHUB_ACTIONS_SETUP.md on that branch)
```

**Files:**
- `bot.py` — single run, exit codes
- `.github/workflows/appointment-checker.yml` — cron trigger
- `GITHUB_ACTIONS_SETUP.md` — detailed setup guide

---

## Switching Between Branches

Switch to **Fly.io version:**
```bash
git checkout main
```

Switch to **GitHub Actions version:**
```bash
git checkout github-actions
```

---

## Quick Comparison

| Feature | main (Fly) | github-actions (GH Actions) |
|---------|-----------|---------------------------|
| Cost | $0/month (Fly free tier) | $0/month (first 2000 min) |
| Setup | Minimal | ~5 min (secrets setup) |
| Run Frequency | Every 17 min (continuous) | Every 30 min (configurable) |
| Maintenance | Minimal | None |
| UI Logging | Via `flyctl logs` | GitHub Actions tab |
| Failure Artifacts | In Fly logs | GitHub Artifacts (7 days) |

---

## Merge Strategy (Optional)

If you want both versions in one branch, create a `--no-ff` merge:
```bash
git checkout main
git merge github-actions --no-ff
# Choose which files to keep using git mergetool or manual conflict resolution
```

Otherwise, keep branches separate and switch as needed.
