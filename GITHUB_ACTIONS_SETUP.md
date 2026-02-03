# GitHub Actions Setup Guide

Run your appointment checker bot as a scheduled workflow on GitHub Actions (completely free).

## Setup Steps

### 1. Push to GitHub

If you haven't already:
```bash
git init
git add .
git commit -m "Initial commit: appointment checker bot"
git remote add origin https://github.com/YOUR_USERNAME/appointment_checker_bot.git
git branch -M main
git push -u origin main
```

### 2. Add GitHub Secrets

Go to your GitHub repository → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these three secrets:
- **TELEGRAM_TOKEN**: Your Telegram bot token (from BotFather)
- **TELEGRAM_CHAT_ID**: Your Telegram chat ID
- **GOOGLE_API_KEY**: Your Google Gemini API key

### 3. Enable Actions

Go to **Actions** tab → Click **I understand my workflows, go ahead and enable them**

### 4. Schedule Configuration (Optional)

Edit `.github/workflows/appointment-checker.yml` to adjust the schedule:

```yaml
on:
  schedule:
    # Every 30 minutes (current)
    - cron: '*/30 * * * *'
    
    # Alternative: Every hour
    # - cron: '0 * * * *'
    
    # Alternative: Every 15 minutes
    # - cron: '*/15 * * * *'
    
    # Alternative: Every day at 9 AM UTC
    # - cron: '0 9 * * *'
```

[Cron syntax help](https://crontab.guru/)

## How It Works

- **Trigger**: Runs automatically on schedule (default: every 30 minutes)
- **Runtime**: ~2-3 minutes per run
- **Logs**: View in GitHub Actions tab
- **Artifacts**: Screenshots and HTML files saved for 7 days on failure
- **Cost**: Free (GitHub Actions has 2,000 free minutes/month per account)

## Monitoring

1. Go to **Actions** tab in your repository
2. Click on the workflow run to see logs
3. Check **Artifacts** section if the run failed (screenshots available)

## Manual Trigger

Go to **Actions** → **Appointment Checker Bot** → **Run workflow** → **Run workflow** button

## Troubleshooting

If the bot doesn't find Chrome:
- GitHub Actions Ubuntu runners come with Chrome pre-installed
- If issues persist, edit `.github/workflows/appointment-checker.yml` and add:
  ```yaml
  - name: Install Chrome dependencies (if needed)
    run: |
      sudo apt-get update
      sudo apt-get install -y chromium-browser
  ```

If Telegram notifications don't send:
- Verify secrets are set correctly (don't include quotes in the secret value)
- Check logs for "Notification sent successfully"

## File Structure

```
.
├── .github/workflows/
│   └── appointment-checker.yml  ← Workflow definition
├── bot.py                       ← Main bot (refactored for GitHub Actions)
├── requirements.txt
├── .env                         ← Local only (secrets used in Actions)
└── GITHUB_ACTIONS_SETUP.md      ← This file
```

## Advantages

✓ Completely free (first 2,000 minutes/month)  
✓ No server to maintain  
✓ Automatic retries on failure  
✓ Easy scheduling  
✓ Built-in logging  
✓ Runs on GitHub's infrastructure  

## Limitations

- Runs stop if there are no commits for 60 days (auto-resume when you push)
- Each run has a ~6 minute setup overhead (GitHub Actions container startup)
- Best for checks every 15+ minutes (not ideal for every 5 minutes)
