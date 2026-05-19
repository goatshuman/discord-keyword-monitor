import os
import json
import asyncio
import time
import aiohttp
import discord
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN  = os.environ["DISCORD_BOT_TOKEN"]
USER_TOKEN = os.environ["DISCORD_USER_TOKEN"]
USER_ID    = int(os.environ["DISCORD_USER_ID"])
BOT_ID     = 1505161323091198094
MONITOR_CHANNELS = [
    int(c.strip())
    for c in os.environ.get("MONITOR_CHANNELS", "1276133271322886270,1276133271322886271").split(",")
    if c.strip()
]

KEYWORDS_FILE   = "keywords.json"
DM_CHANNEL_FILE = "dm_channel.json"
AVAILABLE_TTL   = 3600 * 6  # 6 hours

DISCORD_API = "https://discord.com/api/v10"
# DIRECT_MESSAGES (1<<12). MESSAGE_CONTENT privileged not needed for DMs.
BOT_INTENTS = (1 << 12)

# Global gateway reference so command handlers can push presence updates
bot_gw: "BotGateway | None" = None


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


keywords: list[str]               = load_json(KEYWORDS_FILE, [])
dm_channel_id: str | None         = load_json(DM_CHANNEL_FILE, {}).get("channel_id")
available_games: dict[str, float] = {}
alerted_message_ids: set[int]     = set()  # prevents double-alerting on message edits


# ─────────────────────────────────────────────
# Embed text extractor
# Channel posts are Discord embeds with empty content — read all embed fields
# ─────────────────────────────────────────────

def extract_text(message: discord.Message) -> str:
    """Return all searchable text from a message including embed titles/descriptions/fields."""
    parts: list[str] = []
    if message.content:
        parts.append(message.content)
    for embed in message.embeds:
        if embed.title:
            parts.append(embed.title)
        if embed.description:
            parts.append(embed.description)
        if embed.author and embed.author.name:
            parts.append(embed.author.name)
        for field in embed.fields:
            if field.name:
                parts.append(field.name)
            if field.value:
                parts.append(field.value)
        if embed.footer and embed.footer.text:
            parts.append(embed.footer.text)
    return " ".join(parts)


def extract_text_from_data(data: dict) -> str:
    """Same as extract_text but works on raw message dict (REST API response)."""
    parts: list[str] = []
    if data.get("content"):
        parts.append(data["content"])
    for embed in data.get("embeds", []):
        if embed.get("title"):
            parts.append(embed["title"])
        if embed.get("description"):
            parts.append(embed["description"])
        if embed.get("author", {}).get("name"):
            parts.append(embed["author"]["name"])
        for field in embed.get("fields", []):
            if field.get("name"):
                parts.append(field["name"])
            if field.get("value"):
                parts.append(field["value"])
        if embed.get("footer", {}).get("text"):
            parts.append(embed["footer"]["text"])
    return " ".join(parts)


def message_summary(message: discord.Message) -> str:
    """One-line human-readable summary of a message for the alert embed."""
    if message.content:
        return message.content[:2000]
    for embed in message.embeds:
        lines = []
        if embed.title:
            lines.append(f"**{embed.title}**")
        if embed.description:
            lines.append(embed.description[:500])
        for field in embed.fields:
            lines.append(f"**{field.name}:** {field.value}")
        if lines:
            return "\n".join(lines)[:2000]
    return "(no text content)"


# ─────────────────────────────────────────────
# REST helpers
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
                print(f"[REST] DM channel ready: {dm_channel_id}")
                return dm_channel_id
            print(f"[REST] DM channel error: {res.status} {data}")
            return None


async def bot_send(payload: dict):
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
                print(f"[REST] Send error: {res.status} {await res.json()}")


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


# ─────────────────────────────────────────────
# Presence helpers
# ─────────────────────────────────────────────

def _build_presence(status: str = "online", activity_name: str | None = None, activity_type: int = 3) -> dict:
    if activity_name is None:
        if keywords:
            activity_name = " | ".join(keywords[:3]) + (" ..." if len(keywords) > 3 else "")
        else:
            activity_name = "for game drops 👀"
    return {
        "op": 3,
        "d": {
            "since": None,
            "activities": [{"name": activity_name, "type": activity_type}],
            "status": status,
            "afk": False,
        },
    }


async def push_presence(status: str = "online", activity_name: str | None = None, activity_type: int = 3):
    if bot_gw:
        await bot_gw._send(_build_presence(status, activity_name, activity_type))


