import os
import json
import discord
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
USER_ID = int(os.environ["DISCORD_USER_ID"])
MONITOR_CHANNELS = [
    int(c.strip())
    for c in os.environ.get("MONITOR_CHANNELS", "1276133271322886270,1276133271322886271").split(",")
    if c.strip()
]

KEYWORDS_FILE = "keywords.json"


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


keywords: list[str] = load_keywords()

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"Logged in as {client.user} (ID: {client.user.id})")
    print(f"Monitoring channels: {MONITOR_CHANNELS}")
    print(f"Loaded keywords: {keywords}")

    owner = await client.fetch_user(USER_ID)
    if owner:
        if keywords:
            kw_list = "\n".join(f"• {kw}" for kw in keywords)
            await owner.send(
                f"✅ **Bot is online!**\n\nCurrently watching for:\n{kw_list}\n\n"
                "Send me a keyword to add it. Use `!list`, `!remove <keyword>`, or `!clear` to manage them."
            )
        else:
            await owner.send(
                "✅ **Bot is online!**\n\nNo keywords set yet. Just send me a word or phrase and I'll watch for it in the monitored channels.\n\n"
                "Commands:\n`!list` — show all keywords\n`!remove <keyword>` — remove a keyword\n`!clear` — remove all keywords"
            )


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

    owner = await client.fetch_user(USER_ID)
    if owner is None:
        return

    server_name = message.guild.name if message.guild else "Unknown Server"
    channel_name = getattr(message.channel, "name", str(message.channel.id))

    embed = discord.Embed(
        title="🚨 Keyword Alert — Game Available!",
        description=message.content[:2000],
        color=discord.Color.green(),
        url=message.jump_url,
    )
    embed.add_field(
        name="Matched Keywords",
        value=", ".join(f"**{kw}**" for kw in matched),
        inline=False,
    )
    embed.add_field(name="Server", value=server_name, inline=True)
    embed.add_field(name="Channel", value=f"#{channel_name}", inline=True)
    embed.add_field(name="Posted by", value=str(message.author), inline=True)
    embed.add_field(name="Jump Link", value=f"[Go to message]({message.jump_url})", inline=False)

    if message.attachments:
        embed.set_image(url=message.attachments[0].url)

    try:
        await owner.send(embed=embed)
        print(f"Alerted for keyword(s): {matched} in #{channel_name}")
    except discord.Forbidden:
        print(f"Cannot DM user {USER_ID}")
    except Exception as e:
        print(f"Error sending DM: {e}")


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

    keyword = text.lower()
    if keyword in [kw.lower() for kw in keywords]:
        await message.channel.send(f"**{text}** is already in the watchlist.")
        return

    keywords.append(text)
    save_keywords(keywords)
    await message.channel.send(
        f"✅ Added **{text}** to watchlist.\n\nNow watching for {len(keywords)} keyword(s). "
        f"Use `!list` to see all."
    )


def run_bot():
    client.run(BOT_TOKEN)
