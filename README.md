# Discord Keyword Monitor Bot

Monitors specific Discord channels for keywords and sends you a DM whenever one is spotted — including the full forwarded message.

## Features

- Watches configurable channels for keywords (case-insensitive)
- Supports multiple keywords at once
- Forwards the full message with a jump link directly to it
- Built-in keepalive web server at `/` and `/health` for uptime monitors (e.g. UptimeRobot)

## Setup

### 1. Environment Variables

Set these on Render (or copy `.env.example` to `.env` for local dev):

| Variable | Description |
|---|---|
| `DISCORD_BOT_TOKEN` | Your Discord bot token |
| `DISCORD_USER_ID` | Your Discord user ID (who receives DMs) |
| `KEYWORDS` | Comma-separated keywords to watch (e.g. `free,drop,available`) |
| `MONITOR_CHANNELS` | Comma-separated channel IDs to monitor |

### 2. Discord Bot Setup

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Select your bot → **Bot** tab
3. Enable **Message Content Intent** under Privileged Gateway Intents
4. Invite the bot to your server with `Read Messages` + `Send Messages` permissions

### 3. Deploy on Render

- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python main.py`
- **Environment:** Python 3

Add all the environment variables from the table above in the Render dashboard under **Environment**.

## Local Development

```bash
cp .env.example .env
# Fill in your values in .env
pip install -r requirements.txt
python main.py
```
