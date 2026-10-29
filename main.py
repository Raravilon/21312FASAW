import os
import random
import re
import sqlite3
import time
from collections import deque
from difflib import SequenceMatcher

import discord
from discord import app_commands
from discord.ext import tasks

# ======================================================================
#  Settings
# ======================================================================
TOKEN = os.getenv("DISCORD_TOKEN")

GUILD_ID = int(os.getenv("GUILD_ID", "1564401949019869224"))
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "1564405099004956702"))
VERIFY_CHANNEL_ID = int(os.getenv("VERIFY_CHANNEL_ID", "1564405761105461379"))

JOINED_ROLE_NAME = os.getenv("JOINED_ROLE_NAME", "Just joined!")
NEWCOMER_ROLE_NAME = os.getenv("NEWCOMER_ROLE_NAME", "Newcomer :3")

LEVEL_ROLES = [
    (3, "Silly Critter"),
    (10, "Friendly"),
    (15, "PAWSOME"),
]

XP_MIN = 15
XP_MAX = 25
XP_COOLDOWN = 60  

MIN_LETTERS = 4            
MIN_UNIQUE_LETTERS = 3     
SPAM_HISTORY = 5           
SIMILARITY_LIMIT = 0.85    

MESSAGES_PER_MUPS = 100   
MUPS_REWARD = 10          
VOICE_SECONDS_PER_VUPS = 3600  
VUPS_REWARD = 1                

VOICE_MAX_LIMIT = 10
VOICE_IDLE_SECONDS = 6 * 60 * 60  
BLOCKED_WORDS: list[str] = [
    "Nigger",
    "Niger",
    "Fuck",
    "sex",
    "NSFW",
    "18",
    "roleplay",
]

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
    ("Beige", ("Beige", "Biege")),  
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

DB_PATH = os.getenv("DB_PATH", "data/bot.db")

