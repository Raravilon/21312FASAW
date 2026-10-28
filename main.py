import os
import random
import re
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import tasks

# ======================================================================
#  Settings
# ======================================================================
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

# Currencies
MESSAGES_PER_MUPS = 100   # every 100 messages that earn XP ...
MUPS_REWARD = 10          # ... gives 10 Mups
VOICE_SECONDS_PER_VUPS = 3600  # every hour in voice ...
VUPS_REWARD = 1                # ... gives 1 Vups

# Private voice chats
VOICE_MAX_LIMIT = 10
VOICE_IDLE_SECONDS = 6 * 60 * 60  # delete empty private voices after 6 hours
# Words that are not allowed in private voice names (short words must be a whole word)
BLOCKED_WORDS: list[str] = [
    "Nigger",
    "Niger",
    "Fuck",
    "sex",
    "NSFW",
    "18",
    "roleplay",
]

# Self-assign roles: (label shown in the menu, role name or start of it)
PRONOUN_ROLES = [
    ("He/Him", "He/Him"),
    ("She/Her", "She/Her"),
    ("They/Them", "They/Them"),
    ("It/Its", "It/I"),
    ("Cat/cats", "Cat/cats"),
    ("Ask for pronouns", "Ask for pronouns"),
    ("Neopronouns", "Neopronouns"),
]
COLOR_ROLES = [
    ("Purple", "Purple"),
    ("White", "White"),
    ("Red", "Red"),
    ("Blue", "Blue"),
    ("Light Green", "Light Green"),
    ("Beige", ("Beige", "Biege")),  # also finds a role spelled "Biege"
    ("Green", "Green"),
    ("Pink", "Pink"),
    ("Orange", "Orange"),
    ("Yellow", "Yellow"),
    ("Dark Purple", "Dark Purple"),
    ("Black", "Black"),
]
PING_ROLES = [
    ("Gamenights", "Gamenight"),
    ("Chat.. revive!", "Chat.. REVIVE"),
]

# Where the stats are stored
DB_PATH = os.getenv("DB_PATH", "data/bot.db")

PINK = discord.Colour.from_str("#f4b6c2")
CREAM = discord.Colour.from_str("#f7e7d3")

# Needs "Server Members Intent" enabled in the Discord Developer Portal
intents = discord.Intents.default()
intents.members = True


