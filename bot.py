import os
import discord
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
USER_ID = int(os.environ["DISCORD_USER_ID"])
KEYWORDS = [k.strip().lower() for k in os.environ.get("KEYWORDS", "").split(",") if k.strip()]
MONITOR_CHANNELS = [int(c.strip()) for c in os.environ.get("MONITOR_CHANNELS", "1276133271322886270,1276133271322886271").split(",") if c.strip()]

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"Logged in as {client.user} (ID: {client.user.id})")
    print(f"Monitoring channels: {MONITOR_CHANNELS}")
    print(f"Watching keywords: {KEYWORDS}")
    print("Bot is ready and watching for keywords.")


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return

    if message.channel.id not in MONITOR_CHANNELS:
        return

    content_lower = message.content.lower()
    matched_keywords = [kw for kw in KEYWORDS if kw in content_lower]

    if not matched_keywords:
        return

    target_user = await client.fetch_user(USER_ID)
    if target_user is None:
        print(f"Could not find user {USER_ID}")
        return

    channel_mention = f"<#{message.channel.id}>"
    server_name = message.guild.name if message.guild else "Unknown Server"
    jump_url = message.jump_url

    keywords_found = ", ".join(f"**{kw}**" for kw in matched_keywords)

    dm_embed = discord.Embed(
        title="🎮 Keyword Alert!",
        description=f"A keyword was spotted in {channel_mention}",
        color=discord.Color.green(),
    )
    dm_embed.add_field(name="Keywords Found", value=keywords_found, inline=False)
    dm_embed.add_field(name="Server", value=server_name, inline=True)
    dm_embed.add_field(name="Channel", value=f"#{message.channel.name}", inline=True)
    dm_embed.add_field(name="Sent by", value=str(message.author), inline=True)
    dm_embed.add_field(name="Message", value=message.content[:1024] or "(empty)", inline=False)
    dm_embed.add_field(name="Jump to Message", value=f"[Click here]({jump_url})", inline=False)
    dm_embed.set_footer(text=f"Channel ID: {message.channel.id}")

    try:
        await target_user.send(embed=dm_embed)
        print(f"DM sent for keyword(s) '{', '.join(matched_keywords)}' in channel {message.channel.id}")
    except discord.Forbidden:
        print(f"Cannot DM user {USER_ID} — they may have DMs disabled.")
    except Exception as e:
        print(f"Error sending DM: {e}")


def run_bot():
    client.run(BOT_TOKEN)
