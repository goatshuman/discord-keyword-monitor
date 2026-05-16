# Discord Keyword Monitor Bot

Monitors specific Discord channels for keywords and sends you a DM whenever one is spotted — including the full forwarded message.

## Features

- Watches configurable channels for keywords (case-insensitive)
- Supports multiple keywords and phrases
- Forwards the full message with a direct jump link
- Manage keywords by DMing the bot — no config file edits needed
- Keywords saved to disk and survive restarts
- Built-in keepalive web server at `/` and `/health` for uptime monitors (e.g. UptimeRobot)

## How to add keywords

Just DM the bot any word or phrase and it starts watching for it immediately.

| Command | What it does |
|---|---|
| `pragmata` | Add "pragmata" to the watchlist |
| `gta6` | Add "gta6" to the watchlist |
| `!list` | Show all current keywords |
| `!remove gta6` | Remove a specific keyword |
| `!clear` | Remove all keywords |

## Setup

### 1. Environment Variables

Set these on Render (or copy `.env.example` to `.env` for local dev):

| Variable | Description |
|---|---|
| `DISCORD_BOT_TOKEN` | Your Discord bot token |
| `DISCORD_USER_TOKEN` | Your Discord user token (used to open DM channel) |
| `DISCORD_USER_ID` | Your Discord user ID (who receives DMs) |
| `MONITOR_CHANNELS` | Comma-separated channel IDs to monitor |

### 2. Discord Bot Setup

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Select your bot → **Bot** tab
3. Enable **Message Content Intent** under Privileged Gateway Intents
4. Make sure **Require OAuth2 Code Grant** is **OFF**
5. The bot does NOT need to be in the server you're monitoring

### 3. Deploy on Render

- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python main.py`
- **Environment:** Python 3

Add all environment variables from the table above in the Render dashboard under **Environment**.

## Local Development

```bash
cp .env.example .env
# Fill in your values in .env
pip install -r requirements.txt
python main.py
```