# ─────────────────────────────────────────────
# BOT GATEWAY  (raw WebSocket, bot token)
# Makes bot show online + handles DM commands
# ─────────────────────────────────────────────

class BotGateway:
    GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"

    def __init__(self):
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._sequence: int | None = None
        self._session_id: str | None = None
        self._resume_url: str | None = None
        self._welcomed = False

    async def run(self):
        while True:
            try:
                await self._connect()
            except Exception as exc:
                print(f"[BOT GW] Disconnected: {exc} — reconnecting in 5s")
            await asyncio.sleep(5)

    async def _connect(self):
        url = self._resume_url or self.GATEWAY
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url) as ws:
                self._ws = ws
                print(f"[BOT GW] Connected to {url}")
                async for raw in ws:
                    if raw.type == aiohttp.WSMsgType.TEXT:
                        await self._handle(json.loads(raw.data))
                    elif raw.type == aiohttp.WSMsgType.ERROR:
                        print(f"[BOT GW] WS error: {raw.data}")
                        break
                    elif raw.type == aiohttp.WSMsgType.CLOSED:
                        print(f"[BOT GW] WS closed — code={ws.close_code}")
                        break

    async def _handle(self, msg: dict):
        op   = msg["op"]
        data = msg.get("d")
        seq  = msg.get("s")
        evt  = msg.get("t")

        if seq is not None:
            self._sequence = seq

        if op == 10:  # HELLO
            interval = data["heartbeat_interval"] / 1000
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
            self._heartbeat_task = asyncio.create_task(self._heartbeat(interval))
            if self._session_id and self._sequence:
                await self._send({"op": 6, "d": {
                    "token": BOT_TOKEN,
                    "session_id": self._session_id,
                    "seq": self._sequence,
                }})
            else:
                await self._send({"op": 2, "d": {
                    "token": BOT_TOKEN,
                    "intents": BOT_INTENTS,
                    "properties": {"os": "linux", "browser": "disco", "device": "disco"},
                    "presence": _build_presence()["d"],
                }})
                print("[BOT GW] Sent IDENTIFY")

        elif op == 11:
            pass  # HEARTBEAT ACK

        elif op == 1:
            await self._send({"op": 1, "d": self._sequence})

        elif op == 9:  # INVALID SESSION
            print("[BOT GW] Invalid session — re-identifying")
            self._session_id = None
            self._resume_url = None
            await asyncio.sleep(2)
            await self._send({"op": 2, "d": {
                "token": BOT_TOKEN,
                "intents": BOT_INTENTS,
                "properties": {"os": "linux", "browser": "disco", "device": "disco"},
            }})

        elif op == 0:
            await self._dispatch(evt, data)

    async def _dispatch(self, evt: str | None, data: dict):
        if evt == "READY":
            self._session_id = data["session_id"]
            self._resume_url = data.get("resume_gateway_url", self.GATEWAY)
            print(f"[BOT GW] READY as {data['user']['username']}")
            await push_presence()
            if not self._welcomed:
                self._welcomed = True
                await asyncio.sleep(1)
                kw_text = (
                    "\n".join(f"• {kw}" for kw in keywords)
                    if keywords
                    else "No keywords yet — DM me any game name to start watching."
                )
                await send_embed(
                    title="✅ Bot is online!",
                    description=f"**Watching for:**\n{kw_text}\n\n**Commands:** `!list` · `!status` · `!remove <kw>` · `!clear` · `!help`",
                    color=0x57F287,
                )

        elif evt == "MESSAGE_CREATE":
            author_id = int(data["author"]["id"])
            guild_id  = data.get("guild_id")
            content   = data.get("content", "").strip()
            # DM from the owner → treat as command
            if guild_id is None and author_id == USER_ID:
                print(f"[CMD] {content!r}")
                try:
                    asyncio.create_task(handle_command(content))
                except Exception as exc:
                    print(f"[CMD] Error: {exc}")

        elif evt == "RESUMED":
            print("[BOT GW] Session resumed")

    async def _heartbeat(self, interval: float):
        import random
        await asyncio.sleep(interval * (0.5 + 0.5 * random.random()))
        while True:
            await self._send({"op": 1, "d": self._sequence})
            await asyncio.sleep(interval)

    async def _send(self, payload: dict):
        if self._ws and not self._ws.closed:
            await self._ws.send_str(json.dumps(payload))


# ─────────────────────────────────────────────
# USER CLIENT  (discord.py-self, user token)
# Monitors game channels for keywords
# ─────────────────────────────────────────────

client = discord.Client()


@client.event
async def on_ready():
    print(f"[USER CLIENT] Logged in as {client.user}")
    await ensure_dm_channel()


