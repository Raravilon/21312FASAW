import os
import random
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import tasks

# ---------- Settings ----------
# Token comes from the DISCORD_TOKEN environment variable (set it in BotHost)
TOKEN = os.getenv("DISCORD_TOKEN")

# IDs taken from your server links (can be overridden with env variables)
GUILD_ID = int(os.getenv("GUILD_ID", "1564401949019869224"))
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "1564405099004956702"))
VERIFY_CHANNEL_ID = int(os.getenv("VERIFY_CHANNEL_ID", "1564405761105461379"))

# Verification roles (looked up by name; created automatically if missing)
JOINED_ROLE_NAME = os.getenv("JOINED_ROLE_NAME", "Just joined!")
NEWCOMER_ROLE_NAME = os.getenv("NEWCOMER_ROLE_NAME", "Newcomer :3")

# Level rewards: (level, role name). Names are matched case-insensitively and
# by prefix, so "Friendly" also finds a role called "Friendly ^~^".
LEVEL_ROLES = [
    (3, "Silly Critter"),
    (10, "Friendly"),
    (15, "PAWSOME"),
]

# XP settings
XP_MIN = 15
XP_MAX = 25
XP_COOLDOWN = 60  # seconds between XP gains per person (messages are still counted)

# Where the stats are stored
DB_PATH = os.getenv("DB_PATH", "data/bot.db")

PINK = discord.Colour.from_str("#f4b6c2")
CREAM = discord.Colour.from_str("#f7e7d3")

# Needs "Server Members Intent" enabled in the Discord Developer Portal
intents = discord.Intents.default()
intents.members = True


