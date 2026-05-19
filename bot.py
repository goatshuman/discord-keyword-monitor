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

# DIRECT_MESSAGES (1<<12). MESSAGE_CONTENT is privileged — not needed for DMs.
BOT_INTENTS = (1 << 12)


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


keywords: list[str]         = load_json(KEYWORDS_FILE, [])
dm_channel_id: str | None   = load_json(DM_CHANNEL_FILE, {}).get("channel_id")
available_games: dict[str, float] = {}


# ─────────────────────────────────────────────
# REST helpers — all messages go via aiohttp
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
        """Keep the bot gateway alive forever."""
        while True:
            try:
                await self._connect()
            except Exception as exc:
                print(f"[BOT GW] Disconnected: {exc}  — reconnecting in 5s")
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
                        print(f"[BOT GW] WS closed — code={ws.close_code} extra={ws.exception()}")
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
                await self._send({"op": 6, "d": {  # RESUME
                    "token": BOT_TOKEN,
                    "session_id": self._session_id,
                    "seq": self._sequence,
                }})
                print("[BOT GW] Resuming session")
            else:
                await self._send({"op": 2, "d": {  # IDENTIFY
                    "token": BOT_TOKEN,
                    "intents": BOT_INTENTS,
                    "properties": {"os": "linux", "browser": "disco", "device": "disco"},
                    "presence": {
                        "status": "online",
                        "activities": [{"name": "for game drops 👀", "type": 3}],
                        "afk": False,
                    },
                }})
                print("[BOT GW] Sent IDENTIFY")

        elif op == 11:  # HEARTBEAT ACK
            pass

        elif op == 1:  # HEARTBEAT request
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

        elif op == 0:  # DISPATCH
            await self._dispatch(evt, data)

    async def _update_presence(self):
        """Push presence update after gateway is ready."""
        await self._send({
            "op": 3,
            "d": {
                "since": None,
                "activities": [{"name": "for game drops 👀", "type": 3}],
                "status": "online",
                "afk": False,
            },
        })

    async def _dispatch(self, evt: str | None, data: dict):
        print(f"[BOT GW] EVENT: {evt}")

        if evt == "READY":
            self._session_id = data["session_id"]
            self._resume_url = data.get("resume_gateway_url", self.GATEWAY)
            print(f"[BOT GW] READY as {data['user']['username']}")
            await self._update_presence()
            if not self._welcomed:
                self._welcomed = True
                await asyncio.sleep(1)
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

        elif evt == "MESSAGE_CREATE":
            author_id = int(data["author"]["id"])
            guild_id  = data.get("guild_id")
            content   = data.get("content", "")
            print(f"[BOT GW] MESSAGE guild={guild_id} author={author_id} content={content!r}")
            # DM from the user → treat as command
            if guild_id is None and author_id == USER_ID:
                print(f"[CMD] {content!r}")
                try:
                    asyncio.create_task(handle_command(content.strip()))
                except Exception as exc:
                    print(f"[CMD] Error: {exc}")

        elif evt == "RESUMED":
            print("[BOT GW] Session resumed")

    async def _heartbeat(self, interval: float):
        await asyncio.sleep(interval * (0.5 + 0.5 * __import__("random").random()))
        while True:
            await self._send({"op": 1, "d": self._sequence})
            await asyncio.sleep(interval)

    async def _send(self, payload: dict):
        if self._ws and not self._ws.closed:
            await self._ws.send_str(json.dumps(payload))


# ─────────────────────────────────────────────
# USER CLIENT  (discord.py-self, user token)
# Monitors the game channels for keywords
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

    content_lower = message.content.lower()
    matched = [kw for kw in keywords if kw.lower() in content_lower]
    if not matched:
        return

    for kw in matched:
        available_games[kw] = time.time()

    server  = message.guild.name if message.guild else "Unknown"
    ch_name = getattr(message.channel, "name", str(message.channel.id))
    await send_embed(
        title="🚨 Game Alert — Now Available!",
        description=message.content[:2000],
        color=0x57F287,
        fields=[
            {"name": "Keywords",  "value": ", ".join(f"**{kw}**" for kw in matched), "inline": False},
            {"name": "Server",    "value": server,          "inline": True},
            {"name": "Channel",   "value": f"#{ch_name}",   "inline": True},
            {"name": "Posted by", "value": str(message.author), "inline": True},
            {"name": "Jump Link", "value": f"[Go to message]({message.jump_url})", "inline": False},
        ],
        url=message.jump_url,
    )
    print(f"[ALERT] {matched} in #{ch_name}")


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
        await send_text("✅ All keywords cleared.")
        return

    if text.lower().startswith("!remove "):
        target = text[8:].strip().lower()
        before = len(keywords)
        keywords = [kw for kw in keywords if kw.lower() != target]
        if len(keywords) < before:
            save_json(KEYWORDS_FILE, keywords)
            available_games.pop(target, None)
            await send_text(f"✅ Removed **{target}**.")
        else:
            await send_text(f"❌ **{target}** not in the watchlist.")
        return

    if text.startswith("!"):
        await send_text("Commands: `!list` · `!status` · `!remove <keyword>` · `!clear` · `!help`")
        return

    # Plain text → add as keyword
    kw = text
    if kw.lower() in [k.lower() for k in keywords]:
        await send_text(f"**{kw}** is already in the watchlist.")
        return

    keywords.append(kw)
    save_json(KEYWORDS_FILE, keywords)
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

                    server  = msg.guild.name if msg.guild else "Unknown"
                    ch_name = getattr(channel, "name", str(channel_id))
                    await send_embed(
                        title="⚠️ Already Available! — Found in Channel History",
                        description=msg.content[:2000],
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
                    print(f"[HISTORY] '{kw}' in #{ch_name}")
                    break
        except Exception as e:
            print(f"Error reading history for {channel_id}: {e}")

    if not found_any:
        await send_text(
            f"🔍 No mention of **{kw}** found in the full channel history. I'll alert you the moment it appears!"
        )


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

def run_bot():
    bot_gw = BotGateway()

    async def _main():
        await asyncio.gather(
            client.start(USER_TOKEN),
            bot_gw.run(),
        )

    asyncio.run(_main())