PINK = discord.Colour.from_str("#f4b6c2")
CREAM = discord.Colour.from_str("#f7e7d3")

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

    def _ensure(self, guild_id: int, user_id: int):
        self.conn.execute(
            "INSERT OR IGNORE INTO users (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )

    def add_message(self, guild_id: int, user_id: int, pings: int, xp_gain: int, counted: bool = True):
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
            "UPDATE users SET messages = messages + ?, pings = pings + ?, xp = xp + ?, "
            "xp_messages = ?, mups = mups + ? WHERE guild_id = ? AND user_id = ?",
            (1 if counted else 0, pings, xp_gain, new_xp_messages, mups_gain, guild_id, user_id),
        )
        self.conn.commit()
        return old_xp, old_xp + xp_gain, mups_gain

    def add_voice(self, guild_id: int, user_id: int, seconds: int):
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
        if column not in ("xp", "messages", "voice_seconds"):
            raise ValueError("bad column")
        return self.conn.execute(
            f"SELECT user_id, xp, messages, voice_seconds FROM users "
            f"WHERE guild_id = ? AND {column} > 0 ORDER BY {column} DESC LIMIT ?",
            (guild_id, limit),
        ).fetchall()

    def top_currency(self, guild_id: int, limit: int):
        return self.conn.execute(
            "SELECT user_id, mups FROM users WHERE guild_id = ? AND mups > 0 ORDER BY mups DESC LIMIT ?",
            (guild_id, limit),
        ).fetchall()

    def spend(self, guild_id: int, user_id: int, mups: int, vups: int) -> bool:
        self._ensure(guild_id, user_id)
        cursor = self.conn.execute(
            "UPDATE users SET mups = mups - ?, vups = vups - ? "
            "WHERE guild_id = ? AND user_id = ? AND mups >= ? AND vups >= ?",
            (mups, vups, guild_id, user_id, mups, vups),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    def refund(self, guild_id: int, user_id: int, mups: int, vups: int):
        self.conn.execute(
            "UPDATE users SET mups = mups + ?, vups = vups + ? "
            "WHERE guild_id = ? AND user_id = ?",
            (mups, vups, guild_id, user_id),
        )
        self.conn.commit()

    def add_currency(self, guild_id: int, user_id: int, mups: int, vups: int):
        self._ensure(guild_id, user_id)
        self.conn.execute(
            "UPDATE users SET mups = mups + ?, vups = vups + ? WHERE guild_id = ? AND user_id = ?",
            (mups, vups, guild_id, user_id)
        )
        self.conn.commit()

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
    return 5 * level * level + 50 * level + 100

def level_from_xp(total_xp: int):
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
    if member.guild_permissions.administrator:
        return True
    for _, role_name in LEVEL_ROLES:
        role = find_role(member.guild, role_name)
        if role and role in member.roles:
            return True
    return False

def find_channel_mention(guild: discord.Guild, keyword: str) -> str:
    channel = discord.utils.find(lambda c: keyword in c.name.lower(), guild.channels)
    return channel.mention if channel else f"#{keyword}"

# ======================================================================
#  Shop Data
# ======================================================================
SHOP_CATEGORIES = {
    "nickname": {
        "label": "Nickname Emojis",
        "items": [
            {"key": "blahaj_laying", "name": "Blahaj Laying", "emoji": "Blahaj_laying",
             "role": "Blahaj Laying", "description": "Classic?", "mups": 20, "vups": 0},
            {"key": "shy_boykisser", "name": "Shy Boykisser", "emoji": "Shy_kisser",
             "role": "Shy Boykisser", "description": "For the shy ones ~w~", "mups": 40, "vups": 0},
            {"key": "blahaj_boykisser", "name": "Blahaj Boykisser", "emoji": "BoykisserBlahaj",
             "role": "Blahaj Boykisser", "description": "Hugg da sharkw!", "mups": 40, "vups": 0},
            {"key": "paws_boykisser", "name": "Paws Boykisser", "emoji": "Paws_Boykisser",
             "role": "Paws Boykisser", "description": "Show them your paws", "mups": 90, "vups": 5},
            {"key": "kiss_boykisser", "name": "Kiss boykisser", "emoji": "kissing_boykisser",
             "role": "Kiss boykisser", "description": "Kissing boykisser near you!", "mups": 120, "vups": 20},
            {"key": "halloween_pumpkin_cat", "name": "Halloween Pumpkin Cat", "emoji": "halloween_pupkin_cat",
             "role": "Halloween Pumpkin", "description": "Spooky month, silly critters!", "mups": 45, "vups": 5},
            {"key": "sus_cat", "name": "Sus cat", "emoji": "Cat_Sus",
             "role": "Sus cat", "description": "Looks suspicious.... Is probably fine...?", "mups": 300, "vups": 100},
        ],
    },
    "special": {
        "label": "Special Roles",
        "items": [],
    },
}

# ======================================================================
#  Self-assign roles & Profile Edit
# ======================================================================
class RoleGroupSelect(discord.ui.Select):
    def __init__(self, member: discord.Member, group: dict, placeholder: str, multiple: bool, row: int):
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
        
        await interaction.response.edit_message(content="Saved! You can change this any time.", view=self.view)

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


# --- Изменение профиля (только свои цвета, местоимения, КУПЛЕННЫЕ эмодзи и имя) ---

class EmojiSelect(discord.ui.Select):
    def __init__(self, member: discord.Member, guild: discord.Guild):
        self.all_emoji_roles = []
        options = [discord.SelectOption(label="None", value="None")]
        
        for item in SHOP_CATEGORIES["nickname"]["items"]:
            role = find_role(guild, item["role"])
            if role:
                self.all_emoji_roles.append(role)
                if role in member.roles:
                    options.append(discord.SelectOption(label=item["name"], value=str(role.id), default=True))
        
        if len(options) == 1:
            options = [discord.SelectOption(label="You don't own any name emojis", value="None")]
            super().__init__(placeholder="Edit your Name emoji!", options=options, disabled=True, row=2)
        else:
            # Убираем дефолт с None, если что-то уже выбрано
            has_any = any(opt.default for opt in options if opt.value != "None")
            options[0].default = not has_any
            super().__init__(placeholder="Edit your Name emoji!", min_values=1, max_values=1, options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        member = interaction.user
        val = self.values[0]
        
        wanted = None
        if val != "None":
            wanted = interaction.guild.get_role(int(val))

        held = set(member.roles)
        to_remove = [r for r in self.all_emoji_roles if r in held and r != wanted]
        to_add = [wanted] if wanted and wanted not in held else []

        try:
            if to_remove: await member.remove_roles(*to_remove, reason="Changed name emoji")
            if to_add: await member.add_roles(*to_add, reason="Changed name emoji")
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to change these roles.", ephemeral=True)
            return
        
        for option in self.options:
            option.default = (option.value == val)

        await interaction.response.edit_message(content="Saved! Name emoji updated.", view=self.view)

class NameChangeModal(discord.ui.Modal, title="Change your name (100 Mups)"):
    new_name = discord.ui.TextInput(
        label="New Name",
        placeholder="Enter your new nickname...",
        max_length=32,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        member = interaction.user
        guild = interaction.guild
        
        data = db.get(guild.id, member.id)
        if data["mups"] < 100:
            await interaction.response.send_message("You don't have 100 Mups for this!", ephemeral=True)
            return
        
        try:
            await member.edit(nick=self.new_name.value.strip(), reason="Bought name change")
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to change your name! (You might be a higher rank than me).", ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.response.send_message("Failed to change name.", ephemeral=True)
            return
        
        db.spend(guild.id, member.id, 100, 0)
        await interaction.response.send_message(f"Your name has been changed to **{self.new_name.value.strip()}** for 100 Mups!", ephemeral=True)

class EditNameButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Edit your name! (100 Mups)", style=discord.ButtonStyle.success, row=3)
        
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(NameChangeModal())

class EditProfilePanel(discord.ui.View):
    def __init__(self, member: discord.Member):
        super().__init__(timeout=600)
        guild = member.guild
        
        # 1. Colors
        color_group = {"None": None}
        for label, lookup in COLOR_ROLES:
            names = lookup if isinstance(lookup, (tuple, list)) else (lookup,)
            role = next((r for r in (find_role(guild, n) for n in names) if r), None)
            if role: color_group[label] = role
        if len(color_group) > 1:
            self.add_item(RoleGroupSelect(member, color_group, "Edit your Name color!", False, 0))

        # 2. Pronouns
        pronoun_group = {"None": None}
        for label, lookup in PRONOUN_ROLES:
            names = lookup if isinstance(lookup, (tuple, list)) else (lookup,)
            role = next((r for r in (find_role(guild, n) for n in names) if r), None)
            if role: pronoun_group[label] = role
        if len(pronoun_group) > 1:
            self.add_item(RoleGroupSelect(member, pronoun_group, "Edit your pronouns!", True, 1))
            
        # 3. Name Emojis (Only owned)
        self.add_item(EmojiSelect(member, guild))
        
        # 4. Change Name
        self.add_item(EditNameButton())

class ProfileView(discord.ui.View):
    def __init__(self, member: discord.Member):
        super().__init__(timeout=600)
        self.member = member

    @discord.ui.button(label="Edit Server Profile!", style=discord.ButtonStyle.primary)
    async def edit_profile(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("You can only edit your own profile!", ephemeral=True)
            return
        
        view = EditProfilePanel(interaction.user)
        embed = discord.Embed(
            title="Edit Server Profile",
            description="Change your roles, emojis, and name here!",
            colour=CREAM
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ======================================================================
#  Verification
# ======================================================================
class ConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="I agree, let me in", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user
        newcomer_role = await get_role(guild, NEWCOMER_ROLE_NAME)
        joined_role = find_role(guild, JOINED_ROLE_NAME)
        try:
            if newcomer_role: await member.add_roles(newcomer_role, reason="Passed verification")
            if joined_role and joined_role in member.roles: await member.remove_roles(joined_role, reason="Passed verification")
        except discord.Forbidden:
            await interaction.response.edit_message(content="I can't change your roles. Please tell a mod to move my role higher.", view=None)
            return
        await interaction.response.edit_message(content="You're verified! Welcome to the server, have fun and be silly :3", view=None)

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.primary, custom_id="silly_verify_button")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user
        newcomer_role = find_role(guild, NEWCOMER_ROLE_NAME)
        if newcomer_role and newcomer_role in member.roles:
            await interaction.response.send_message("You're already verified!", ephemeral=True)
            return
        rules = find_channel_mention(guild, "rules")
        know = find_channel_mention(guild, "what-to-know")
        await interaction.response.send_message(
            f"Clicking on verify, you accept that you have read channels: {rules} and {know}.\nPress the button below to confirm.",
            view=ConfirmView(), ephemeral=True
        )

# ======================================================================
#  Private voice chats
# ======================================================================
class NameLimitModal(discord.ui.Modal, title="Your voice chat"):
    name_input = discord.ui.TextInput(label="What's your voice name?", placeholder="Cozy corner", max_length=50, required=True)
    limit_input = discord.ui.TextInput(label="How much is limit for people in your voice?", placeholder=f"A number from 1 to {VOICE_MAX_LIMIT}", max_length=2, required=True)

    def __init__(self, parent: "VoiceSetupView"):
        super().__init__()
        self.parent = parent
        if parent.name: self.name_input.default = parent.name
        if parent.limit: self.limit_input.default = str(parent.limit)

    async def on_submit(self, interaction: discord.Interaction):
        name = self.name_input.value.strip()
        if not name:
            await interaction.response.send_message("The name can't be empty.", ephemeral=True)
            return
        try: limit = int(self.limit_input.value.strip())
        except ValueError:
            await interaction.response.send_message(f"The limit must be a number from 1 to {VOICE_MAX_LIMIT}.", ephemeral=True)
            return
        if not 1 <= limit <= VOICE_MAX_LIMIT:
            await interaction.response.send_message(f"The limit must be a number from 1 to {VOICE_MAX_LIMIT}.", ephemeral=True)
            return
        if is_blocked_name(name):
            await interaction.response.send_message("Please choose a different name, that one isn't allowed.", ephemeral=True)
            return

        self.parent.name = name
        self.parent.limit = limit
        await interaction.response.edit_message(embed=self.parent.make_embed(), view=self.parent)

class VoiceSetupView(discord.ui.View):
    def __init__(self, owner: discord.Member, category: discord.CategoryChannel | None):
        super().__init__(timeout=600)
        self.owner = owner
        self.category = category
        self.name: str | None = None
        self.limit: int | None = None
        self.access = "public"
        self.invited: list[discord.Member] = []

    def make_embed(self) -> discord.Embed:
        embed = discord.Embed(title="Create your voice chat", description="Set everything up, then press **Confirm, create voice!**", colour=CREAM)
        embed.add_field(name="What's your voice name?", value=self.name or "Not set yet", inline=False)
        embed.add_field(name="How much is limit for people in your voice?", value=str(self.limit) if self.limit else f"Not set yet (1 to {VOICE_MAX_LIMIT})", inline=False)
        if self.access == "public": access_text = "**Public**: all Newcomers and Silly Critters can join"
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
        ], row=0
    )
    async def access_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.access = select.values[0]
        for option in select.options: option.default = option.value == self.access
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Private only: who can join? (up to 10)", min_values=0, max_values=10, row=1)
    async def invite_select(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        self.invited = [user for user in select.values if not user.bot and user.id != self.owner.id]
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="Set name and limit", style=discord.ButtonStyle.secondary, row=2)
    async def set_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(NameLimitModal(self))

    @discord.ui.button(label="Confirm, create voice!", style=discord.ButtonStyle.success, row=2)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.name is None or self.limit is None:
            await interaction.response.send_message("Please press **Set name and limit** first.", ephemeral=True)
            return
        guild = interaction.guild
        existing_id = db.get_voice_by_owner(guild.id, self.owner.id)
        if existing_id:
            existing = guild.get_channel(existing_id)
            if existing:
                await interaction.response.edit_message(content=f"You already have a voice chat: {existing.mention}", embed=None, view=None)
                return
            db.remove_voice(existing_id)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True, move_members=True),
            self.owner: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, manage_channels=True, manage_permissions=True, move_members=True, mute_members=True),
        }
        if self.access == "public":
            for role_name in (NEWCOMER_ROLE_NAME, LEVEL_ROLES[0][1]):
                role = find_role(guild, role_name)
                if role: overwrites[role] = discord.PermissionOverwrite(view_channel=True, connect=True)
        else:
            for user in self.invited: overwrites[user] = discord.PermissionOverwrite(view_channel=True, connect=True)

        try:
            channel = await guild.create_voice_channel(name=self.name, user_limit=self.limit, category=self.category, overwrites=overwrites, reason=f"Private voice created by {self.owner}")
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.edit_message(content="I couldn't create the channel. Please tell a mod.", embed=None, view=None)
            return

        db.add_voice_channel(guild.id, channel.id, self.owner.id, int(time.time()))
        self.stop()
        await interaction.response.edit_message(content=f"Your voice chat is ready: {channel.mention}", embed=None, view=None)
        if self.owner.voice is not None:
            try: await self.owner.move_to(channel)
            except (discord.Forbidden, discord.HTTPException): pass

class CreateVoiceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Voice", style=discord.ButtonStyle.primary, custom_id="create_voice_button")
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        guild = interaction.guild
        if not has_level_role(member):
            await interaction.response.send_message("Private voice chats are for Silly Critters and up. Keep chatting to reach level 3!", ephemeral=True)
            return

        existing_id = db.get_voice_by_owner(guild.id, member.id)
        if existing_id:
            existing = guild.get_channel(existing_id)
            if existing:
                await interaction.response.send_message(f"You already have a voice chat: {existing.mention}", ephemeral=True)
                return
            db.remove_voice(existing_id)

        category = getattr(interaction.channel, "category", None)
        view = VoiceSetupView(member, category)
        await interaction.response.send_message(embed=view.make_embed(), view=view, ephemeral=True)


class GetRolesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Get roles", style=discord.ButtonStyle.primary, custom_id="get_roles_button")
    async def open_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="Your special roles", description="Pick what you like in the menus below. Changes are saved right away, and you can come back any time.", colour=CREAM)
        await interaction.response.send_message(embed=embed, view=RolesPanelView(interaction.user), ephemeral=True)


# ======================================================================
#  Bot Initialization
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
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self):
        print(f"Bot is online as {self.user}")

client = SillyBot()

last_xp_time: dict[tuple[int, int], float] = {}
recent_messages: dict[tuple[int, int], deque] = {}