# ---------- Database ----------
class Database:
    def __init__(self, path: str):
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                xp INTEGER NOT NULL DEFAULT 0,
                messages INTEGER NOT NULL DEFAULT 0,
                pings INTEGER NOT NULL DEFAULT 0,
                voice_seconds INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        self.conn.commit()

    def _ensure(self, guild_id: int, user_id: int):
        self.conn.execute(
            "INSERT OR IGNORE INTO users (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )

    def add_message(self, guild_id: int, user_id: int, pings: int, xp_gain: int):
        """Count a message. Returns (old_xp, new_xp)."""
        self._ensure(guild_id, user_id)
        old_xp = self.conn.execute(
            "SELECT xp FROM users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()[0]
        self.conn.execute(
            "UPDATE users SET messages = messages + 1, pings = pings + ?, xp = xp + ? "
            "WHERE guild_id = ? AND user_id = ?",
            (pings, xp_gain, guild_id, user_id),
        )
        self.conn.commit()
        return old_xp, old_xp + xp_gain

    def add_voice(self, guild_id: int, user_id: int, seconds: int):
        self._ensure(guild_id, user_id)
        self.conn.execute(
            "UPDATE users SET voice_seconds = voice_seconds + ? "
            "WHERE guild_id = ? AND user_id = ?",
            (seconds, guild_id, user_id),
        )
        self.conn.commit()

    def get(self, guild_id: int, user_id: int):
        row = self.conn.execute(
            "SELECT xp, messages, pings, voice_seconds FROM users "
            "WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        if row is None:
            return {"xp": 0, "messages": 0, "pings": 0, "voice_seconds": 0}
        return {
            "xp": row[0],
            "messages": row[1],
            "pings": row[2],
            "voice_seconds": row[3],
        }

    def rank(self, guild_id: int, xp: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) + 1 FROM users WHERE guild_id = ? AND xp > ?",
            (guild_id, xp),
        ).fetchone()[0]


db = Database(DB_PATH)


# ---------- Level math ----------
def xp_needed(level: int) -> int:
    """XP needed to go from `level` to `level + 1`."""
    return 5 * level * level + 50 * level + 100


def level_from_xp(total_xp: int):
    """Returns (level, xp_into_level, xp_needed_for_next_level)."""
    level = 0
    remaining = total_xp
    while remaining >= xp_needed(level):
        remaining -= xp_needed(level)
        level += 1
    return level, remaining, xp_needed(level)


def progress_bar(current: int, total: int, length: int = 14) -> str:
    filled = int(length * current / total) if total else 0
    return "█" * filled + "░" * (length - filled)


def format_duration(seconds: int) -> str:
    minutes = seconds // 60
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# ---------- Role helpers ----------
def find_role(guild: discord.Guild, name: str) -> discord.Role | None:
    lowered = name.lower()
    for role in guild.roles:
        if role.name.lower() == lowered:
            return role
    for role in guild.roles:
        if role.name.lower().startswith(lowered):
            return role
    return None


async def get_role(guild: discord.Guild, name: str) -> discord.Role | None:
    role = find_role(guild, name)
    if role is None:
        try:
            role = await guild.create_role(name=name, reason="Created by Mupaw bot")
        except discord.Forbidden:
            return None
    return role


async def sync_level_roles(member: discord.Member, level: int) -> list[discord.Role]:
    """Give every reward role the member has earned. Returns newly given roles."""
    granted = []
    for required_level, role_name in LEVEL_ROLES:
        if level < required_level:
            continue
        role = await get_role(member.guild, role_name)
        if role is None or role in member.roles:
            continue
        try:
            await member.add_roles(role, reason=f"Reached level {required_level}")
            granted.append(role)
        except discord.Forbidden:
            print(f"Cannot give role '{role.name}': maple this error again.")
    return granted


def find_channel_mention(guild: discord.Guild, keyword: str) -> str:
    """Find a channel whose name contains the keyword (names have decorations)."""
    channel = discord.utils.find(lambda c: keyword in c.name.lower(), guild.channels)
    return channel.mention if channel else f"#{keyword}"


# ---------- Verification ----------
class ConfirmView(discord.ui.View):
    """Second step: the person confirms they read the rules."""

    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="I agree, let me in", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user

        newcomer_role = await get_role(guild, NEWCOMER_ROLE_NAME)
        joined_role = find_role(guild, JOINED_ROLE_NAME)

        try:
            if newcomer_role:
                await member.add_roles(newcomer_role, reason="Passed verification")
            if joined_role and joined_role in member.roles:
                await member.remove_roles(joined_role, reason="Passed verification")
        except discord.Forbidden:
            await interaction.response.edit_message(
                content="I can't change your roles. Please tell a mod to move my role higher.",
                view=None,
            )
            return

        await interaction.response.edit_message(
            content="You're verified! Welcome to the server, have fun and be silly :3",
            view=None,
        )


class VerifyView(discord.ui.View):
    """First step: the button in the verify channel."""

    def __init__(self):
        super().__init__(timeout=None)  # persistent: works after bot restarts

    @discord.ui.button(
        label="Verify",
        style=discord.ButtonStyle.primary,
        custom_id="silly_verify_button",
    )
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user

        newcomer_role = find_role(guild, NEWCOMER_ROLE_NAME)
        if newcomer_role and newcomer_role in member.roles:
            await interaction.response.send_message(
                "You're already verified!", ephemeral=True
            )
            return

        rules = find_channel_mention(guild, "rules")
        know = find_channel_mention(guild, "what-to-know")
        await interaction.response.send_message(
            f"Clicking on verify, you accept that you have read channels: {rules} and {know}.\n"
            "Press the button below to confirm.",
            view=ConfirmView(),
            ephemeral=True,
        )


# ---------- Bot ----------
class SillyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.add_view(VerifyView())
        voice_tracker.start()
        # Sync commands to your server (shows up instantly)
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self):
        print(f"Bot is online as {self.user}")


client = SillyBot()

# Remembers when each person last got XP (for the cooldown)
last_xp_time: dict[tuple[int, int], float] = {}


# ---------- Voice time tracking ----------
@tasks.loop(seconds=60)
async def voice_tracker():
    """Every minute, add one minute to everyone sitting in a voice channel."""
    guild = client.get_guild(GUILD_ID)
    if guild is None:
        return
    for channel in guild.voice_channels + guild.stage_channels:
        if channel == guild.afk_channel:
            continue
        for member in channel.members:
            if not member.bot:
                db.add_voice(guild.id, member.id, 60)


@voice_tracker.before_loop
async def before_voice_tracker():
    await client.wait_until_ready()


# ---------- Events ----------
def build_welcome_embed(member: discord.Member) -> discord.Embed:
    verify_channel = member.guild.get_channel(VERIFY_CHANNEL_ID)
    verify_text = verify_channel.mention if verify_channel else "the verify channel"

    embed = discord.Embed(
        title="A new silly critter appeared!",
        description=(
            f"Haii {member.mention}, welcome to **{member.guild.name}**!\n\n"
            f"Hop over to {verify_text} and press the button to get access "
            f"to the rest of the server. Be silly with us :3"
        ),
        colour=PINK,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"You are member #{member.guild.member_count}")
    return embed


@client.event
async def on_member_join(member: discord.Member):
    if member.guild.id != GUILD_ID or member.bot:
        return

    # Give the "Just joined!" role
    joined_role = await get_role(member.guild, JOINED_ROLE_NAME)
    if joined_role:
        try:
            await member.add_roles(joined_role, reason="New member")
        except discord.Forbidden:
            print("Cannot give the 'Just joined!' role: move the bot role higher.")

    # Welcome message
    channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if channel is not None:
        await channel.send(content=member.mention, embed=build_welcome_embed(member))


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None or message.guild.id != GUILD_ID:
        return

    member = message.author

    # Count pings: unique real people mentioned (not yourself, not bots)
    pings = len({m.id for m in message.mentions if not m.bot and m.id != member.id})

    # XP only if the cooldown has passed
    key = (message.guild.id, member.id)
    now = time.time()
    gain = 0
    if now - last_xp_time.get(key, 0) >= XP_COOLDOWN:
        gain = random.randint(XP_MIN, XP_MAX)
        last_xp_time[key] = now

    old_xp, new_xp = db.add_message(message.guild.id, member.id, pings, gain)

    old_level = level_from_xp(old_xp)[0]
    new_level = level_from_xp(new_xp)[0]
    if new_level > old_level:
        granted = await sync_level_roles(member, new_level)
        text = f"{member.mention} just reached **level {new_level}**!"
        if granted:
            names = ", ".join(f"**{role.name}**" for role in granted)
            text += f" New role unlocked: {names}"
        await message.channel.send(text)


# ---------- Commands ----------
@client.tree.command(name="ping", description="Check if the bot is awake")
async def ping(interaction: discord.Interaction):
    latency = round(client.latency * 1000)
    await interaction.response.send_message(f"Pong! {latency} ms")


@client.tree.command(name="hello", description="Say hi to the bot")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"Haii {interaction.user.mention}!")