# ======================================================================
#  Database
# ======================================================================
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
                mups INTEGER NOT NULL DEFAULT 0,
                vups INTEGER NOT NULL DEFAULT 0,
                xp_messages INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        # Upgrade old databases that don't have the currency columns yet
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(users)")}
        for column in ("mups", "vups", "xp_messages"):
            if column not in columns:
                self.conn.execute(
                    f"ALTER TABLE users ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS voices (
                channel_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                last_active INTEGER NOT NULL
            )
            """
        )
        self.conn.commit()

    # ----- users -----
    def _ensure(self, guild_id: int, user_id: int):
        self.conn.execute(
            "INSERT OR IGNORE INTO users (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )

    def add_message(self, guild_id: int, user_id: int, pings: int, xp_gain: int):
        """Count a message. Returns (old_xp, new_xp, mups_gained).

        Every message is counted in `messages`, but Mups are only earned from
        messages that gave XP (`xp_messages`), so spamming doesn't farm coins.
        """
        self._ensure(guild_id, user_id)
        old_xp, old_xp_messages = self.conn.execute(
            "SELECT xp, xp_messages FROM users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        new_xp_messages = old_xp_messages + (1 if xp_gain > 0 else 0)
        mups_gain = (
            new_xp_messages // MESSAGES_PER_MUPS - old_xp_messages // MESSAGES_PER_MUPS
        ) * MUPS_REWARD
        self.conn.execute(
            "UPDATE users SET messages = messages + 1, pings = pings + ?, xp = xp + ?, "
            "xp_messages = ?, mups = mups + ? WHERE guild_id = ? AND user_id = ?",
            (pings, xp_gain, new_xp_messages, mups_gain, guild_id, user_id),
        )
        self.conn.commit()
        return old_xp, old_xp + xp_gain, mups_gain

    def add_voice(self, guild_id: int, user_id: int, seconds: int):
        """Add voice time. Returns Vups gained."""
        self._ensure(guild_id, user_id)
        old_seconds = self.conn.execute(
            "SELECT voice_seconds FROM users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()[0]
        new_seconds = old_seconds + seconds
        vups_gain = (
            new_seconds // VOICE_SECONDS_PER_VUPS - old_seconds // VOICE_SECONDS_PER_VUPS
        ) * VUPS_REWARD
        self.conn.execute(
            "UPDATE users SET voice_seconds = ?, vups = vups + ? "
            "WHERE guild_id = ? AND user_id = ?",
            (new_seconds, vups_gain, guild_id, user_id),
        )
        self.conn.commit()
        return vups_gain

    def get(self, guild_id: int, user_id: int):
        row = self.conn.execute(
            "SELECT xp, messages, pings, voice_seconds, mups, vups FROM users "
            "WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        if row is None:
            return {"xp": 0, "messages": 0, "pings": 0, "voice_seconds": 0, "mups": 0, "vups": 0}
        return {
            "xp": row[0],
            "messages": row[1],
            "pings": row[2],
            "voice_seconds": row[3],
            "mups": row[4],
            "vups": row[5],
        }

    def rank(self, guild_id: int, xp: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) + 1 FROM users WHERE guild_id = ? AND xp > ?",
            (guild_id, xp),
        ).fetchone()[0]

    def top(self, guild_id: int, column: str, limit: int):
        """Top users. Returns rows of (user_id, xp, messages, voice_seconds)."""
        if column not in ("xp", "messages", "voice_seconds"):
            raise ValueError("bad column")
        return self.conn.execute(
            f"SELECT user_id, xp, messages, voice_seconds FROM users "
            f"WHERE guild_id = ? AND {column} > 0 ORDER BY {column} DESC LIMIT ?",
            (guild_id, limit),
        ).fetchall()

    # ----- private voice channels -----
    def add_voice_channel(self, guild_id: int, channel_id: int, owner_id: int, now: int):
        self.conn.execute(
            "INSERT OR REPLACE INTO voices (channel_id, guild_id, owner_id, last_active) "
            "VALUES (?, ?, ?, ?)",
            (channel_id, guild_id, owner_id, now),
        )
        self.conn.commit()

    def get_voice_by_owner(self, guild_id: int, owner_id: int):
        row = self.conn.execute(
            "SELECT channel_id FROM voices WHERE guild_id = ? AND owner_id = ?",
            (guild_id, owner_id),
        ).fetchone()
        return row[0] if row else None

    def list_voices(self, guild_id: int):
        return self.conn.execute(
            "SELECT channel_id, last_active FROM voices WHERE guild_id = ?",
            (guild_id,),
        ).fetchall()

    def touch_voice(self, channel_id: int, now: int):
        self.conn.execute(
            "UPDATE voices SET last_active = ? WHERE channel_id = ?", (now, channel_id)
        )
        self.conn.commit()

    def remove_voice(self, channel_id: int):
        self.conn.execute("DELETE FROM voices WHERE channel_id = ?", (channel_id,))
        self.conn.commit()


db = Database(DB_PATH)


# ======================================================================
#  Level math and formatting
# ======================================================================
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


def is_blocked_name(name: str) -> bool:
    """True if the name contains a blocked word.

    Short words (3 letters or less, like "sex" or "18") only count as whole
    words, so names like "Essex" or "Room 180" are fine. Longer words are
    also caught inside other words and when letters are separated ("f u c k").
    """
    lowered = name.lower()
    tokens = re.findall(r"[a-z0-9]+", lowered)
    compact = "".join(tokens)
    for word in BLOCKED_WORDS:
        word = word.lower()
        if len(word) <= 3:
            if word in tokens:
                return True
        elif word in lowered or word in compact:
            return True
    return False


# ======================================================================
#  Role helpers
# ======================================================================
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
            print(f"Cannot give role '{role.name}': move the bot role higher.")
    return granted


def has_level_role(member: discord.Member) -> bool:
    """True for Silly Critter and above (and for admins)."""
    if member.guild_permissions.administrator:
        return True
    for _, role_name in LEVEL_ROLES:
        role = find_role(member.guild, role_name)
        if role and role in member.roles:
            return True
    return False


def find_channel_mention(guild: discord.Guild, keyword: str) -> str:
    """Find a channel whose name contains the keyword (names have decorations)."""
    channel = discord.utils.find(lambda c: keyword in c.name.lower(), guild.channels)
    return channel.mention if channel else f"#{keyword}"


# ======================================================================
#  Verification
# ======================================================================
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


# ======================================================================
#  Private voice chats
# ======================================================================
class NameLimitModal(discord.ui.Modal, title="Your voice chat"):
    name_input = discord.ui.TextInput(
        label="What's your voice name?",
        placeholder="Cozy corner",
        max_length=50,
        required=True,
    )
    limit_input = discord.ui.TextInput(
        label="How much is limit for people in your voice?",
        placeholder=f"A number from 1 to {VOICE_MAX_LIMIT}",
        max_length=2,
        required=True,
    )

    def __init__(self, parent: "VoiceSetupView"):
        super().__init__()
        self.parent = parent
        if parent.name:
            self.name_input.default = parent.name
        if parent.limit:
            self.limit_input.default = str(parent.limit)

    async def on_submit(self, interaction: discord.Interaction):
        name = self.name_input.value.strip()
        if not name:
            await interaction.response.send_message("The name can't be empty.", ephemeral=True)
            return

        try:
            limit = int(self.limit_input.value.strip())
        except ValueError:
            await interaction.response.send_message(
                f"The limit must be a number from 1 to {VOICE_MAX_LIMIT}.", ephemeral=True
            )
            return
        if not 1 <= limit <= VOICE_MAX_LIMIT:
            await interaction.response.send_message(
                f"The limit must be a number from 1 to {VOICE_MAX_LIMIT}.", ephemeral=True
            )
            return

        if is_blocked_name(name):
            await interaction.response.send_message(
                "Please choose a different name, that one isn't allowed.", ephemeral=True
            )
            return

        self.parent.name = name
        self.parent.limit = limit
        await interaction.response.edit_message(
            embed=self.parent.make_embed(), view=self.parent
        )


class VoiceSetupView(discord.ui.View):
    """The settings panel shown after pressing "Create Voice"."""

    def __init__(self, owner: discord.Member, category: discord.CategoryChannel | None):
        super().__init__(timeout=600)
        self.owner = owner
        self.category = category
        self.name: str | None = None
        self.limit: int | None = None
        self.access = "public"
        self.invited: list[discord.Member] = []

    def make_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Create your voice chat",
            description="Set everything up, then press **Confirm, create voice!**",
            colour=CREAM,
        )
        embed.add_field(
            name="What's your voice name?",
            value=self.name or "Not set yet",
            inline=False,
        )
        embed.add_field(
            name="How much is limit for people in your voice?",
            value=str(self.limit) if self.limit else f"Not set yet (1 to {VOICE_MAX_LIMIT})",
            inline=False,
        )
        if self.access == "public":
            access_text = "**Public**: all Newcomers and Silly Critters can join"
        else:
            people = ", ".join(m.mention for m in self.invited) or "nobody picked yet"
            access_text = f"**Private**: only you and {people}"
        embed.add_field(name="Who has access to your voice chat?", value=access_text, inline=False)
        return embed

    @discord.ui.select(
        placeholder="Who has access to your voice chat?",
        options=[
            discord.SelectOption(label="Public", value="public", description="All Newcomers and Silly Critters", default=True),
            discord.SelectOption(label="Private", value="private", description="Only the people you pick"),
        ],
        row=0,
    )
    async def access_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.access = select.values[0]
        for option in select.options:
            option.default = option.value == self.access
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="Private only: who can join? (up to 10)",
        min_values=0,
        max_values=10,
        row=1,
    )
    async def invite_select(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        self.invited = [
            user for user in select.values if not user.bot and user.id != self.owner.id
        ]
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="Set name and limit", style=discord.ButtonStyle.secondary, row=2)
    async def set_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(NameLimitModal(self))

    @discord.ui.button(label="Confirm, create voice!", style=discord.ButtonStyle.success, row=2)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.name is None or self.limit is None:
            await interaction.response.send_message(
                "Please press **Set name and limit** first.", ephemeral=True
            )
            return

        guild = interaction.guild

        # One voice chat per person
        existing_id = db.get_voice_by_owner(guild.id, self.owner.id)
        if existing_id:
            existing = guild.get_channel(existing_id)
            if existing:
                await interaction.response.edit_message(
                    content=f"You already have a voice chat: {existing.mention}",
                    embed=None,
                    view=None,
                )
                return
            db.remove_voice(existing_id)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, connect=True, manage_channels=True, move_members=True
            ),
            self.owner: discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                speak=True,
                manage_channels=True,
                manage_permissions=True,
                move_members=True,
                mute_members=True,
            ),
        }
        if self.access == "public":
            for role_name in (NEWCOMER_ROLE_NAME, LEVEL_ROLES[0][1]):
                role = find_role(guild, role_name)
                if role:
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True, connect=True)
        else:
            for user in self.invited:
                overwrites[user] = discord.PermissionOverwrite(view_channel=True, connect=True)

        try:
            channel = await guild.create_voice_channel(
                name=self.name,
                user_limit=self.limit,
                category=self.category,
                overwrites=overwrites,
                reason=f"Private voice created by {self.owner}",
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.edit_message(
                content="I couldn't create the channel. Please tell a mod.",
                embed=None,
                view=None,
            )
            return

        db.add_voice_channel(guild.id, channel.id, self.owner.id, int(time.time()))
        self.stop()
        await interaction.response.edit_message(
            content=f"Your voice chat is ready: {channel.mention}",
            embed=None,
            view=None,
        )

        # If they are already in a voice channel, bring them along
        if self.owner.voice is not None:
            try:
                await self.owner.move_to(channel)
            except (discord.Forbidden, discord.HTTPException):
                pass


class CreateVoiceView(discord.ui.View):
    """The "Create Voice" button posted in the create-voice channel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create Voice",
        style=discord.ButtonStyle.primary,
        custom_id="create_voice_button",
    )
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        guild = interaction.guild

        if not has_level_role(member):
            await interaction.response.send_message(
                "Private voice chats are for Silly Critters and up. "
                "Keep chatting to reach level 3!",
                ephemeral=True,
            )
            return

        existing_id = db.get_voice_by_owner(guild.id, member.id)
        if existing_id:
            existing = guild.get_channel(existing_id)
            if existing:
                await interaction.response.send_message(
                    f"You already have a voice chat: {existing.mention}", ephemeral=True
                )
                return
            db.remove_voice(existing_id)

        # New voices are created in the same category as this channel
        category = getattr(interaction.channel, "category", None)
        view = VoiceSetupView(member, category)
        await interaction.response.send_message(
            embed=view.make_embed(), view=view, ephemeral=True
        )


