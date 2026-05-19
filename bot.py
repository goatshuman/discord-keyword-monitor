import os
import json
import asyncio
import time
import aiohttp
import discord
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
USER_TOKEN = os.environ["DISCORD_USER_TOKEN"]
USER_ID = int(os.environ["DISCORD_USER_ID"])
BOT_ID = 1505161323091198094
MONITOR_CHANNELS = [
    int(c.strip())
    for c in os.environ.get("MONITOR_CHANNELS", "1276133271322886270,1276133271322886271").split(",")
    if c.strip()
]

KEYWORDS_FILE = "keywords.json"
DM_CHANNEL_FILE = "dm_channel.json"
AVAILABLE_TTL = 3600 * 6  # 6 hours

DISCORD_API = "https://discord.com/api/v10"


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


keywords: list[str] = load_json(KEYWORDS_FILE, [])
dm_channel_id: str | None = load_json(DM_CHANNEL_FILE, {}).get("channel_id")
available_games: dict[str, float] = {}


# ─────────────────────────────────────────────
# REST helpers — all bot actions go via aiohttp
# ─────────────────────────────────────────────

async def ensure_dm_channel() -> str | None:
    global dm_channel_id
    if dm_channel_id:
        return dm_channel_id
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{DISCORD_API}/users/@me/channels",
            headers={"Authorization": USER_TOKEN, "Content-Type": "application/json"},
            json={"recipient_id": str(BOT_ID)},
        ) as res:
            data = await res.json()
            if res.status == 200 and data.get("id"):
                dm_channel_id = data["id"]
                save_json(DM_CHANNEL_FILE, {"channel_id": dm_channel_id})
                print(f"DM channel ready: {dm_channel_id}")
                return dm_channel_id
            print(f"DM channel error: {res.status} {data}")
            return None


async def bot_send(payload: dict):
    """Send a message to the DM channel using the bot token."""
    channel_id = await ensure_dm_channel()
    if not channel_id:
        return
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
            json=payload,
        ) as res:
            if res.status not in (200, 201):
                print(f"Send error: {res.status} {await res.json()}")


async def send_text(text: str):
    await bot_send({"content": text})


async def send_embed(
    title: str,
    description: str,
    color: int,
    fields: list[dict] | None = None,
    footer: str | None = None,
    url: str | None = None,
):
    embed: dict = {"title": title, "description": description[:4096], "color": color}
    if fields:
        embed["fields"] = fields
    if footer:
        embed["footer"] = {"text": footer}
    if url:
        embed["url"] = url
    await bot_send({"embeds": [embed]})


async def update_presence():
    """Update the bot's Discord status via REST gateway (best-effort)."""
    now = time.time()
    for kw in list(available_games):
        if now - available_games[kw] >= AVAILABLE_TTL:
            available_games.pop(kw)

    active = list(available_games.keys())
    if active:
        name = f"🟢 {', '.join(active)} — AVAILABLE!"
        atype = 0  # Playing
        status = "online"
    elif keywords:
        name = "  |  ".join(keywords[:3]) + ("  ..." if len(keywords) > 3 else "")
        atype = 3  # Watching
        status = "idle"
    else:
        name = "for game drops 👀"
        atype = 3
        status = "idle"

    # Update via bot REST — presence updates require gateway but we can
    # set the bot's activity by patching the application if needed.
    # For now log it; gateway-based presence is set on connect in on_ready via user client.
    print(f"[Presence] {status}: {name}")


# ─────────────────────────────────────────────
# USER CLIENT  (discord.py-self, user token)
# Does: channel monitoring + command handling
# ─────────────────────────────────────────────

client = discord.Client()
_welcomed = False


@client.event
async def on_ready():
    global _welcomed
    print(f"[CLIENT] Logged in as {client.user}")
    await ensure_dm_channel()

    if not _welcomed:
        _welcomed = True
        kw_text = (
            "Currently watching for:\n" + "\n".join(f"• {kw}" for kw in keywords)
            if keywords
            else "No keywords yet — DM me any game name to start watching."
        )
        await send_embed(
            title="✅ Bot is online!",
            description=f"{kw_text}\n\n**Commands:** `!list` · `!status` · `!remove <kw>` · `!clear` · `!help`",
            color=0x57F287,
        )


@client.event
async def on_message(message: discord.Message):
    # Command: user sends a message in the DM with the bot
    # Check if it's a DM where the user (not the bot) is the author
    if isinstance(message.channel, discord.DMChannel) and message.author.id == USER_ID:
        print(f"[CMD] Received: {message.content!r}")
        await handle_command(message.content.strip())
        return

    # Alert: message in a monitored channel
    if message.channel.id not in MONITOR_CHANNELS:
        return
    if not keywords:
        return

    content_lower = message.content.lower()
    matched = [kw for kw in keywords if kw.lower() in content_lower]
    if not matched:
        return

    for kw in matched:
        available_games[kw] = time.time()
    await update_presence()

    server = message.guild.name if message.guild else "Unknown"
    ch_name = getattr(message.channel, "name", str(message.channel.id))
    await send_embed(
        title="🚨 Game Alert — Now Available!",
        description=message.content[:2000],
        color=0x57F287,
        fields=[
            {"name": "Keywords", "value": ", ".join(f"**{kw}**" for kw in matched), "inline": False},
            {"name": "Server", "value": server, "inline": True},
            {"name": "Channel", "value": f"#{ch_name}", "inline": True},
            {"name": "Posted by", "value": str(message.author), "inline": True},
            {"name": "Jump Link", "value": f"[Go to message]({message.jump_url})", "inline": False},
        ],
        url=message.jump_url,
    )
    print(f"Alert sent: {matched} in #{ch_name}")