@client.tree.command(name="profile", description="Shows your or someone else's profile")
@app_commands.describe(member="Profile to show (leave empty for your own)")
@app_commands.guild_only()
async def profile(interaction: discord.Interaction, member: discord.Member | None = None):
    member = member or interaction.user

    if member.bot:
        await interaction.response.send_message(
            "Bots don't have profiles, they are too busy working.", ephemeral=True
        )
        return

    data = db.get(interaction.guild.id, member.id)
    level, into_level, needed = level_from_xp(data["xp"])
    rank = db.rank(interaction.guild.id, data["xp"])

    # Next reward role
    next_reward = "All roles unlocked"
    for required_level, role_name in LEVEL_ROLES:
        if level < required_level:
            next_reward = f"{role_name} at level {required_level}"
            break

    colour = member.colour if member.colour.value else PINK
    embed = discord.Embed(
        title=f"{member.display_name}'s profile",
        description=(
            f"**Level {level}**  |  Rank **#{rank}**\n"
            f"`{progress_bar(into_level, needed)}`  {into_level:,} / {needed:,} XP"
        ),
        colour=colour,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Messages sent", value=f"{data['messages']:,}", inline=True)
    embed.add_field(name="Pings sent", value=f"{data['pings']:,}", inline=True)
    embed.add_field(name="Time in voice", value=format_duration(data["voice_seconds"]), inline=True)
    embed.add_field(name="Next reward", value=next_reward, inline=False)
    embed.set_footer(text=f"Total XP: {data['xp']:,}")

    await interaction.response.send_message(embed=embed)


@client.tree.command(name="setup_verify", description="Post the verification message (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_verify(interaction: discord.Interaction):
    channel = interaction.guild.get_channel(VERIFY_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message("I can't find the verify channel.", ephemeral=True)
        return

    embed = discord.Embed(
        title="Verification",
        description=(
            "Welcome, little critter! Press the button below to verify "
            "and get access to the rest of the server."
        ),
        colour=CREAM,
    )
    await channel.send(embed=embed, view=VerifyView())
    await interaction.response.send_message(f"Verification posted in {channel.mention}.", ephemeral=True)


@client.tree.command(name="test_welcome", description="Preview the welcome message (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def test_welcome(interaction: discord.Interaction):
    await interaction.response.send_message(
        content=interaction.user.mention,
        embed=build_welcome_embed(interaction.user),
        ephemeral=True,
    )


if not TOKEN:
    raise SystemExit("DISCORD_TOKEN environment variable is not set")

client.run(TOKEN)
