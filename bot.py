import os
import json
import asyncio
import aiohttp
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

DISCORD_API = "https://discord.com/api/v10"


def load_keywords() -> list[str]:
    if os.path.exists(KEYWORDS_FILE):
        try:
            with open(KEYWORDS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_keywords(keywords: list[str]):
    with open(KEYWORDS_FILE, "w") as f:
        json.dump(keywords, f, indent=2)


def load_dm_channel() -> str | None:
    if os.path.exists(DM_CHANNEL_FILE):
        try:
            with open(DM_CHANNEL_FILE, "r") as f:
                return json.load(f).get("channel_id")
        except Exception:
            pass
    return None


def save_dm_channel(channel_id: str):
    with open(DM_CHANNEL_FILE, "w") as f:
        json.dump({"channel_id": channel_id}, f)


keywords: list[str] = load_keywords()
dm_channel_id: str | None = load_dm_channel()

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


async def ensure_dm_channel() -> str | None:
    global dm_channel_id
    if dm_channel_id:
        return dm_channel_id

    bot_id = str(client.user.id)
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{DISCORD_API}/users/@me/channels",
            headers={"Authorization": USER_TOKEN, "Content-Type": "application/json"},
            json={"recipient_id": bot_id},
        ) as res:
            data = await res.json()
            if res.status == 200 and data.get("id"):
                dm_channel_id = data["id"]
                save_dm_channel(dm_channel_id)
                print(f"DM channel established: {dm_channel_id}")
                return dm_channel_id
            else:
                print(f"Failed to open DM channel: {res.status} {data}")
                return None


async def send_dm(embed: discord.Embed):
    channel_id = await ensure_dm_channel()
    if not channel_id:
        print("Cannot send DM — no channel available.")
        return

    channel = client.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await client.fetch_channel(int(channel_id))
        except Exception:
            pass

    if channel:
        await channel.send(embed=embed)
    else:
        async with aiohttp.ClientSession() as session:
            payload = {
                "embeds": [
                    {
                        "title": embed.title,
                        "description": embed.description,
                        "color": embed.colour.value if embed.colour else 0x57F287,
                        "fields": [{"name": f.name, "value": f.value, "inline": f.inline} for f in embed.fields],
                    }
                ]
            }
            async with session.post(
                f"{DISCORD_API}/channels/{channel_id}/messages",
                headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
                json=payload,
            ) as res:
                if res.status not in (200, 201):
                    data = await res.json()
                    print(f"Failed to send message: {res.status} {data}")


@client.event
async def on_ready():
    print(f"Logged in as {client.user} (ID: {client.user.id})")
    print(f"Monitoring channels: {MONITOR_CHANNELS}")
    print(f"Loaded keywords: {keywords}")

    channel_id = await ensure_dm_channel()
    if not channel_id:
        print("WARNING: Could not establish DM channel.")
        return

    if keywords:
        kw_list = "\n".join(f"• {kw}" for kw in keywords)
        embed = discord.Embed(
            title="✅ Bot is online!",
            description=f"Currently watching for:\n{kw_list}\n\nSend me a keyword to add more. Use `!list`, `!remove <keyword>`, or `!clear` to manage them.",
            color=discord.Color.green(),
        )
    else:
        embed = discord.Embed(
            title="✅ Bot is online!",
            description="No keywords set yet. Just send me a word or phrase and I'll watch for it.\n\n**Commands:**\n`!list` — show all keywords\n`!remove <keyword>` — remove one\n`!clear` — remove all",
            color=discord.Color.green(),
        )

    await send_dm(embed)


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return

    if isinstance(message.channel, discord.DMChannel) and message.author.id == USER_ID:
        await handle_dm_command(message)
        return

    if message.channel.id not in MONITOR_CHANNELS:
        return

    if not keywords:
        return

    content_lower = message.content.lower()
    matched = [kw for kw in keywords if kw.lower() in content_lower]

    if not matched:
        return

    server_name = message.guild.name if message.guild else "Unknown Server"
    channel_name = getattr(message.channel, "name", str(message.channel.id))

    embed = discord.Embed(
        title="🚨 Keyword Alert — Game Available!",
        description=message.content[:2000],
        color=discord.Color.green(),
        url=message.jump_url,
    )
    embed.add_field(name="Matched Keywords", value=", ".join(f"**{kw}**" for kw in matched), inline=False)
    embed.add_field(name="Server", value=server_name, inline=True)
    embed.add_field(name="Channel", value=f"#{channel_name}", inline=True)
    embed.add_field(name="Posted by", value=str(message.author), inline=True)
    embed.add_field(name="Jump Link", value=f"[Go to message]({message.jump_url})", inline=False)

    if message.attachments:
        embed.set_image(url=message.attachments[0].url)

    await send_dm(embed)
    print(f"Alerted for keyword(s): {matched} in #{channel_name}")


async def handle_dm_command(message: discord.Message):
    global keywords
    text = message.content.strip()

    if text.lower() == "!list":
        if keywords:
            kw_list = "\n".join(f"• {kw}" for kw in keywords)
            await message.channel.send(f"**Watching for ({len(keywords)}):**\n{kw_list}")
        else:
            await message.channel.send("No keywords set. Send me any word or phrase to add it.")
        return

    if text.lower() == "!clear":
        keywords = []
        save_keywords(keywords)
        await message.channel.send("✅ All keywords cleared.")
        return

    if text.lower().startswith("!remove "):
        target = text[8:].strip().lower()
        before = len(keywords)
        keywords = [kw for kw in keywords if kw.lower() != target]
        if len(keywords) < before:
            save_keywords(keywords)
            await message.channel.send(f"✅ Removed **{target}** from watchlist.")
        else:
            await message.channel.send(f"❌ **{target}** wasn't in the watchlist.")
        return

    if text.startswith("!"):
        await message.channel.send(
            "Commands:\n`!list` — show all keywords\n`!remove <keyword>` — remove one\n`!clear` — remove all"
        )
        return

    keyword = text.strip()
    if keyword.lower() in [kw.lower() for kw in keywords]:
        await message.channel.send(f"**{keyword}** is already in the watchlist.")
        return

    keywords.append(keyword)
    save_keywords(keywords)
    await message.channel.send(
        f"✅ Added **{keyword}** to watchlist.\n\nNow watching {len(keywords)} keyword(s). Use `!list` to see all."
    )


def run_bot():
    client.run(BOT_TOKEN)