URL_RE = re.compile(r"https?://\S+")
DISCORD_TAG_RE = re.compile(r"<a?:\w+:\d+>|<[@#][!&]?\d+>")  

def letters_only(text: str) -> str:
    text = URL_RE.sub("", text)
    text = DISCORD_TAG_RE.sub("", text)
    return re.sub(r"[\W\d_]+", "", text).lower()

def is_real_message(key: tuple[int, int], content: str) -> bool:
    letters = letters_only(content)
    if len(letters) < MIN_LETTERS or len(set(letters)) < MIN_UNIQUE_LETTERS: return False
    history = recent_messages.setdefault(key, deque(maxlen=SPAM_HISTORY))
    for old in history:
        if SequenceMatcher(None, old, letters).ratio() >= SIMILARITY_LIMIT: return False
    history.append(letters)
    return True

# ======================================================================
#  Background tasks
# ======================================================================
@tasks.loop(seconds=60)
async def voice_tracker():
    guild = client.get_guild(GUILD_ID)
    if guild is None: return
    for channel in guild.voice_channels + guild.stage_channels:
        if channel == guild.afk_channel: continue
        humans = [m for m in channel.members if not m.bot]
        if len(humans) < 2: continue
        for member in humans:
            state = member.voice
            if state is None or state.channel != channel: continue
            if state.deaf or state.self_deaf: continue
            db.add_voice(guild.id, member.id, 60)