@client.event
async def on_message(message: discord.Message):
    if message.channel.id not in MONITOR_CHANNELS:
        return
    if not keywords:
        return

    full_text = extract_text(message).lower()
    matched = [kw for kw in keywords if kw.lower() in full_text]
    if not matched:
        return

    for kw in matched:
        available_games[kw] = time.time()

    # Update presence to show the alert
    active_names = ", ".join(f"🟢 {kw.upper()}" for kw in matched)
    await push_presence(status="online", activity_name=f"{active_names} — AVAILABLE!", activity_type=0)

    server  = message.guild.name if message.guild else "Unknown"
    ch_name = getattr(message.channel, "name", str(message.channel.id))
    summary = message_summary(message)

    await send_embed(
        title="🚨 Game Alert — Now Available!",
        description=summary,
        color=0x57F287,
        fields=[
            {"name": "Keywords",  "value": ", ".join(f"**{kw}**" for kw in matched), "inline": False},
            {"name": "Server",    "value": server,              "inline": True},
            {"name": "Channel",   "value": f"#{ch_name}",       "inline": True},
            {"name": "Posted by", "value": str(message.author), "inline": True},
            {"name": "Jump Link", "value": f"[Go to message]({message.jump_url})", "inline": False},
        ],
        url=message.jump_url,
    )
    print(f"[ALERT] {matched} in #{ch_name}")
    alerted_message_ids.add(message.id)

    # Auto-remove found keywords from watchlist
    for kw in matched:
        keywords[:] = [k for k in keywords if k.lower() != kw.lower()]
    save_json(KEYWORDS_FILE, keywords)
    await push_presence()


@client.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    """Catch messages that gain embed data after posting (Discord URL-unfurl / bot edits)."""
    if after.channel.id not in MONITOR_CHANNELS:
        return
    if not keywords:
        return
    if after.id in alerted_message_ids:
        return  # already alerted on this message

    # Only act if embeds were added/changed in the edit
    if len(after.embeds) <= len(before.embeds):
        return

    full_text = extract_text(after).lower()
    matched = [kw for kw in keywords if kw.lower() in full_text]
    if not matched:
        return

    for kw in matched:
        available_games[kw] = time.time()

    active_names = ", ".join(f"🟢 {kw.upper()}" for kw in matched)
    await push_presence(status="online", activity_name=f"{active_names} — AVAILABLE!", activity_type=0)

    server  = after.guild.name if after.guild else "Unknown"
    ch_name = getattr(after.channel, "name", str(after.channel.id))
    summary = message_summary(after)

    await send_embed(
        title="🚨 Game Alert — Now Available!",
        description=summary,
        color=0x57F287,
        fields=[
            {"name": "Keywords",  "value": ", ".join(f"**{kw}**" for kw in matched), "inline": False},
            {"name": "Server",    "value": server,            "inline": True},
            {"name": "Channel",   "value": f"#{ch_name}",     "inline": True},
            {"name": "Posted by", "value": str(after.author), "inline": True},
            {"name": "Jump Link", "value": f"[Go to message]({after.jump_url})", "inline": False},
        ],
        url=after.jump_url,
    )
    print(f"[ALERT via edit] {matched} in #{ch_name}")
    alerted_message_ids.add(after.id)

    for kw in matched:
        keywords[:] = [k for k in keywords if k.lower() != kw.lower()]
    save_json(KEYWORDS_FILE, keywords)
    await push_presence()


# ─────────────────────────────────────────────
# Command handling
# ─────────────────────────────────────────────