# ======================================================================
#  Self-assign roles
# ======================================================================
class RoleGroupSelect(discord.ui.Select):
    """One menu for a group of roles (pronouns, colors, pings)."""

    def __init__(self, member: discord.Member, group: dict, placeholder: str, multiple: bool, row: int):
        # group: label -> Role (or None for the "None" option)
        held = set(member.roles)
        has_any = any(role in held for role in group.values() if role)
        options = []
        for label, role in group.items():
            selected = (role in held) if role else (not has_any)
            options.append(discord.SelectOption(label=label, value=label, default=selected))
        super().__init__(
            placeholder=placeholder,
            min_values=0,
            max_values=len(options) if multiple else 1,
            options=options,
            row=row,
        )
        self.group = group

    async def callback(self, interaction: discord.Interaction):
        member = interaction.user
        chosen = set(self.values)
        if "None" in chosen:
            chosen = set()

        wanted = {role for label, role in self.group.items() if role and label in chosen}
        group_roles = {role for role in self.group.values() if role}
        held = set(member.roles)
        to_add = list(wanted - held)
        to_remove = list((group_roles - wanted) & held)

        try:
            if to_add:
                await member.add_roles(*to_add, reason="Self-assigned roles")
            if to_remove:
                await member.remove_roles(*to_remove, reason="Self-assigned roles")
        except discord.Forbidden:
            await interaction.response.send_message(
                "I can't change those roles. Please tell a mod to move my role higher.",
                ephemeral=True,
            )
            return

        for option in self.options:
            if option.value == "None":
                option.default = not wanted
            else:
                option.default = option.value in chosen
        await interaction.response.edit_message(
            content="Saved! You can change this any time.", view=self.view
        )