@voice_tracker.before_loop
async def before_voice_tracker():
    await client.wait_until_ready()

@tasks.loop(minutes=5)
async def voice_cleanup():
    guild = client.get_guild(GUILD_ID)
    if guild is None: return
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
            try: await channel.delete(reason="Private voice inactive for too long")
            except (discord.Forbidden, discord.HTTPException): continue
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
        description=f"Haii {member.mention}, welcome to **{member.guild.name}**!\n\nHop over to {verify_text} and press the button to get access to the rest of the server. Then grab a snack and be silly with us :3",
        colour=PINK,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"You are member #{member.guild.member_count}")
    return embed

@client.event
async def on_member_join(member: discord.Member):
    if member.guild.id != GUILD_ID or member.bot: return
    joined_role = await get_role(member.guild, JOINED_ROLE_NAME)
    if joined_role:
        try: await member.add_roles(joined_role, reason="New member")
        except discord.Forbidden: print("Cannot give the 'Just joined!' role: move the bot role higher.")
    channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if channel is not None:
        await channel.send(content=member.mention, embed=build_welcome_embed(member))

@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None or message.guild.id != GUILD_ID: return
    member = message.author
    pings = len({m.id for m in message.mentions if not m.bot and m.id != member.id})
    key = (message.guild.id, member.id)
    counted = is_real_message(key, message.content)

    now = time.time()
    gain = 0
    if counted and now - last_xp_time.get(key, 0) >= XP_COOLDOWN:
        gain = random.randint(XP_MIN, XP_MAX)
        last_xp_time[key] = now

    old_xp, new_xp, _mups = db.add_message(message.guild.id, member.id, pings, gain, counted)
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
    target_member = member or interaction.user

    if target_member.bot:
        await interaction.response.send_message("Bots don't have profiles, they are too busy working.", ephemeral=True)
        return

    data = db.get(interaction.guild.id, target_member.id)
    level, into_level, needed = level_from_xp(data["xp"])
    rank = db.rank(interaction.guild.id, data["xp"])

    next_reward = "All roles unlocked"
    for required_level, role_name in LEVEL_ROLES:
        if level < required_level:
            next_reward = f"{role_name} at level {required_level}"
            break

    colour = target_member.colour if target_member.colour.value else PINK
    embed = discord.Embed(
        title=f"{target_member.display_name}'s profile",
        description=f"**Level {level}**  |  Rank **#{rank}**\n`{progress_bar(into_level, needed)}`  {into_level:,} / {needed:,} XP",
        colour=colour,
    )
    embed.set_thumbnail(url=target_member.display_avatar.url)
    embed.add_field(name="Messages sent", value=f"{data['messages']:,}", inline=True)
    embed.add_field(name="Pings sent", value=f"{data['pings']:,}", inline=True)
    embed.add_field(name="Time in voice", value=format_duration(data["voice_seconds"]), inline=True)
    embed.add_field(name="Mups", value=f"{data['mups']:,}", inline=True)
    embed.add_field(name="Vups", value=f"{data['vups']:,}", inline=True)
    embed.add_field(name="Next reward", value=next_reward, inline=False)
    embed.set_footer(text=f"Total XP: {data['xp']:,}")

    # Если мы смотрим СВОЙ профиль, добавляем кнопку Edit
    if target_member.id == interaction.user.id:
        view = ProfileView(target_member)
        await interaction.response.send_message(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed)


def build_leaderboard_embed(guild: discord.Guild, kind: str) -> discord.Embed:
    titles = {"xp": "Top levels", "messages": "Top chatters", "voice_seconds": "Top voice time"}
    rows = db.top(guild.id, kind, 50)  
    lines = []
    for user_id, xp, messages, voice_seconds in rows:
        member = guild.get_member(user_id)
        if member is None: continue
        if kind == "xp": value = f"Level {level_from_xp(xp)[0]} | {xp:,} XP"
        elif kind == "messages": value = f"{messages:,} messages"
        else: value = format_duration(voice_seconds)
        lines.append(f"**#{len(lines) + 1}** {member.display_name} | {value}")
        if len(lines) == 10: break

    return discord.Embed(title=titles[kind], description="\n".join(lines) if lines else "Nobody is on the board yet. Start chatting!", colour=PINK)

@client.tree.command(name="leaderboard", description="Shows the top levels")
@app_commands.guild_only()
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_leaderboard_embed(interaction.guild, "xp"))

@client.tree.command(name="messagesleaderboard", description="Shows the top people who write the most messages")
@app_commands.guild_only()
async def messagesleaderboard(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_leaderboard_embed(interaction.guild, "messages"))

@client.tree.command(name="voiceleaderboard", description="Shows the top people who spent the most time in voice chats")
@app_commands.guild_only()
async def voiceleaderboard(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_leaderboard_embed(interaction.guild, "voice_seconds"))

