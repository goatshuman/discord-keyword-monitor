import os
import json
import asyncio
import time
import discord
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
USER_TOKEN = os.environ["DISCORD_USER_TOKEN"]
USER_ID = int(os.environ["DISCORD_USER_ID"])
MONITOR_CHANNELS = [
    int(c.strip())
    for c in os.environ.get("MONITOR_CHANNELS", "1276133271322886270,1276133271322886271").split(",")
    if c.strip()
]

KEYWORDS_FILE = "keywords.json"
DM_CHANNEL_FILE = "dm_channel.json"
AVAILABLE_TTL = 3600 * 6  # game stays "available" in status for 6 hours


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
available_games: dict[str, float] = {}  # keyword -> timestamp when last seen

alert_queue: asyncio.Queue = asyncio.Queue()


# ──────────────────────────────────────────
# BOT CLIENT  (bot token — DMs + presence)
# ──────────────────────────────────────────

bot_intents = discord.Intents.default()
bot_intents.message_content = True
bot_client = discord.Client(intents=bot_intents)


async def ensure_dm_channel() -> str | None:
    global dm_channel_id
    if dm_channel_id:
        return dm_channel_id

    bot_id = str(bot_client.user.id)

    user_client_ref = discord.Client(intents=discord.Intents.default())

    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://discord.com/api/v10/users/@me/channels",
            headers={"Authorization": USER_TOKEN, "Content-Type": "application/json"},
            json={"recipient_id": bot_id},
        ) as res:
            data = await res.json()
            if res.status == 200 and data.get("id"):
                dm_channel_id = data["id"]
                save_json(DM_CHANNEL_FILE, {"channel_id": dm_channel_id})
                print(f"DM channel ready: {dm_channel_id}")
                return dm_channel_id
            print(f"Failed to open DM channel: {res.status} {data}")
            return None


async def send_bot_dm(embed: discord.Embed):
    channel_id = await ensure_dm_channel()
    if not channel_id:
        return
    import aiohttp
    payload = {
        "embeds": [{
            "title": embed.title,
            "description": embed.description,
            "color": embed.colour.value if embed.colour else 0x57F287,
            "fields": [{"name": f.name, "value": f.value, "inline": f.inline} for f in embed.fields],
        }]
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
            json=payload,
        ) as res:
            if res.status not in (200, 201):
                data = await res.json()
                print(f"DM send error: {res.status} {data}")


async def update_bot_presence():
    now = time.time()
    active = {kw: ts for kw, ts in available_games.items() if now - ts < AVAILABLE_TTL}
    available_games.clear()
    available_games.update(active)

    if active:
        names = ", ".join(active.keys())
        activity = discord.Activity(
            type=discord.ActivityType.playing,
            name=f"🟢 {names} — AVAILABLE!",
        )
        status = discord.Status.online
    elif keywords:
        activity = discord.Activity(
            type=discord.ActivityType.watching,
            name=f"{'  |  '.join(keywords[:3])}{'  ...' if len(keywords) > 3 else ''}",
        )
        status = discord.Status.idle
    else:
        activity = discord.Activity(
            type=discord.ActivityType.watching,
            name="for game drops 👀",
        )
        status = discord.Status.idle

    await bot_client.change_presence(status=status, activity=activity)


async def process_alerts():
    await bot_client.wait_until_ready()
    while True:
        alert = await alert_queue.get()
        matched: list[str] = alert["matched"]
        message_data: dict = alert["message"]

        for kw in matched:
            available_games[kw] = time.time()

        await update_bot_presence()

        embed = discord.Embed(
            title="🚨 Game Alert — Now Available!",
            description=message_data["content"][:2000],
            color=discord.Color.green(),
            url=message_data["jump_url"],
        )
        embed.add_field(name="Keywords", value=", ".join(f"**{kw}**" for kw in matched), inline=False)
        embed.add_field(name="Server", value=message_data["server"], inline=True)
        embed.add_field(name="Channel", value=message_data["channel"], inline=True)
        embed.add_field(name="Posted by", value=message_data["author"], inline=True)
        embed.add_field(name="Jump Link", value=f"[Go to message]({message_data['jump_url']})", inline=False)

        await send_bot_dm(embed)
        print(f"Alerted: {matched}")


@bot_client.event
async def on_ready():
    print(f"[BOT] Logged in as {bot_client.user} ({bot_client.user.id})")
    await ensure_dm_channel()
    await update_bot_presence()

    embed = discord.Embed(
        title="✅ Bot is online!",
        description=(
            f"Monitoring **#{', #'.join(str(c) for c in MONITOR_CHANNELS)}**\n\n"
            + (
                "Currently watching for:\n" + "\n".join(f"• {kw}" for kw in keywords)
                if keywords
                else "No keywords yet — just DM me any game name to start watching."
            )
            + "\n\n**Commands:** `!list` · `!remove <kw>` · `!clear` · `!status`"
        ),
        color=discord.Color.green(),
    )
    await send_bot_dm(embed)


@bot_client.event
async def on_message(message: discord.Message):
    if message.author == bot_client.user:
        return
    if isinstance(message.channel, discord.DMChannel) and message.author.id == USER_ID:
        await handle_command(message)