# ─────────────────────────────────────────────
# Command handling
# ─────────────────────────────────────────────

async def handle_command(text: str):
    global keywords

    if text.lower() in ("!help", "help"):
        await send_embed(
            title="📖 Bot Commands",
            description=(
                "**Adding & removing keywords**\n"
                "`<game name>` — Add a keyword. Bot instantly scans the full channel history.\n"
                "`!remove <keyword>` — Stop watching for a keyword.\n"
                "`!clear` — Remove all keywords.\n\n"
                "**Checking status**\n"
                "`!list` — Show all keywords you're watching.\n"
                "`!status` — Show 🟢/🔴 for each keyword.\n\n"
                "**This message**\n"
                "`!help` — Show this help card."
            ),
            color=0x5865F2,
            footer="The bot monitors your channels 24/7 and DMs you the moment a keyword appears.",
        )
        return

    if text.lower() == "!list":
        if keywords:
            await send_text("**Watching for:**\n" + "\n".join(f"• {kw}" for kw in keywords))
        else:
            await send_text("No keywords set. Send me a game name to add it.")
        return

    if text.lower() == "!status":
        now = time.time()
        lines = []
        for kw in keywords:
            ts = available_games.get(kw)
            if ts and now - ts < AVAILABLE_TTL:
                mins = int((now - ts) / 60)
                lines.append(f"🟢 **{kw}** — seen {mins}m ago")
            else:
                lines.append(f"🔴 **{kw}** — not seen yet")
        await send_text("**Game status:**\n" + "\n".join(lines) if lines else "No keywords set.")
        return

    if text.lower() == "!clear":
        keywords = []
        save_json(KEYWORDS_FILE, keywords)
        await update_presence()
        await send_text("✅ All keywords cleared.")
        return

    if text.lower().startswith("!remove "):
        target = text[8:].strip().lower()
        before = len(keywords)
        keywords = [kw for kw in keywords if kw.lower() != target]
        if len(keywords) < before:
            save_json(KEYWORDS_FILE, keywords)
            available_games.pop(target, None)
            await update_presence()
            await send_text(f"✅ Removed **{target}**.")
        else:
            await send_text(f"❌ **{target}** not in the watchlist.")
        return

    if text.startswith("!"):
        await send_text("Commands: `!list` · `!status` · `!remove <keyword>` · `!clear` · `!help`")
        return

    kw = text
    if kw.lower() in [k.lower() for k in keywords]:
        await send_text(f"**{kw}** is already in the watchlist.")
        return

    keywords.append(kw)
    save_json(KEYWORDS_FILE, keywords)
    await update_presence()
    await send_text(
        f"✅ Added **{kw}**. Watching {len(keywords)} keyword(s).\n"
        f"🔍 Scanning the full channel history for **{kw}**..."
    )
    asyncio.create_task(check_history_for_keyword(kw))


async def check_history_for_keyword(kw: str):
    found_any = False
    for channel_id in MONITOR_CHANNELS:
        channel = client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(channel_id)
            except Exception as e:
                print(f"Cannot fetch channel {channel_id}: {e}")
                continue

        try:
            async for msg in channel.history(limit=None):
                if kw.lower() in msg.content.lower():
                    found_any = True
                    available_games[kw] = time.time()
                    await update_presence()

                    server = msg.guild.name if msg.guild else "Unknown"
                    ch_name = getattr(channel, "name", str(channel_id))
                    await send_embed(
                        title="⚠️ Already Available! — Found in Channel History",
                        description=msg.content[:2000],
                        color=0xFEE75C,
                        fields=[
                            {"name": "Keyword", "value": f"**{kw}**", "inline": True},
                            {"name": "Server", "value": server, "inline": True},
                            {"name": "Channel", "value": f"#{ch_name}", "inline": True},
                            {"name": "Posted by", "value": str(msg.author), "inline": True},
                            {"name": "Posted at", "value": f"<t:{int(msg.created_at.timestamp())}:R>", "inline": True},
                            {"name": "Jump Link", "value": f"[Go to message]({msg.jump_url})", "inline": False},
                        ],
                        footer="This message was already in the channel before you added the keyword.",
                        url=msg.jump_url,
                    )
                    print(f"History match: '{kw}' in #{ch_name}")
                    break
        except Exception as e:
            print(f"Error reading history for {channel_id}: {e}")

    if not found_any:
        await send_text(
            f"🔍 No mention of **{kw}** found in the full channel history. I'll alert you the moment it appears!"
        )


def run_bot():
    client.run(USER_TOKEN)