@client.tree.command(name="currencyleaderboard", description="Shows the top 15 richest people by Mups")
@app_commands.guild_only()
async def currencyleaderboard(interaction: discord.Interaction):
    guild = interaction.guild
    rows = db.top_currency(guild.id, 15)
    lines = []
    for user_id, mups in rows:
        member = guild.get_member(user_id)
        if member is None: continue
        lines.append(f"**#{len(lines) + 1}** {member.display_name} | {mups:,} Mups")
    embed = discord.Embed(title="Top Richest Critters (Mups)", description="\n".join(lines) if lines else "Nobody has Mups yet!", colour=PINK)
    await interaction.response.send_message(embed=embed)


# ======================================================================
#  Shop Command
# ======================================================================
SHOP_EMPTY_TEXT = "There is nothing right now! Sowwee ~w~"

def find_emoji(guild: discord.Guild, name: str) -> discord.Emoji | None:
    lowered = name.lower()
    for emoji in guild.emojis:
        if emoji.name.lower() == lowered: return emoji
    return None

def shop_item_text(guild: discord.Guild, item: dict) -> str:
    emoji = find_emoji(guild, item["emoji"])
    icon = f" {emoji}" if emoji else ""
    return f"**@Nickname{icon} — {item['name']}!**\n{item['description']}\nPrice: {item['mups']} Mups | {item['vups']} Vups"

class ShopCategorySelect(discord.ui.Select):
    def __init__(self, shop: "ShopView"):
        options = [discord.SelectOption(label=category["label"], value=key, default=(key == shop.category)) for key, category in SHOP_CATEGORIES.items()]
        super().__init__(placeholder="Choose a category!", options=options, row=0)
        self.shop = shop

    async def callback(self, interaction: discord.Interaction):
        self.shop.category = self.values[0]
        self.shop.selected = None
        self.shop.notice = None
        self.shop.build()
        await interaction.response.edit_message(embed=self.shop.make_embed(), view=self.shop)

class ShopItemSelect(discord.ui.Select):
    def __init__(self, shop: "ShopView"):
        guild = shop.member.guild
        options = [
            discord.SelectOption(
                label=item["name"], value=item["key"], description=f"{item['mups']} Mups | {item['vups']} Vups",
                emoji=find_emoji(guild, item["emoji"]), default=(item["key"] == shop.selected)
            ) for item in shop.items()
        ]
        super().__init__(placeholder="Choose what to buy!", options=options, row=1)
        self.shop = shop

    async def callback(self, interaction: discord.Interaction):
        self.shop.selected = self.values[0]
        self.shop.build()
        await interaction.response.edit_message(embed=self.shop.make_embed(), view=self.shop)

class ShopBuyButton(discord.ui.Button):
    def __init__(self, shop: "ShopView"):
        super().__init__(label="Buy", style=discord.ButtonStyle.success, row=2)
        self.shop = shop

    async def callback(self, interaction: discord.Interaction):
        shop = self.shop
        item = shop.selected_item()
        if item is None:
            await interaction.response.send_message("Pick something to buy first.", ephemeral=True)
            return

        guild = interaction.guild
        member = interaction.user
        role = find_role(guild, item["role"])
        if role is None:
            await interaction.response.send_message("I can't find that role right now. Please tell a mod.", ephemeral=True)
            return
        if role in member.roles:
            await interaction.response.send_message("You already own this one.", ephemeral=True)
            return

        data = db.get(guild.id, member.id)
        missing_mups = max(0, item["mups"] - data["mups"])
        missing_vups = max(0, item["vups"] - data["vups"])
        if missing_mups or missing_vups:
            parts = []
            if missing_mups: parts.append(f"{missing_mups} more Mups")
            if missing_vups: parts.append(f"{missing_vups} more Vups")
            await interaction.response.send_message(f"You need {' and '.join(parts)}.", ephemeral=True)
            return

        if not db.spend(guild.id, member.id, item["mups"], item["vups"]):
            await interaction.response.send_message("You don't have enough for that.", ephemeral=True)
            return

        try: await member.add_roles(role, reason=f"Bought in the shop: {item['name']}")
        except (discord.Forbidden, discord.HTTPException):
            db.refund(guild.id, member.id, item["mups"], item["vups"])
            await interaction.response.send_message("I can't give you that role. Please tell a mod to move my role higher.", ephemeral=True)
            return

        shop.notice = f"You bought {item['name']}!"
        shop.selected = None
        shop.build()
        await interaction.response.edit_message(embed=shop.make_embed(), view=shop)