async def handle_command(message: discord.Message):
    global keywords
    text = message.content.strip()

    if text.lower() in ("!help", "help"):
        embed = discord.Embed(
            title="📖 Bot Commands",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Adding & removing keywords",
            value=(
                "`<game name>` — Add a keyword to the watchlist. Bot instantly scans the full channel history and DMs you if it's already posted.\n"
                "`!remove <keyword>` — Stop watching for a specific keyword.\n"
                "`!clear` — Remove every keyword at once."
            ),
            inline=False,
        )
        embed.add_field(
            name="Checking status",
            value=(
                "`!list` — Show all keywords you're currently watching.\n"
                "`!status` — Show each keyword with 🟢 (seen recently) or 🔴 (not seen yet)."
            ),
            inline=False,
        )
        embed.add_field(
            name="This message",
            value="`!help` — Show this help card.",
            inline=False,
        )
        embed.set_footer(text="The bot monitors both channels 24/7 and DMs you the moment a keyword appears.")
        await message.channel.send(embed=embed)
        return

    if text.lower() == "!list":
        if keywords:
            await message.channel.send("**Watching for:**\n" + "\n".join(f"• {kw}" for kw in keywords))
        else:
            await message.channel.send("No keywords set. Send me a game name to add it.")
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
        if lines:
            await message.channel.send("**Game status:**\n" + "\n".join(lines))
        else:
            await message.channel.send("No keywords set.")
        return

    if text.lower() == "!clear":
        keywords = []
        save_json(KEYWORDS_FILE, keywords)
        await update_bot_presence()
        await message.channel.send("✅ All keywords cleared.")
        return

    if text.lower().startswith("!remove "):
        target = text[8:].strip().lower()
        before = len(keywords)
        keywords = [kw for kw in keywords if kw.lower() != target]
        if len(keywords) < before:
            save_json(KEYWORDS_FILE, keywords)
            available_games.pop(target, None)
            await update_bot_presence()
            await message.channel.send(f"✅ Removed **{target}**.")
        else:
            await message.channel.send(f"❌ **{target}** not found.")
        return

    if text.startswith("!"):
        await message.channel.send("Commands: `!list` · `!remove <keyword>` · `!clear` · `!status`")
        return

    kw = text.strip()
    if kw.lower() in [k.lower() for k in keywords]:
        await message.channel.send(f"**{kw}** is already in the watchlist.")
        return

    keywords.append(kw)
    save_json(KEYWORDS_FILE, keywords)
    await update_bot_presence()
    await message.channel.send(
        f"✅ Added **{kw}**. Now watching {len(keywords)} keyword(s). Use `!list` to see all.\n"
        f"🔍 Scanning recent channel history for **{kw}**..."
    )
    await check_history_for_keyword(kw)


async def check_history_for_keyword(kw: str):
    """Scan the last 50 messages in each monitored channel for kw right after it's added."""
    found_any = False
    for channel_id in MONITOR_CHANNELS:
        channel = user_client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await user_client.fetch_channel(channel_id)
            except Exception as e:
                print(f"Cannot fetch channel {channel_id}: {e}")
                continue

        try:
            async for msg in channel.history(limit=None):
                if kw.lower() in msg.content.lower():
                    found_any = True
                    available_games[kw] = time.time()
                    await update_bot_presence()

                    server = msg.guild.name if msg.guild else "Unknown"
                    channel_name = getattr(channel, "name", str(channel_id))

                    embed = discord.Embed(
                        title="⚠️ Already Available! — Found in Recent History",
                        description=msg.content[:2000],
                        color=discord.Color.yellow(),
                        url=msg.jump_url,
                    )
                    embed.add_field(name="Keyword", value=f"**{kw}**", inline=True)
                    embed.add_field(name="Server", value=server, inline=True)
                    embed.add_field(name="Channel", value=f"#{channel_name}", inline=True)
                    embed.add_field(name="Posted by", value=str(msg.author), inline=True)
                    embed.add_field(name="Posted at", value=f"<t:{int(msg.created_at.timestamp())}:R>", inline=True)
                    embed.add_field(name="Jump Link", value=f"[Go to message]({msg.jump_url})", inline=False)
                    embed.set_footer(text="This message was already in the channel before you added the keyword.")

                    await send_bot_dm(embed)
                    print(f"History match found for '{kw}' in #{channel_name}")
                    break  # one match per channel is enough
        except Exception as e:
            print(f"Error reading history for channel {channel_id}: {e}")

    if not found_any:
        channel_id = await ensure_dm_channel()
        if channel_id:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                    headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
                    json={"content": f"🔍 No recent mention of **{kw}** found in the last 50 messages. I'll alert you the moment it appears!"},
                )


# ──────────────────────────────────────────
# USER CLIENT  (user token — reads channels)
# ──────────────────────────────────────────

user_intents = discord.Intents.default()
user_intents.message_content = True
user_client = discord.Client(intents=user_intents)


@user_client.event
async def on_ready():
    print(f"[USER] Logged in as {user_client.user} — monitoring {len(MONITOR_CHANNELS)} channels")


@user_client.event
async def on_message(message: discord.Message):
    if message.channel.id not in MONITOR_CHANNELS:
        return
    if not keywords:
        return

    content_lower = message.content.lower()
    matched = [kw for kw in keywords if kw.lower() in content_lower]
    if not matched:
        return

    server = message.guild.name if message.guild else "Unknown"
    channel = getattr(message.channel, "name", str(message.channel.id))

    await alert_queue.put({
        "matched": matched,
        "message": {
            "content": message.content,
            "server": server,
            "channel": f"#{channel}",
            "author": str(message.author),
            "jump_url": message.jump_url,
        },
    })


async def run_all():
    await asyncio.gather(
        bot_client.start(BOT_TOKEN),
        user_client.start(USER_TOKEN, bot=False),
        process_alerts(),
    )


def run_bot():
    asyncio.run(run_all())