class RolesPanelView(discord.ui.View):
    def __init__(self, member: discord.Member):
        super().__init__(timeout=600)
        guild = member.guild
        groups = [
            ("Choose your pronouns!", PRONOUN_ROLES, True, False),
            ("Choose your name color!", COLOR_ROLES, False, False),
            ("Choose your ping settings!", PING_ROLES, True, True),
        ]
        for row, (placeholder, config, multiple, has_none) in enumerate(groups):
            group = {"None": None} if has_none else {}
            for label, lookup in config:
                names = lookup if isinstance(lookup, (tuple, list)) else (lookup,)
                role = next((r for r in (find_role(guild, n) for n in names) if r), None)
                if role:
                    group[label] = role
                else:
                    print(f"Role not found for '{label}' (looked for {names})")
            if not any(group.values()):
                continue
            self.add_item(RoleGroupSelect(member, group, placeholder, multiple, row))


class GetRolesView(discord.ui.View):
    """The button posted in the get-roles channel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Get roles",
        style=discord.ButtonStyle.primary,
        custom_id="get_roles_button",
    )
    async def open_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="Your special roles",
            description=(
                "Pick what you like in the menus below. "
                "Changes are saved right away, and you can come back any time."
            ),
            colour=CREAM,
        )
        await interaction.response.send_message(
            embed=embed, view=RolesPanelView(interaction.user), ephemeral=True
        )


# ======================================================================
#  Bot
# ======================================================================
class SillyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.add_view(VerifyView())
        self.add_view(CreateVoiceView())
        self.add_view(GetRolesView())
        voice_tracker.start()
        voice_cleanup.start()
        # Sync commands to your server (shows up instantly)
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self):
        print(f"Bot is online as {self.user}")


client = SillyBot()

# Remembers when each person last got XP (for the cooldown)
last_xp_time: dict[tuple[int, int], float] = {}


# ======================================================================
#  Background tasks
# ======================================================================
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


@tasks.loop(minutes=5)
async def voice_cleanup():
    """Delete private voice chats that have been empty for too long."""
    guild = client.get_guild(GUILD_ID)
    if guild is None:
        return
    now = int(time.time())
    for channel_id, last_active in db.list_voices(guild.id):
        channel = guild.get_channel(channel_id)
        if channel is None:
            db.remove_voice(channel_id)
            continue
        if channel.members:
            db.touch_voice(channel_id, now)
            continue
        if now - last_active >= VOICE_IDLE_SECONDS:
            try:
                await channel.delete(reason="Private voice inactive for too long")
            except (discord.Forbidden, discord.HTTPException):
                continue
            db.remove_voice(channel_id)


@voice_cleanup.before_loop
async def before_voice_cleanup():
    await client.wait_until_ready()


# ======================================================================
#  Events
# ======================================================================
def build_welcome_embed(member: discord.Member) -> discord.Embed:
    verify_channel = member.guild.get_channel(VERIFY_CHANNEL_ID)
    verify_text = verify_channel.mention if verify_channel else "the verify channel"

    embed = discord.Embed(
        title="A new silly critter appeared!",
        description=(
            f"Haii {member.mention}, welcome to **{member.guild.name}**!\n\n"
            f"Hop over to {verify_text} and press the button to get access "
            f"to the rest of the server. Then grab a snack and be silly with us :3"
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

    old_xp, new_xp, _mups = db.add_message(message.guild.id, member.id, pings, gain)

    old_level = level_from_xp(old_xp)[0]
    new_level = level_from_xp(new_xp)[0]
    if new_level > old_level:
        granted = await sync_level_roles(member, new_level)
        text = f"{member.mention} just reached **level {new_level}**!"
        if granted:
            names = ", ".join(f"**{role.name}**" for role in granted)
            text += f" New role unlocked: {names}"
        await message.channel.send(text)


@client.event
async def on_voice_state_update(member, before, after):
    """Keep private voice chats alive while people come and go."""
    now = int(time.time())
    for state in (before, after):
        if state.channel is not None:
            db.touch_voice(state.channel.id, now)


# ======================================================================
#  Commands
# ======================================================================
@client.tree.command(name="ping", description="Check if the bot is awake")
async def ping(interaction: discord.Interaction):
    latency = round(client.latency * 1000)
    await interaction.response.send_message(f"Pong! {latency} ms")


@client.tree.command(name="hello", description="Say hi to the bot")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"Haii {interaction.user.mention}!")


@client.tree.command(name="profile", description="Show your profile or someone else's")
@app_commands.describe(member="Whose profile to show (leave empty for your own)")
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
    embed.add_field(name="Mups", value=f"{data['mups']:,}", inline=True)
    embed.add_field(name="Vups", value=f"{data['vups']:,}", inline=True)
    embed.add_field(name="Next reward", value=next_reward, inline=False)
    embed.set_footer(text=f"Total XP: {data['xp']:,}")

    await interaction.response.send_message(embed=embed)


def build_leaderboard_embed(guild: discord.Guild, kind: str) -> discord.Embed:
    """kind is 'xp', 'messages' or 'voice_seconds'."""
    titles = {
        "xp": "Top levels",
        "messages": "Top chatters",
        "voice_seconds": "Top voice time",
    }
    rows = db.top(guild.id, kind, 50)  # fetch extra in case some people left

    lines = []
    for user_id, xp, messages, voice_seconds in rows:
        member = guild.get_member(user_id)
        if member is None:
            continue
        if kind == "xp":
            value = f"Level {level_from_xp(xp)[0]} | {xp:,} XP"
        elif kind == "messages":
            value = f"{messages:,} messages"
        else:
            value = format_duration(voice_seconds)
        lines.append(f"**#{len(lines) + 1}** {member.display_name} | {value}")
        if len(lines) == 10:
            break

    return discord.Embed(
        title=titles[kind],
        description="\n".join(lines) if lines else "Nobody is on the board yet. Start chatting!",
        colour=PINK,
    )


@client.tree.command(name="leaderboard", description="Shows the top levels")
@app_commands.guild_only()
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_leaderboard_embed(interaction.guild, "xp"))


@client.tree.command(
    name="messagesleaderboard",
    description="Shows the top people who write the most messages",
)
@app_commands.guild_only()
async def messagesleaderboard(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=build_leaderboard_embed(interaction.guild, "messages")
    )


@client.tree.command(
    name="voiceleaderboard",
    description="Shows the top people who spent the most time in voice chats",
)
@app_commands.guild_only()
async def voiceleaderboard(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=build_leaderboard_embed(interaction.guild, "voice_seconds")
    )


@client.tree.command(name="shop", description="Visit the shop")
@app_commands.guild_only()
async def shop(interaction: discord.Interaction):
    data = db.get(interaction.guild.id, interaction.user.id)
    embed = discord.Embed(
        title="Silly Shop",
        description=(
            "The shelves are empty for now, but the critters are busy stocking them. "
            "Come back soon!"
        ),
        colour=CREAM,
    )
    embed.add_field(name="Your Mups", value=f"{data['mups']:,}", inline=True)
    embed.add_field(name="Your Vups", value=f"{data['vups']:,}", inline=True)
    embed.set_footer(text=f"{MUPS_REWARD} Mups for every {MESSAGES_PER_MUPS} messages that earn XP, "
                          f"{VUPS_REWARD} Vups for every hour in voice")
    await interaction.response.send_message(embed=embed)


# ----- Admin setup commands (run them inside the right channel) -----
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


@client.tree.command(name="setup_voice", description="Post the create-voice message here (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_voice(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Private voice chats",
        description=(
            "Click on the button below to create your own private voice chat!\n"
            "And configure it how you want!\n\n"
            "**Make sure to follow these simple rules:**\n"
            "• Do not create channels with inappropriate text in the name\n"
            "• Your channel will be deleted if it is inactive for more than 6 hours\n"
            "• If you join a private voice chat, you agree that we can't control what's "
            "going on in it. If you see something breaking the rules, report it with a "
            "clip, or call staff to check."
        ),
        colour=CREAM,
    )
    await interaction.channel.send(embed=embed, view=CreateVoiceView())
    await interaction.response.send_message("Posted.", ephemeral=True)


@client.tree.command(name="setup_roles", description="Post the get-roles message here (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_roles(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Special roles",
        description=(
            "Hi! Here you can get your special roles if you want them!\n"
            "Set up everything the way you like by clicking the button below.\n\n"
            "You can also keep yourself without all that, but if it fits you, go for it!"
        ),
        colour=CREAM,
    )
    await interaction.channel.send(embed=embed, view=GetRolesView())
    await interaction.response.send_message("Posted.", ephemeral=True)


@client.tree.command(name="test_welcome", description="Preview the welcome message (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def test_welcome(interaction: discord.Interaction):
    await interaction.response.send_message(
        content=interaction.user.mention,
        embed=build_welcome_embed(interaction.user),
        ephemeral=True,
    )


# ======================================================================
#  Hug and pat
# ======================================================================
ACTION_COOLDOWN = 15        # seconds between uses, counted per person and per command
ACTION_REPLY_SECONDS = 10   # how long the target has to answer

ACTIONS = {
    "hug": {
        "noun": "hug",
        "lines": [
            "{a} hugged {b}, how adorable!!",
            "{a} gave {b} a big warm hug, so cozy :3",
            "{a} wrapped {b} in a hug, the cutest!",
        ],
        "asked": "{a} just hugged you! Answer the same?",
        "back": "{b} hugged {a} back, so cozy :3",
    },
    "pat": {
        "noun": "pat",
        "lines": [
            "{a} patted {b} on the head, how sweet!",
            "{a} gave {b} some gentle headpats, so soft :3",
            "{a} pats {b}, what a good critter!",
        ],
        "asked": "{a} just patted you! Answer the same?",
        "back": "{b} patted {a} back, so soft :3",
    },
}

# When each person last used each command: (user_id, command) -> time
action_last_used: dict[tuple[int, str], float] = {}


class ActionReplyView(discord.ui.View):
    """The "Sure!" button. Only the person who got hugged or patted can press it."""

    def __init__(self, action: str, giver: discord.Member, target: discord.Member, first_line: str):
        super().__init__(timeout=ACTION_REPLY_SECONDS)
        self.action = action
        self.giver = giver
        self.target = target
        self.first_line = first_line
        self.message: discord.Message | None = None
        self.answered = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.target.id:
            return True
        await interaction.response.send_message("This one isn't for you!", ephemeral=True)
        return False

    @discord.ui.button(label="Sure!", style=discord.ButtonStyle.success)
    async def sure(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.answered = True
        self.stop()
        back = ACTIONS[self.action]["back"].format(a=self.giver.mention, b=self.target.mention)
        await interaction.response.edit_message(
            content=f"{self.first_line}\n{back}", view=None
        )

    async def on_timeout(self):
        if self.answered or self.message is None:
            return
        noun = ACTIONS[self.action]["noun"]
        ignored = f"{self.target.mention} didn't answer {self.giver.mention}'s {noun}..."
        try:
            await self.message.edit(content=f"{self.first_line}\n{ignored}", view=None)
        except (discord.NotFound, discord.HTTPException):
            pass


async def run_action(interaction: discord.Interaction, name: str, target: discord.Member):
    giver = interaction.user

    if target.id == giver.id:
        await interaction.response.send_message(
            f"You can't {name} yourself, pick someone else!", ephemeral=True
        )
        return
    if target.bot:
        await interaction.response.send_message(
            "Bots are too busy working, pick a person!", ephemeral=True
        )
        return

    # Cooldown (only the person who used the command sees this)
    now = time.time()
    key = (giver.id, name)
    remaining = ACTION_COOLDOWN - (now - action_last_used.get(key, 0))
    if remaining > 0:
        seconds = int(remaining) + 1
        unit = "second" if seconds == 1 else "seconds"
        await interaction.response.send_message(
            f"Command /{name} is on cooldown, try in {seconds} {unit}!", ephemeral=True
        )
        return
    action_last_used[key] = now

    config = ACTIONS[name]
    first_line = random.choice(config["lines"]).format(a=giver.mention, b=target.mention)
    asked = config["asked"].format(a=giver.mention)

    view = ActionReplyView(name, giver, target, first_line)
    await interaction.response.send_message(
        f"{first_line}\n{asked}",
        view=view,
        allowed_mentions=discord.AllowedMentions(users=[target]),  # only the target gets pinged
    )
    view.message = await interaction.original_response()


@client.tree.command(name="hug", description="Give someone a hug")
@app_commands.describe(user="Who do you want to hug?")
@app_commands.guild_only()
async def hug(interaction: discord.Interaction, user: discord.Member):
    await run_action(interaction, "hug", user)


@client.tree.command(name="pat", description="Give someone a pat")
@app_commands.describe(user="Who do you want to pat?")
@app_commands.guild_only()
async def pat(interaction: discord.Interaction, user: discord.Member):
    await run_action(interaction, "pat", user)


if not TOKEN:
    raise SystemExit("DISCORD_TOKEN environment variable is not set")

client.run(TOKEN)