class ShopView(discord.ui.View):
    def __init__(self, member: discord.Member):
        super().__init__(timeout=600)
        self.member = member
        self.category: str | None = None
        self.selected: str | None = None
        self.notice: str | None = None
        self.build()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.member.id: return True
        await interaction.response.send_message("Это не твой магазин! Напиши /shop, чтобы открыть свой.", ephemeral=True)
        return False

    def items(self) -> list[dict]:
        if self.category is None: return []
        return SHOP_CATEGORIES[self.category]["items"]

    def selected_item(self) -> dict | None:
        for item in self.items():
            if item["key"] == self.selected: return item
        return None

    def build(self):
        self.clear_items()
        self.add_item(ShopCategorySelect(self))
        if self.items():
            self.add_item(ShopItemSelect(self))
            self.add_item(ShopBuyButton(self))

    def make_embed(self) -> discord.Embed:
        guild = self.member.guild
        data = db.get(guild.id, self.member.id)
        if self.category is None:
            embed = discord.Embed(title="Silly Shop", description="Welcome in! Pick a category below to see what's on the shelves.", colour=CREAM)
            embed.set_footer(text=f"{MUPS_REWARD} Mups for every {MESSAGES_PER_MUPS} messages that earn XP, {VUPS_REWARD} Vups for every hour in voice")
        else:
            category = SHOP_CATEGORIES[self.category]
            if category["items"]: body = "\n\n".join(shop_item_text(guild, item) for item in category["items"])
            else: body = SHOP_EMPTY_TEXT
            text = f"{self.notice}\n\n{body}" if self.notice else body
            embed = discord.Embed(title=category["label"], description=text, colour=CREAM)
        embed.add_field(name="Your Mups", value=f"{data['mups']:,}", inline=True)
        embed.add_field(name="Your Vups", value=f"{data['vups']:,}", inline=True)
        return embed

@client.tree.command(name="shop", description="Visit the shop")
@app_commands.guild_only()
async def shop(interaction: discord.Interaction):
    view = ShopView(interaction.user)
    await interaction.response.send_message(embed=view.make_embed(), view=view)


# ======================================================================
#  Admin commands (Setup & Give Currency)
# ======================================================================
@client.tree.command(name="give", description="Give Mups or Vups to a user (Admins/Owner only)")
@app_commands.describe(user="The user to give currency to", amount="Amount to give", currency="Which currency?")
@app_commands.choices(currency=[
    app_commands.Choice(name="Mups", value="mups"),
    app_commands.Choice(name="Vups", value="vups"),
])
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def give_currency(interaction: discord.Interaction, user: discord.Member, amount: int, currency: app_commands.Choice[str]):
    # Дополнительная проверка: только Создатель или Админы могут использовать эту команду
    if interaction.user.id != interaction.guild.owner_id and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Only the server creator or admins can do this.", ephemeral=True)
        return
        
    if amount <= 0:
        await interaction.response.send_message("Amount must be a positive number.", ephemeral=True)
        return
        
    if currency.value == "mups":
        db.add_currency(interaction.guild.id, user.id, amount, 0)
    else:
        db.add_currency(interaction.guild.id, user.id, 0, amount)
        
    await interaction.response.send_message(f"Successfully gave {amount} {currency.name} to {user.mention}!", ephemeral=True)


