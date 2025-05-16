🤖 Telegram Bot — Local Development Guide
This guide explains how to run your Telegram bot locally using Python, Flask, and aiogram.

Install dependencies

pip install -r requirements.txt

pip install aiogram flask


Run the bot using:

python bot.py

You should see output like this:
 * Running on http://127.0.0.1:8080
 * Running on http://192.168.xxx.xxx:8080
✅ Your bot is now running locally!

---------------------------------------------------------------


🚀 Deploying a Telegram Bot to Fly.io


✅ Prerequisites
A Fly.io account: Sign up here

Fly CLI installed:
👉 Install via:
macOS/Linux:

curl -L https://fly.io/install.sh | sh
Windows: Use this installer

Docker installed (required by Fly)

Your bot code ready (bot.py, .env, etc.)

Telegram bot token


Deploy
Now deploy your app:

fly deploy