async def handle_command(text: str):
    global keywords

    if not text:
        return

    if text.lower() in ("!help", "help"):
        await send_embed(
            title="📖 Bot Commands",
            description=(
                "**Adding & removing keywords**\n"
                "`<game name>` — Add a keyword. Bot instantly scans the full channel history (including embeds).\n"
                "`!remove <keyword>` — Stop watching for a keyword.\n"
                "`!clear` — Remove all keywords.\n\n"
                "**Checking status**\n"
                "`!list` — Show all keywords you're watching.\n"
                "`!status` — Show 🟢/🔴 availability for each keyword.\n\n"
                "**Help**\n"
                "`!help` — Show this card."
            ),
            color=0x5865F2,
            footer="The bot monitors your channels 24/7 and reads both text and embed messages.",
        )
        return

    if text.lower() == "!list":
        if keywords:
            await send_embed(
                title="👀 Watchlist",
                description="\n".join(f"• {kw}" for kw in keywords),
                color=0x5865F2,
            )
        else:
            await send_embed(
                title="👀 Watchlist",
                description="No keywords set yet. Send me a game name to start watching.",
                color=0x5865F2,
            )
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
        await send_embed(
            title="📊 Game Status",
            description="\n".join(lines) if lines else "No keywords set.",
            color=0x5865F2,
        )
        return

    if text.lower() == "!clear":
        count = len(keywords)
        keywords = []
        save_json(KEYWORDS_FILE, keywords)
        available_games.clear()
        await push_presence()
        await send_embed(
            title="🗑️ Watchlist Cleared",
            description=f"Removed all {count} keyword(s).",
            color=0xED4245,
        )
        return

    if text.lower().startswith("!remove "):
        target = text[8:].strip()
        before = len(keywords)
        keywords = [kw for kw in keywords if kw.lower() != target.lower()]
        if len(keywords) < before:
            save_json(KEYWORDS_FILE, keywords)
            available_games.pop(target.lower(), None)
            await push_presence()
            await send_embed(
                title="✅ Keyword Removed",
                description=f"**{target}** has been removed from the watchlist.",
                color=0x57F287,
            )
        else:
            await send_embed(
                title="❌ Not Found",
                description=f"**{target}** is not in the watchlist.",
                color=0xED4245,
            )
        return

    if text.startswith("!"):
        await send_embed(
            title="❓ Unknown Command",
            description="Commands: `!list` · `!status` · `!remove <keyword>` · `!clear` · `!help`",
            color=0xED4245,
        )
        return

    # Plain text = add as keyword
    kw = text
    if kw.lower() in [k.lower() for k in keywords]:
        await send_embed(
            title="ℹ️ Already Watching",
            description=f"**{kw}** is already in the watchlist.",
            color=0x5865F2,
        )
        return

    keywords.append(kw)
    save_json(KEYWORDS_FILE, keywords)

    # Update presence to show the new keyword list
    kw_list = " | ".join(keywords[:3]) + (" ..." if len(keywords) > 3 else "")
    await push_presence(activity_name=kw_list)

    await send_embed(
        title="✅ Keyword Added",
        description=(
            f"Now watching for **{kw}**.\n"
            f"Total: **{len(keywords)}** keyword(s)\n\n"
            f"🔍 Scanning full channel history (including embeds)..."
        ),
        color=0x57F287,
    )
    asyncio.create_task(check_history_for_keyword(kw))


# ─────────────────────────────────────────────
# History scan — reads both text and embed content
# ─────────────────────────────────────────────

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
                full_text = extract_text(msg).lower()
                if kw.lower() in full_text:
                    found_any = True
                    available_games[kw] = time.time()

                    server  = msg.guild.name if msg.guild else "Unknown"
                    ch_name = getattr(channel, "name", str(channel_id))
                    summary = message_summary(msg)

                    await push_presence(
                        status="online",
                        activity_name=f"🟢 {kw.upper()} — FOUND IN HISTORY",
                        activity_type=0,
                    )
                    await send_embed(
                        title="⚠️ Already Available! — Found in Channel History",
                        description=summary,
                        color=0xFEE75C,
                        fields=[
                            {"name": "Keyword",   "value": f"**{kw}**",  "inline": True},
                            {"name": "Server",    "value": server,        "inline": True},
                            {"name": "Channel",   "value": f"#{ch_name}", "inline": True},
                            {"name": "Posted by", "value": str(msg.author), "inline": True},
                            {"name": "Posted at", "value": f"<t:{int(msg.created_at.timestamp())}:R>", "inline": True},
                            {"name": "Jump Link", "value": f"[Go to message]({msg.jump_url})", "inline": False},
                        ],
                        footer="This message was already in the channel before you added the keyword.",
                        url=msg.jump_url,
                    )
                    print(f"[HISTORY] '{kw}' found in #{ch_name}")
                    # Auto-remove from watchlist now that it's been found
                    keywords[:] = [k for k in keywords if k.lower() != kw.lower()]
                    save_json(KEYWORDS_FILE, keywords)
                    await push_presence()
                    break
        except Exception as e:
            print(f"Error reading history for {channel_id}: {e}")

    if not found_any:
        await send_embed(
            title="🔍 History Scan Complete",
            description=f"No mention of **{kw}** found in the full channel history.\nYou'll be alerted the moment it appears!",
            color=0x5865F2,
        )


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

def run_bot():
    global bot_gw
    bot_gw = BotGateway()

    async def _main():
        await asyncio.gather(
            client.start(USER_TOKEN),
            bot_gw.run(),
        )

    asyncio.run(_main())