@client.tree.command(name="setup_verify", description="Post the verification message (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_verify(interaction: discord.Interaction):
    channel = interaction.guild.get_channel(VERIFY_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message("I can't find the verify channel.", ephemeral=True)
        return
    embed = discord.Embed(title="Verification", description="Welcome, little critter! Press the button below to verify and get access to the rest of the server.", colour=CREAM)
    await channel.send(embed=embed, view=VerifyView())
    await interaction.response.send_message(f"Verification posted in {channel.mention}.", ephemeral=True)

@client.tree.command(name="setup_voice", description="Post the create-voice message here (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_voice(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Private voice chats",
        description="Click on the button below to create your own private voice chat!\nAnd configure it how you want!\n\n**Make sure to follow these simple rules:**\n• Do not create channels with inappropriate text in the name\n• Your channel will be deleted if it is inactive for more than 6 hours\n• If you join a private voice chat, you agree that we can't control what's going on in it. If you see something breaking the rules, report it with a clip, or call staff to check.",
        colour=CREAM,
    )
    await interaction.channel.send(embed=embed, view=CreateVoiceView())
    await interaction.response.send_message("Posted.", ephemeral=True)

@client.tree.command(name="setup_roles", description="Post the get-roles message here (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_roles(interaction: discord.Interaction):
    embed = discord.Embed(title="Special roles", description="Hi! Here you can get your special roles if you want them!\nSet up everything the way you like by clicking the button below.\n\nYou can also keep yourself without all that, but if it fits you, go for it!", colour=CREAM)
    await interaction.channel.send(embed=embed, view=GetRolesView())
    await interaction.response.send_message("Posted.", ephemeral=True)

@client.tree.command(name="test_welcome", description="Preview the welcome message (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def test_welcome(interaction: discord.Interaction):
    await interaction.response.send_message(content=interaction.user.mention, embed=build_welcome_embed(interaction.user), ephemeral=True)


# ======================================================================
#  Hug and pat
# ======================================================================
ACTION_COOLDOWN = 15        
ACTION_REPLY_SECONDS = 10   

# Заменены на рабочие ссылки от Tenor!
PAT_GIFS: list[str] = [
    "https://media1.tenor.com/m/la-8MgilWWAAAAAC/furry-furry-cuddle.gif",
    "https://media1.tenor.com/m/FraPRlGRUSQAAAAC/crystal-the-cavern-spirit.gif",
    "https://media1.tenor.com/m/jfPxGhnzSe4AAAAd/furry-pet.gif",
    "https://media1.tenor.com/m/Oo81tnLM6jcAAAAC/boykisser-cute.gif",
    "https://media1.tenor.com/m/iV_Oz43GbZ4AAAAC/yumiki-yumi.gif",
    "https://media1.tenor.com/m/8kvNhB9VejwAAAAC/heat-pat-pat-furry.gif",
    "https://media1.tenor.com/m/-9ywIhI-dbcAAAAd/good-boy.gif",
    "https://media1.tenor.com/m/-jJlxJOR2yIAAAAC/moose-protogen.gif"
]
HUG_GIFS: list[str] = [
    "https://media1.tenor.com/m/slJASMK2qooAAAAC/furry-boykisser.gif",
    "https://media1.tenor.com/m/uNmdXKUxI3QAAAAC/boykisser.gif",
    "https://media1.tenor.com/m/bhXYOTy2FGIAAAAC/cute-cuddle.gif"
]

ACTIONS = {
    "hug": {
        "noun": "hug",
        "lines": ["{a} hugged {b}, how adorable!!", "{a} gave {b} a big warm hug, so cozy :3", "{a} wrapped {b} in a hug, the cutest!"],
        "asked": "{a} just hugged you! Answer the same?",
        "back": "{b} hugged {a} back, so cozy :3",
        "gifs": HUG_GIFS,
    },
    "pat": {
        "noun": "pat",
        "lines": ["{a} patted {b} on the head, how sweet!", "{a} gave {b} some gentle headpats, so soft :3", "{a} pats {b}, what a good critter!"],
        "asked": "{a} just patted you! Answer the same?",
        "back": "{b} patted {a} back, so soft :3",
        "gifs": PAT_GIFS,
    },
}

action_last_used: dict[tuple[int, str], float] = {}

def pick_gif(action: str) -> str | None:
    pool = ACTIONS[action]["gifs"]
    return random.choice(pool) if pool else None

def build_action_embed(text: str, image_url: str | None) -> discord.Embed:
    embed = discord.Embed(description=text, colour=PINK)
    if image_url: embed.set_image(url=image_url)
    return embed

class ActionReplyView(discord.ui.View):
    def __init__(self, action: str, giver: discord.Member, target: discord.Member, first_line: str, image_url: str | None):
        super().__init__(timeout=ACTION_REPLY_SECONDS)
        self.action = action
        self.giver = giver
        self.target = target
        self.first_line = first_line
        self.image_url = image_url
        self.message: discord.Message | None = None
        self.answered = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.target.id: return True
        await interaction.response.send_message("This one isn't for you!", ephemeral=True)
        return False

    @discord.ui.button(label="Sure!", style=discord.ButtonStyle.success)
    async def sure(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.answered = True
        self.stop()
        back = ACTIONS[self.action]["back"].format(a=self.giver.mention, b=self.target.mention)
        embed = build_action_embed(f"{self.first_line}\n{back}", self.image_url)
        await interaction.response.edit_message(embed=embed, view=None)

    async def on_timeout(self):
        if self.answered or self.message is None: return
        noun = ACTIONS[self.action]["noun"]
        ignored = f"{self.target.mention} didn't answer {self.giver.mention}'s {noun}..."
        embed = build_action_embed(f"{self.first_line}\n{ignored}", self.image_url)
        try: await self.message.edit(embed=embed, view=None)
        except (discord.NotFound, discord.HTTPException): pass

async def run_action(interaction: discord.Interaction, name: str, target: discord.Member):
    giver = interaction.user
    if target.id == giver.id:
        await interaction.response.send_message(f"You can't {name} yourself, pick someone else!", ephemeral=True)
        return
    if target.bot:
        await interaction.response.send_message("Bots are too busy working, pick a person!", ephemeral=True)
        return

    now = time.time()
    key = (giver.id, name)
    remaining = ACTION_COOLDOWN - (now - action_last_used.get(key, 0))
    if remaining > 0:
        seconds = int(remaining) + 1
        unit = "second" if seconds == 1 else "seconds"
        await interaction.response.send_message(f"Command /{name} is on cooldown, try in {seconds} {unit}!", ephemeral=True)
        return
    action_last_used[key] = now

    config = ACTIONS[name]
    first_line = random.choice(config["lines"]).format(a=giver.mention, b=target.mention)
    asked = config["asked"].format(a=giver.mention)
    image_url = pick_gif(name)

    view = ActionReplyView(name, giver, target, first_line, image_url)
    await interaction.response.send_message(
        content=target.mention,
        embed=build_action_embed(f"{first_line}\n{asked}", image_url),
        view=view, allowed_mentions=discord.AllowedMentions(users=[target])
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
