import os
import random
import re
import sqlite3
import time
import datetime
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

# Optional: channel for bot warnings to moderators (0 = look for a channel
# named "mod-log" or "moderator-only")
MOD_LOG_CHANNEL_ID = int(os.getenv("MOD_LOG_CHANNEL_ID", "0"))

JOINED_ROLE_NAME = os.getenv("JOINED_ROLE_NAME", "Just joined!")
NEWCOMER_ROLE_NAME = os.getenv("NEWCOMER_ROLE_NAME", "Newcomer :3")

# Level rewards. Names are matched ignoring case, emojis and punctuation, so
# "MAPLE SYROUP" finds "🍁 MAPLE SYROUP 🍁".
# IMPORTANT: the level-85 role is "Maplet..........." (with dots). It is looked up as
# "Maplet..." so it can never be confused with the shop role called just "Maplet".
LEVEL_ROLES = [
    (3, "Silly Critter"),
    (10, "Friendly"),
    (15, "PAWSOME"),
    (50, "Half of a Goober"),
    (85, "Maplet..."),
    (100, "MAPLE SYROUP"),
]

# ----------------------------------------------------------------------
#  XP system
# ----------------------------------------------------------------------
XP_MIN = 10
XP_MAX = 25
XP_COOLDOWN_RANGE = (15, 40)    # random seconds between two XP gains (hidden from players)

# Fatigue: the more XP gains in the last hour, the less each one is worth.
# (gains in the last hour below this number, multiplier). No hard wall.
FATIGUE_STEPS = [(30, 1.0), (50, 0.7), (70, 0.4), (10**9, 0.15)]

# Message quality (by number of different words)
SINGLE_WORD_MULT = 0.25         # 1 word
SHORT_MULT = 0.6                # 2-3 words
LONG_MULT = 1.2                 # 10+ words

# Someone else must be talking too, otherwise XP is reduced
COMPANY_WINDOW = 5 * 60         # "someone else wrote here in the last 5 minutes"
SOLO_MULTIPLIER = 0.3
REPLY_BONUS = 1.15              # replying to another person

# Mechanical-looking timing detector (scripts / macros)
MECH_MESSAGES = 8               # look at the last 8 messages
MECH_TOLERANCE = 1.5            # all gaps within 1.5 s of each other ...
MECH_MIN_GAP = 3.0              # ... and every gap at least 3 s = suspicious
SUSPICIOUS_SECONDS = 30 * 60    # reduced XP for this long
SUSPICIOUS_MULTIPLIER = 0.2

# Voice also gives a little XP (only with 2+ people who can hear). 0 = no voice XP.
VOICE_XP_PER_MINUTE = 5

# Text XP (and Mups) are given ONLY in these channels. A channel counts if its name
# contains one of these words (decorations like "┊✦〉" don't matter).
# Threads of these channels count too. An empty list means XP everywhere.
XP_CHANNELS = ["general", "meme-stuff"]

# ANTI-SPAM (Soft):
MIN_LETTERS = 2
MIN_UNIQUE_LETTERS = 2
SPAM_HISTORY = 5
SIMILARITY_LIMIT = 0.85

# CURRENCY (Fast):
MESSAGES_PER_MUPS = 25
MUPS_REWARD = 15
MUPS_ELIGIBLE_PER_HOUR = 60     # only the first 60 XP gains per hour count towards Mups
VOICE_SECONDS_PER_VUPS = 1800
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
intents.message_content = True


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
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

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
        for column in ("mups", "vups", "xp_messages", "cookies_received", "cookies_given",
                       "last_cookie_given", "tot_cooldown", "xp_buff_end"):
            if column not in columns:
                self.conn.execute(f"ALTER TABLE users ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")

        if "xp_buff_value" not in columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN xp_buff_value REAL NOT NULL DEFAULT 1.0")

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

    def get_setting(self, key: str, default: str) -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str):
        self.conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()

    def _ensure(self, guild_id: int, user_id: int):
        self.conn.execute(
            "INSERT OR IGNORE INTO users (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )

    def add_message(self, guild_id: int, user_id: int, pings: int, xp_gain: int, counted: bool = True,
                    booster_mult: float = 1.0, count_for_mups: bool = True):
        self._ensure(guild_id, user_id)
        old_xp, old_xp_messages = self.conn.execute(
            "SELECT xp, xp_messages FROM users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()

        new_xp_messages = old_xp_messages + (1 if (xp_gain > 0 and count_for_mups) else 0)

        cycles_old = old_xp_messages // MESSAGES_PER_MUPS
        cycles_new = new_xp_messages // MESSAGES_PER_MUPS

        global_curr = float(self.get_setting("global_curr_mult", "1.0"))
        mups_gain = int((cycles_new - cycles_old) * MUPS_REWARD * booster_mult * global_curr)

        self.conn.execute(
            "UPDATE users SET messages = messages + 1, pings = pings + ?, xp = xp + ?, "
            "xp_messages = ?, mups = mups + ? WHERE guild_id = ? AND user_id = ?",
            (pings, xp_gain, new_xp_messages, mups_gain, guild_id, user_id),
        )
        self.conn.commit()
        return old_xp, old_xp + xp_gain, mups_gain

    def add_xp(self, guild_id: int, user_id: int, amount: int):
        """Add XP without counting a message (voice XP). Returns (old_xp, new_xp)."""
        self._ensure(guild_id, user_id)
        old_xp = self.conn.execute(
            "SELECT xp FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        ).fetchone()[0]
        self.conn.execute(
            "UPDATE users SET xp = xp + ? WHERE guild_id = ? AND user_id = ?",
            (amount, guild_id, user_id),
        )
        self.conn.commit()
        return old_xp, old_xp + amount

    def add_voice(self, guild_id: int, user_id: int, seconds: int, booster_mult: float = 1.0):
        self._ensure(guild_id, user_id)
        old_seconds = self.conn.execute(
            "SELECT voice_seconds FROM users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()[0]

        new_seconds = old_seconds + seconds
        global_curr = float(self.get_setting("global_curr_mult", "1.0"))

        vups_gain = int((new_seconds // VOICE_SECONDS_PER_VUPS - old_seconds // VOICE_SECONDS_PER_VUPS) * VUPS_REWARD * booster_mult * global_curr)

        self.conn.execute(
            "UPDATE users SET voice_seconds = ?, vups = vups + ? "
            "WHERE guild_id = ? AND user_id = ?",
            (new_seconds, vups_gain, guild_id, user_id),
        )
        self.conn.commit()
        return vups_gain

    def get(self, guild_id: int, user_id: int):
        row = self.conn.execute(
            "SELECT xp, messages, pings, voice_seconds, mups, vups, cookies_received, cookies_given, last_cookie_given, tot_cooldown, xp_buff_end, xp_buff_value FROM users "
            "WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        if row is None:
            return {"xp": 0, "messages": 0, "pings": 0, "voice_seconds": 0, "mups": 0, "vups": 0,
                    "cookies_received": 0, "cookies_given": 0, "last_cookie_given": 0, "tot_cooldown": 0,
                    "xp_buff_end": 0, "xp_buff_value": 1.0}
        return {
            "xp": row[0], "messages": row[1], "pings": row[2], "voice_seconds": row[3],
            "mups": row[4], "vups": row[5], "cookies_received": row[6], "cookies_given": row[7],
            "last_cookie_given": row[8], "tot_cooldown": row[9], "xp_buff_end": row[10], "xp_buff_value": row[11],
        }

    def rank(self, guild_id: int, xp: int) -> int:
        return self.conn.execute("SELECT COUNT(*) + 1 FROM users WHERE guild_id = ? AND xp > ?", (guild_id, xp)).fetchone()[0]

    def top(self, guild_id: int, column: str, limit: int):
        if column not in ("xp", "messages", "voice_seconds"):
            raise ValueError("bad column")
        return self.conn.execute(
            f"SELECT user_id, xp, messages, voice_seconds FROM users WHERE guild_id = ? AND {column} > 0 ORDER BY {column} DESC LIMIT ?", (guild_id, limit)
        ).fetchall()

    def top_currency(self, guild_id: int, limit: int):
        return self.conn.execute("SELECT user_id, mups FROM users WHERE guild_id = ? AND mups > 0 ORDER BY mups DESC LIMIT ?", (guild_id, limit)).fetchall()

    def spend(self, guild_id: int, user_id: int, mups: int, vups: int) -> bool:
        self._ensure(guild_id, user_id)
        cursor = self.conn.execute(
            "UPDATE users SET mups = mups - ?, vups = vups - ? WHERE guild_id = ? AND user_id = ? AND mups >= ? AND vups >= ?",
            (mups, vups, guild_id, user_id, mups, vups),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    def refund(self, guild_id: int, user_id: int, mups: int, vups: int):
        self.conn.execute("UPDATE users SET mups = mups + ?, vups = vups + ? WHERE guild_id = ? AND user_id = ?", (mups, vups, guild_id, user_id))
        self.conn.commit()

    def add_currency(self, guild_id: int, user_id: int, mups: int, vups: int):
        self._ensure(guild_id, user_id)
        self.conn.execute("UPDATE users SET mups = mups + ?, vups = vups + ? WHERE guild_id = ? AND user_id = ?", (mups, vups, guild_id, user_id))
        self.conn.commit()

    def give_cookie(self, guild_id: int, giver_id: int, receiver_id: int, now: int):
        self._ensure(guild_id, giver_id)
        self._ensure(guild_id, receiver_id)
        self.conn.execute("UPDATE users SET cookies_given = cookies_given + 1, last_cookie_given = ? WHERE guild_id = ? AND user_id = ?", (now, guild_id, giver_id))
        self.conn.execute("UPDATE users SET cookies_received = cookies_received + 1 WHERE guild_id = ? AND user_id = ?", (guild_id, receiver_id))
        self.conn.commit()

    def set_tot_cooldown(self, guild_id: int, user_id: int, now: int):
        self._ensure(guild_id, user_id)
        self.conn.execute("UPDATE users SET tot_cooldown = ? WHERE guild_id = ? AND user_id = ?", (now, guild_id, user_id))
        self.conn.commit()

    def set_xp_buff(self, guild_id: int, user_id: int, end_time: int, value: float):
        self._ensure(guild_id, user_id)
        self.conn.execute("UPDATE users SET xp_buff_end = ?, xp_buff_value = ? WHERE guild_id = ? AND user_id = ?", (end_time, value, guild_id, user_id))
        self.conn.commit()

    def add_voice_channel(self, guild_id: int, channel_id: int, owner_id: int, now: int):
        self.conn.execute("INSERT OR REPLACE INTO voices (channel_id, guild_id, owner_id, last_active) VALUES (?, ?, ?, ?)", (channel_id, guild_id, owner_id, now))
        self.conn.commit()

    def get_voice_by_owner(self, guild_id: int, owner_id: int):
        row = self.conn.execute("SELECT channel_id FROM voices WHERE guild_id = ? AND owner_id = ?", (guild_id, owner_id)).fetchone()
        return row[0] if row else None

    def list_voices(self, guild_id: int):
        return self.conn.execute("SELECT channel_id, last_active FROM voices WHERE guild_id = ?", (guild_id,)).fetchall()

    def touch_voice(self, channel_id: int, now: int):
        self.conn.execute("UPDATE voices SET last_active = ? WHERE channel_id = ?", (now, channel_id))
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

def xp_for_level(target_level: int) -> int:
    total = 0
    for i in range(target_level):
        total += xp_needed(i)
    return total

def progress_bar(current: int, total: int, length: int = 14) -> str:
    filled = int(length * current / total) if total else 0
    return "█" * filled + "░" * (length - filled)

def progress_percent(current: int, total: int) -> int:
    """Progress to the next level, rounded down to steps of 5 (exact XP numbers are hidden)."""
    percent = int(100 * current / total) if total else 0
    return percent - percent % 5

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
            if word in tokens: return True
        elif word in lowered or word in compact: return True
    return False

# ======================================================================
#  Role helpers
# ======================================================================
def _norm_role(text: str) -> str:
    """Lowercase letters and digits only: '🍁 MAPLE SYROUP 🍁' -> 'maplesyroup'."""
    return re.sub(r"[\W_]+", "", text.lower())

def find_role(guild: discord.Guild, name: str) -> discord.Role | None:
    lowered = name.lower()
    for role in guild.roles:
        if role.name.lower() == lowered: return role
    for role in guild.roles:
        if role.name.lower().startswith(lowered): return role
    wanted = _norm_role(name)
    if wanted:
        for role in guild.roles:
            if _norm_role(role.name) == wanted: return role
    return None

def level_role_set(guild: discord.Guild) -> set:
    found = set()
    for _, role_name in LEVEL_ROLES:
        role = find_role(guild, role_name)
        if role: found.add(role)
    return found

def find_shop_role(guild: discord.Guild, name: str) -> discord.Role | None:
    """Like find_role, but never returns a level-reward role (so the shop can't sell them)."""
    excluded = level_role_set(guild)
    candidates = [r for r in guild.roles if r not in excluded]
    lowered = name.lower()
    for role in candidates:
        if role.name.lower() == lowered: return role
    for role in candidates:
        if role.name.lower().startswith(lowered): return role
    wanted = _norm_role(name)
    if wanted:
        for role in candidates:
            if _norm_role(role.name) == wanted: return role
    return None

async def get_role(guild: discord.Guild, name: str) -> discord.Role | None:
    role = find_role(guild, name)
    if role is None:
        try: role = await guild.create_role(name=name, reason="Created by Mupaw bot")
        except discord.Forbidden: return None
    return role

async def sync_level_roles(member: discord.Member, level: int) -> list[discord.Role]:
    granted = []
    for required_level, role_name in LEVEL_ROLES:
        if level < required_level: continue
        role = await get_role(member.guild, role_name)
        if role is None or role in member.roles: continue
        try:
            await member.add_roles(role, reason=f"Reached level {required_level}")
            granted.append(role)
        except discord.Forbidden: pass
    return granted

def has_level_role(member: discord.Member) -> bool:
    if member.guild_permissions.administrator: return True
    for _, role_name in LEVEL_ROLES:
        role = find_role(member.guild, role_name)
        if role and role in member.roles: return True
    return False

def find_channel_mention(guild: discord.Guild, keyword: str) -> str:
    channel = discord.utils.find(lambda c: keyword in c.name.lower(), guild.channels)
    return channel.mention if channel else f"#{keyword}"

# ======================================================================
#  Mod log (warnings for moderators)
# ======================================================================
def get_mod_log_channel(guild: discord.Guild):
    if MOD_LOG_CHANNEL_ID:
        channel = guild.get_channel(MOD_LOG_CHANNEL_ID)
        if channel is not None: return channel
    keywords = ("mod-log", "modlog", "moderator-only")
    return discord.utils.find(
        lambda c: isinstance(c, discord.TextChannel) and any(k in c.name.lower() for k in keywords),
        guild.channels,
    )

async def mod_log(guild: discord.Guild, title: str, description: str):
    channel = get_mod_log_channel(guild)
    if channel is None: return
    try: await channel.send(embed=discord.Embed(title=title, description=description, colour=PINK))
    except (discord.Forbidden, discord.HTTPException): pass

# ======================================================================
#  Shop Data (Packs) - ALL 50% OFF
# ======================================================================
HALLOWEEN_EXPIRY = 1793750400   # 2026-11-04 00:00 UTC = the end of November 3

SHOP_CATEGORIES = {
    "expie": {
        "label": "Expie Pack",
        "flag": "NEW",
        "items": [
            {"key": "expie_heart", "name": "Expie Heart", "emoji": "Expie_Heart", "role": "Expie Heart", "description": "idk jsut a hearatea weeweweweewe", "mups": 5, "vups": 0},
            {"key": "brain_damage", "name": "BRAIN DAMAGE", "emoji": "BRAIN_DAMAGE", "role": "BRAIN DAMAGE", "description": "Brain.exe has stopped working", "mups": 20, "vups": 0},
            {"key": "blehh_expie", "name": "Blehh Expie", "emoji": "De_plehxpie", "role": "Blehh Expie", "description": "Blehhh!", "mups": 50, "vups": 25},
        ],
    },
    "other": {
        "label": "Other",
        "flag": "",
        "items": [
            {"key": "cool_femboy", "name": "Cool femboy", "emoji": "Da_cool_femboy", "role": "Cool femboy", "description": "Da cool femboy!", "mups": 30, "vups": 0},
            {"key": "maplet", "name": "Maplet", "emoji": "Silly_Critter", "role": "Maplet", "description": "Little Maplefuw", "mups": 5, "vups": 0},
        ],
    },
    "halloween": {
        "label": "Halloween Pack",
        "flag": "LIMITED",
        "expiry": HALLOWEEN_EXPIRY,
        "items": [
            {"key": "halloween_pumpkin_cat", "name": "Halloween Pumpkin Cat", "emoji": "halloween_pupkin_cat", "role": "Halloween Pumpkin", "description": "Spooky month, silly critters!", "mups": 22, "vups": 2},
        ],
    },
    "silly": {
        "label": "Silly Pack",
        "flag": "",
        "items": [
            {"key": "blahaj_laying", "name": "Blahaj Laying", "emoji": "Blahaj_laying", "role": "Blahaj Laying", "description": "Classic?", "mups": 10, "vups": 0},
            {"key": "sus_cat", "name": "Sus cat", "emoji": "Cat_Sus", "role": "Sus cat", "description": "Looks suspicious.... Is probably fine...?", "mups": 150, "vups": 50},
            {"key": "true", "name": "TRUE...", "emoji": "De_true", "role": "TRUE...", "description": "who is he.?", "mups": 250, "vups": 0},
        ],
    },
    "boykisser": {
        "label": "Boykisser Pack",
        "flag": "",
        "items": [
            {"key": "shy_boykisser", "name": "Shy Boykisser", "emoji": "Shy_kisser", "role": "Shy Boykisser", "description": "For the shy ones ~w~", "mups": 20, "vups": 0},
            {"key": "blahaj_boykisser", "name": "Blahaj Boykisser", "emoji": "BoykisserBlahaj", "role": "Blahaj Boykisser", "description": "Hugg da sharkw!", "mups": 20, "vups": 0},
            {"key": "paws_boykisser", "name": "Paws Boykisser", "emoji": "Paws_Boykisser", "role": "Paws Boykisser", "description": "Show them your paws", "mups": 45, "vups": 2},
            {"key": "kiss_boykisser", "name": "Kiss boykisser", "emoji": "kissing_boykisser", "role": "Kiss boykisser", "description": "Kissing boykisser near you!", "mups": 60, "vups": 10},
            {"key": "ice_cream_boykisser", "name": "Ice Cream Boykisser", "emoji": "icecreambkisser", "role": "Ice Cream Boykisser", "description": "Kisser in da ice cream ^^", "mups": 50, "vups": 0},
        ],
    },
    "wyph": {
        "label": "WYPH PACK?",
        "flag": "",
        "items": [
            {"key": "wyphlet", "name": "Wyphlet", "emoji": "Wyphlet", "role": "Wyphlet", "description": "WAIT... A REAL WYP-", "mups": 500, "vups": 500},
        ],
    }
}

def get_active_shop_categories():
    active = {}
    now = time.time()
    for key, data in SHOP_CATEGORIES.items():
        if "expiry" in data and now > data["expiry"]: continue
        active[key] = data
    return active

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
        super().__init__(placeholder=placeholder, min_values=0, max_values=len(options) if multiple else 1, options=options, row=row)
        self.group = group

    async def callback(self, interaction: discord.Interaction):
        member = interaction.user
        chosen = set(self.values)
        if "None" in chosen: chosen = set()

        wanted = {role for label, role in self.group.items() if role and label in chosen}
        group_roles = {role for role in self.group.values() if role}
        held = set(member.roles)
        to_add = list(wanted - held)
        to_remove = list((group_roles - wanted) & held)

        try:
            if to_add: await member.add_roles(*to_add, reason="Self-assigned roles")
            if to_remove: await member.remove_roles(*to_remove, reason="Self-assigned roles")
        except discord.Forbidden:
            await interaction.response.send_message("I can't change those roles. Please tell a mod to move my role higher.", ephemeral=True)
            return

        for option in self.options:
            if option.value == "None": option.default = not wanted
            else: option.default = option.value in chosen
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
                if role: group[label] = role
            if not any(group.values()): continue
            self.add_item(RoleGroupSelect(member, group, placeholder, multiple, row))

class EmojiSelect(discord.ui.Select):
    def __init__(self, member: discord.Member, guild: discord.Guild):
        self.all_emoji_roles = []
        options = [discord.SelectOption(label="None", value="None")]

        all_items = []
        for cat_data in SHOP_CATEGORIES.values():
            all_items.extend(cat_data["items"])

        for item in all_items:
            role = find_shop_role(guild, item["role"])
            if role:
                self.all_emoji_roles.append(role)
                if role in member.roles:
                    options.append(discord.SelectOption(label=item["name"], value=str(role.id), default=True))

        if len(options) == 1:
            options = [discord.SelectOption(label="You don't own any name emojis", value="None")]
            super().__init__(placeholder="Edit your Name emoji!", options=options, disabled=True, row=2)
        else:
            has_any = any(opt.default for opt in options if opt.value != "None")
            options[0].default = not has_any
            super().__init__(placeholder="Edit your Name emoji!", min_values=1, max_values=1, options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        member = interaction.user
        val = self.values[0]
        wanted = interaction.guild.get_role(int(val)) if val != "None" else None
        held = set(member.roles)
        to_remove = [r for r in self.all_emoji_roles if r in held and r != wanted]
        to_add = [wanted] if wanted and wanted not in held else []

        try:
            if to_remove: await member.remove_roles(*to_remove, reason="Changed name emoji")
            if to_add: await member.add_roles(*to_add, reason="Changed name emoji")
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to change these roles.", ephemeral=True)
            return

        for option in self.options: option.default = (option.value == val)
        await interaction.response.edit_message(content="Saved! Name emoji updated.", view=self.view)

class NameChangeModal(discord.ui.Modal, title="Change your name (100 Mups)"):
    new_name = discord.ui.TextInput(label="New Name", placeholder="Enter your new nickname...", max_length=32, required=True)
    async def on_submit(self, interaction: discord.Interaction):
        member = interaction.user
        guild = interaction.guild
        data = db.get(guild.id, member.id)
        if data["mups"] < 100:
            await interaction.response.send_message("You don't have 100 Mups for this!", ephemeral=True)
            return
        try: await member.edit(nick=self.new_name.value.strip(), reason="Bought name change")
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
        color_group = {"None": None}
        for label, lookup in COLOR_ROLES:
            names = lookup if isinstance(lookup, (tuple, list)) else (lookup,)
            role = next((r for r in (find_role(guild, n) for n in names) if r), None)
            if role: color_group[label] = role
        if len(color_group) > 1:
            self.add_item(RoleGroupSelect(member, color_group, "Edit your Name color!", False, 0))

        pronoun_group = {"None": None}
        for label, lookup in PRONOUN_ROLES:
            names = lookup if isinstance(lookup, (tuple, list)) else (lookup,)
            role = next((r for r in (find_role(guild, n) for n in names) if r), None)
            if role: pronoun_group[label] = role
        if len(pronoun_group) > 1:
            self.add_item(RoleGroupSelect(member, pronoun_group, "Edit your pronouns!", True, 1))

        self.add_item(EmojiSelect(member, guild))
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
        embed = discord.Embed(title="Edit Server Profile", description="Change your roles, emojis, and name here!", colour=CREAM)
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

# ======================================================================
#  XP engine
# ======================================================================
next_xp_time: dict[tuple[int, int], float] = {}                  # earliest time of the next XP gain
recent_messages: dict[tuple[int, int], deque] = {}               # for the "similar message" filter
xp_timestamps: dict[tuple[int, int], deque] = {}                 # XP gains in the last hour (fatigue)
channel_activity: dict[int, deque] = {}                          # channel -> (time, author) of recent messages
message_times: dict[tuple[int, int], deque] = {}                 # last message times (mechanical detector)
suspicious_until: dict[tuple[int, int], float] = {}              # reduced XP until this time

URL_RE = re.compile(r"https?://\S+")
DISCORD_TAG_RE = re.compile(r"<a?:\w+:\d+>|<[@#][!&]?\d+>")
WORD_RE = re.compile(r"[^\W\d_]+")

# --- xp engine helpers (pure functions) ---
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

def unique_word_count(text: str) -> int:
    text = URL_RE.sub(" ", text)
    text = DISCORD_TAG_RE.sub(" ", text)
    return len(set(WORD_RE.findall(text.lower())))

def quality_multiplier(unique_words: int) -> float:
    """C: more different words = more XP. Repeating one word gives very little."""
    if unique_words <= 1: return SINGLE_WORD_MULT
    if unique_words <= 3: return SHORT_MULT
    if unique_words <= 9: return 1.0
    return LONG_MULT

def fatigue_multiplier(gains_last_hour: int) -> float:
    """B: smooth slowdown instead of a hard wall."""
    for limit, mult in FATIGUE_STEPS:
        if gains_last_hour < limit: return mult
    return FATIGUE_STEPS[-1][1]

def has_company(channel_id: int, author_id: int, now: float) -> bool:
    """D: did someone else write in this channel recently?"""
    recent = channel_activity.get(channel_id)
    if not recent: return False
    return any(author != author_id and now - when <= COMPANY_WINDOW for when, author in recent)

def looks_mechanical(key: tuple[int, int], now: float) -> bool:
    """E: messages arriving at suspiciously even intervals (scripts, macros)."""
    times = message_times.setdefault(key, deque(maxlen=MECH_MESSAGES))
    times.append(now)
    if len(times) < MECH_MESSAGES: return False
    ordered = list(times)
    gaps = [b - a for a, b in zip(ordered, ordered[1:])]
    return min(gaps) >= MECH_MIN_GAP and (max(gaps) - min(gaps)) <= MECH_TOLERANCE

def channel_gives_xp(channel) -> bool:
    """XP only in the channels listed in XP_CHANNELS (text channels and their threads)."""
    if not XP_CHANNELS: return True
    base = getattr(channel, "parent", None) or channel   # a thread counts as its parent channel
    if not isinstance(base, discord.TextChannel): return False
    name = base.name.lower()
    return any(word.lower() in name for word in XP_CHANNELS)
# --- end xp engine helpers ---


# ======================================================================
#  Background tasks
# ======================================================================
@tasks.loop(seconds=60)
async def voice_tracker():
    guild = client.get_guild(GUILD_ID)
    if guild is None: return
    global_xp = float(db.get_setting("global_xp_mult", "1.0"))
    voice_xp = int(VOICE_XP_PER_MINUTE * global_xp)

    for channel in guild.voice_channels + guild.stage_channels:
        if channel == guild.afk_channel: continue
        humans = [m for m in channel.members if not m.bot]
        # G: only count people who can actually hear, and need at least 2 of them
        listeners = [
            m for m in humans
            if m.voice is not None and m.voice.channel == channel and not (m.voice.deaf or m.voice.self_deaf)
        ]
        if len(listeners) < 2: continue

        for member in listeners:
            has_booster = any("CERTIFIED PAWBOOSTER" in r.name.upper() for r in member.roles)
            booster_mult = 1.5 if has_booster else 1.0
            db.add_voice(guild.id, member.id, int(60 * booster_mult))

            if voice_xp > 0:
                old_xp, new_xp = db.add_xp(guild.id, member.id, voice_xp)
                old_level = level_from_xp(old_xp)[0]
                new_level = level_from_xp(new_xp)[0]
                if new_level > old_level:
                    granted = await sync_level_roles(member, new_level)
                    text = f"{member.mention} just reached **level {new_level}**!"
                    if granted:
                        names = ", ".join(f"**{role.name}**" for role in granted)
                        text += f" New role unlocked: {names}"
                    try: await channel.send(text)
                    except (discord.Forbidden, discord.HTTPException, AttributeError): pass

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
        except discord.Forbidden: pass
    channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if channel is not None:
        await channel.send(content=member.mention, embed=build_welcome_embed(member))

@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None or message.guild.id != GUILD_ID: return
    member = message.author
    pings = len({m.id for m in message.mentions if not m.bot and m.id != member.id})
    key = (message.guild.id, member.id)
    now_f = time.time()
    now = int(now_f)

    # --- context about this message ---
    company = has_company(message.channel.id, member.id, now_f)

    replied_to_human = False
    reference = message.reference
    if reference is not None and isinstance(reference.resolved, discord.Message):
        original_author = reference.resolved.author
        if original_author.id != member.id and not original_author.bot:
            replied_to_human = True
            company = True   # answering someone counts as talking together

    # E: scripts and macros send messages at very even intervals
    if looks_mechanical(key, now_f) and now_f >= suspicious_until.get(key, 0):
        suspicious_until[key] = now_f + SUSPICIOUS_SECONDS
        await mod_log(
            message.guild,
            "Suspicious message timing",
            f"{member.mention} in {message.channel.mention} sends messages at very even intervals. "
            f"Their XP is reduced for {SUSPICIOUS_SECONDS // 60} minutes.",
        )

    channel_activity.setdefault(message.channel.id, deque(maxlen=30)).append((now_f, member.id))

    counted = is_real_message(key, message.content)

    # --- XP ---
    gain = 0
    count_for_mups = False
    if counted and channel_gives_xp(message.channel) and now_f >= next_xp_time.get(key, 0):
        history = xp_timestamps.setdefault(key, deque())
        while history and now - history[0] > 3600:
            history.popleft()
        gains_last_hour = len(history)

        data = db.get(message.guild.id, member.id)
        current_level = level_from_xp(data["xp"])[0]

        quality = quality_multiplier(unique_word_count(message.content))
        fatigue = fatigue_multiplier(gains_last_hour)
        company_mult = 1.0 if company else SOLO_MULTIPLIER
        suspicious_mult = SUSPICIOUS_MULTIPLIER if now_f < suspicious_until.get(key, 0) else 1.0
        reply_mult = REPLY_BONUS if replied_to_human else 1.0

        base_gain = random.randint(XP_MIN, XP_MAX)
        level_bonus = int(current_level * 0.2)

        # Global / TrickOrTreat Multipliers
        global_xp = float(db.get_setting("global_xp_mult", "1.0"))
        tot_xp_buff = 1.0
        if now < data.get("xp_buff_end", 0):
            tot_xp_buff = data.get("xp_buff_value", 1.0)

        gain = int((base_gain + level_bonus) * quality * fatigue * company_mult * suspicious_mult * reply_mult * global_xp * tot_xp_buff)
        gain = max(1, gain)

        # Mups only for real conversation, and only for the first gains of each hour
        count_for_mups = (
            quality >= SHORT_MULT and company and suspicious_mult == 1.0
            and gains_last_hour < MUPS_ELIGIBLE_PER_HOUR
        )

        next_xp_time[key] = now_f + random.uniform(*XP_COOLDOWN_RANGE)
        history.append(now)

    has_booster = any("CERTIFIED PAWBOOSTER" in r.name.upper() for r in member.roles)
    booster_mult = 1.5 if has_booster else 1.0

    old_xp, new_xp, _mups = db.add_message(message.guild.id, member.id, pings, gain, counted, booster_mult, count_for_mups)
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
            next_reward = f"{role_name.rstrip('.')} at level {required_level}"
            break

    colour = target_member.colour if target_member.colour.value else PINK
    embed = discord.Embed(
        title=f"{target_member.display_name}'s profile",
        description=f"**Level {level}**  |  Rank **#{rank}**\n`{progress_bar(into_level, needed)}`  {progress_percent(into_level, needed)}% to the next level",
        colour=colour,
    )
    embed.set_thumbnail(url=target_member.display_avatar.url)

    embed.add_field(name="Mups", value=f"{data['mups']:,}", inline=True)
    embed.add_field(name="Vups", value=f"{data['vups']:,}", inline=True)
    embed.add_field(name="Cookies", value=f"🍪 {data['cookies_received']:,}", inline=True)

    embed.add_field(name="Messages sent", value=f"{data['messages']:,}", inline=True)
    embed.add_field(name="Pings sent", value=f"{data['pings']:,}", inline=True)
    embed.add_field(name="Time in voice", value=format_duration(data["voice_seconds"]), inline=True)

    embed.add_field(name="Next reward", value=next_reward, inline=False)
    embed.set_footer(text="Keep chatting and be silly :3")

    if target_member.id == interaction.user.id:
        view = ProfileView(target_member)
        await interaction.response.send_message(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed)


@client.tree.command(name="progress", description="See your level progress and upcoming roles!")
@app_commands.guild_only()
async def progress(interaction: discord.Interaction):
    data = db.get(interaction.guild.id, interaction.user.id)
    current_level, into_level, needed = level_from_xp(data["xp"])

    lines = []
    next_found = False
    for req_level, role_name in LEVEL_ROLES:
        shown = role_name.rstrip(".")
        if current_level >= req_level:
            lines.append(f"✅ **Level {req_level}:** {shown} *(Unlocked!)*")
        elif not next_found:
            lines.append(f"🔄 **Level {req_level}:** {shown} *(Next!)*")
            next_found = True
        else:
            lines.append(f"🔒 **Level {req_level}:** {shown}")

    embed = discord.Embed(
        title="📈 Your Level Progress",
        description=f"**Current Level: {current_level}**\n`{progress_bar(into_level, needed)}` {progress_percent(into_level, needed)}% to the next level\n\n**Role Unlocks:**\n" + "\n".join(lines),
        colour=PINK
    )
    await interaction.response.send_message(embed=embed)


def build_leaderboard_embed(guild: discord.Guild, kind: str) -> discord.Embed:
    titles = {"xp": "Top levels", "messages": "Top chatters", "voice_seconds": "Top voice time"}
    rows = db.top(guild.id, kind, 50)
    lines = []
    for user_id, xp, messages, voice_seconds in rows:
        member = guild.get_member(user_id)
        if member is None: continue
        if kind == "xp": value = f"Level {level_from_xp(xp)[0]}"
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
#  Cookies! 🍪
# ======================================================================
@client.tree.command(name="award_cookie", description="Give one cookie to someone every 24 hours :3")
@app_commands.describe(user="Who do you want to give a cookie to?")
@app_commands.guild_only()
async def award_cookie(interaction: discord.Interaction, user: discord.Member):
    giver = interaction.user

    if user.id == giver.id:
        await interaction.response.send_message("You can't give a cookie to yourself! Share with others :3", ephemeral=True)
        return
    if user.bot:
        await interaction.response.send_message("Bots don't eat cookies, they eat RAM! Pick a real person.", ephemeral=True)
        return

    guild_id = interaction.guild.id
    data = db.get(guild_id, giver.id)

    has_booster = any("CERTIFIED PAWBOOSTER" in r.name.upper() for r in giver.roles)
    base_cooldown = 24 * 60 * 60
    cooldown = int(base_cooldown * 0.8) if has_booster else base_cooldown

    now = int(time.time())
    last_given = data.get("last_cookie_given", 0)

    if now - last_given < cooldown:
        remaining = cooldown - (now - last_given)
        hours, remainder = divmod(remaining, 3600)
        minutes, _ = divmod(remainder, 60)
        await interaction.response.send_message(
            f"You already gave a cookie! You can give another one in {hours}h {minutes}m.",
            ephemeral=True
        )
        return

    db.give_cookie(guild_id, giver.id, user.id, now)
    await interaction.response.send_message(f"🍪 {giver.mention} gave a delicious cookie to {user.mention}! So sweet :3")


@client.tree.command(name="my_cookies", description="Check how many cookies you have and gave")
@app_commands.guild_only()
async def my_cookies(interaction: discord.Interaction):
    data = db.get(interaction.guild.id, interaction.user.id)
    received = data.get("cookies_received", 0)
    given = data.get("cookies_given", 0)

    embed = discord.Embed(
        title=f"🍪 {interaction.user.display_name}'s Cookie Jar",
        description=f"**Cookies received:** {received}\n**Cookies given:** {given}",
        colour=CREAM
    )
    await interaction.response.send_message(embed=embed)


# ======================================================================
#  Trick or Treat 🎃
# ======================================================================
@client.tree.command(name="trick_or_treat", description="Trick or Treat! Random events, good or bad!")
@app_commands.guild_only()
async def trick_or_treat(interaction: discord.Interaction):
    user_id = interaction.user.id
    guild_id = interaction.guild.id

    data = db.get(guild_id, user_id)
    now = int(time.time())
    last_tot = data.get("tot_cooldown", 0)

    if now - last_tot < 600:
        rem = 600 - (now - last_tot)
        m, s = divmod(rem, 60)
        await interaction.response.send_message(f"You can play again in {m}m {s}s!", ephemeral=True)
        return

    db.set_tot_cooldown(guild_id, user_id, now)

    is_treat = random.choice([True, False])
    outcome = random.randint(1, 7)

    title_text = "Treat" if is_treat else "Trick"
    msg = ""

    if is_treat:
        if outcome == 1:
            currency = random.choice(["Mups", "Vups"])
            amount = 50
            if currency == "Mups": db.add_currency(guild_id, user_id, amount, 0)
            else: db.add_currency(guild_id, user_id, 0, amount)
            msg = f"Jackpot!!! You got {amount} {currency}"

        elif outcome == 2:
            amount = random.randint(1, 3)
            db.conn.execute("UPDATE users SET cookies_received = cookies_received + ? WHERE guild_id = ? AND user_id = ?", (amount, guild_id, user_id))
            db.conn.commit()
            msg = f"Yummy! you got {amount} cookies!🍪"

        elif outcome == 3:
            db.set_xp_buff(guild_id, user_id, now + 86400, 3.0)
            msg = "WOAAHHWW!!! You get a HUGE x3 XP Boost for the next 24 hours! Great luck ^^"

        elif outcome == 4:
            current_lvl, _, _ = level_from_xp(data["xp"])
            next_lvl_xp = xp_for_level(current_lvl + 1)
            db.conn.execute("UPDATE users SET xp = ? WHERE guild_id = ? AND user_id = ?", (next_lvl_xp, guild_id, user_id))
            db.conn.commit()
            msg = "You got +1 lvl! Congradulations, gambler!"
            await sync_level_roles(interaction.user, current_lvl + 1)

        elif outcome == 5:
            all_shop_roles = [item["role"] for cat in SHOP_CATEGORIES.values() for item in cat["items"]]
            unowned = []
            for r_name in all_shop_roles:
                r = find_shop_role(interaction.guild, r_name)
                if r and r not in interaction.user.roles:
                    unowned.append((r, r_name))
            if unowned:
                role_obj, r_name = random.choice(unowned)
                try:
                    await interaction.user.add_roles(role_obj, reason="Trick or Treat")
                    msg = f"Cat gave you the {r_name} item.. It's yours now!"
                except (discord.Forbidden, discord.HTTPException):
                    msg = "A cat tried to bring smt, but I can't give you roles right now..."
            else:
                msg = "A cat tried to bring smt, but you own all of them..."

        elif outcome == 6:
            role = await get_role(interaction.guild, "Pumpkin Pie")
            if role:
                try:
                    await interaction.user.add_roles(role, reason="Trick or Treat")
                    msg = "YESS.. I HIT THE LIMITED THING! You unlocked the exclusive Pumpkin Pie name color"
                except (discord.Forbidden, discord.HTTPException):
                    msg = "YESS.. but I don't have permissions to give you the role!"
            else:
                msg = "YESS.. but I couldn't find the Pumpkin Pie role!"

        elif outcome == 7:
            db.set_xp_buff(guild_id, user_id, now + 86400, 1.4)
            msg = "HEAVENLY LUCK FOR A BUFF! You will earn +40% MORE XP for the next 24 hours! "

    else:  # TRICK
        if outcome == 1:
            currency = random.choice(["Mups", "Vups"])
            if currency == "Mups":
                amount = random.randint(10, 50)
                db.conn.execute("UPDATE users SET mups = MAX(0, mups - ?) WHERE guild_id = ? AND user_id = ?", (amount, guild_id, user_id))
            else:
                amount = random.randint(5, 15)
                db.conn.execute("UPDATE users SET vups = MAX(0, vups - ?) WHERE guild_id = ? AND user_id = ?", (amount, guild_id, user_id))
            db.conn.commit()
            msg = f"GUHH! A mischievous cat stole {amount} {currency} from you ~w~!"

        elif outcome == 2:
            current_lvl, _, _ = level_from_xp(data["xp"])
            new_lvl = max(0, current_lvl - 1)
            new_xp = xp_for_level(new_lvl)
            db.conn.execute("UPDATE users SET xp = ? WHERE guild_id = ? AND user_id = ?", (new_xp, guild_id, user_id))
            db.conn.commit()
            msg = "You lost 1 lvl! not cool.. not cool"

        elif outcome == 3:
            try:
                await interaction.user.timeout(discord.utils.utcnow() + datetime.timedelta(minutes=5), reason="Trick or Treat Trick")
                msg = "You were SILENCED! Time outed for 5 mins"
            except (discord.Forbidden, discord.HTTPException):
                msg = "You were SILENCED! (Wait, I don't have permissions to timeout you...)"

        elif outcome == 4:
            db.conn.execute("UPDATE users SET xp = MAX(0, xp - 200) WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            db.conn.commit()
            msg = "You lost 200 xp, bad luck!"

        elif outcome == 5:
            current_lvl, _, _ = level_from_xp(data["xp"])
            new_lvl = max(0, current_lvl - 2)
            new_xp = xp_for_level(new_lvl)
            db.conn.execute("UPDATE users SET xp = ? WHERE guild_id = ? AND user_id = ?", (new_xp, guild_id, user_id))
            db.conn.commit()
            msg = "You lost 2 lvls! This really not cool!!"

        elif outcome == 6:
            all_shop_roles = [item["role"] for cat in SHOP_CATEGORIES.values() for item in cat["items"]]
            owned = []
            for r_name in all_shop_roles:
                r = find_shop_role(interaction.guild, r_name)
                if r and r in interaction.user.roles:
                    owned.append((r, r_name))
            if owned:
                role_obj, r_name = random.choice(owned)
                try:
                    await interaction.user.remove_roles(role_obj, reason="Trick or Treat Trick")
                    msg = f"Someone stole {r_name} from your inventory!"
                except (discord.Forbidden, discord.HTTPException):
                    msg = "Guhh.. I wanted ur items but you got nothing.. ~w~"
            else:
                msg = "Guhh.. I wanted ur items but you got nothing.. ~w~"

        elif outcome == 7:
            db.set_xp_buff(guild_id, user_id, now + 86400, 0.95)
            msg = "You ate a cursed candy! You will earn 5% less XP for the next 24 hours..."

    await interaction.response.send_message(f"{title_text}\n\n{msg}")


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
        active_categories = get_active_shop_categories()
        options = []
        for key, category in active_categories.items():
            flag_text = f" ({category['flag']})" if category.get("flag") else ""
            item_count = len(category["items"])
            item_text = f"{item_count} item{'s' if item_count != 1 else ''}"

            options.append(discord.SelectOption(
                label=category["label"],
                value=key,
                description=f"{flag_text} {item_text}".strip(),
                default=(key == shop.category)
            ))

        super().__init__(placeholder="Choose a pack!", options=options, row=0)
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
        role = find_shop_role(guild, item["role"])
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
        await interaction.response.send_message("This is not your shop! Type /shop to open your own.", ephemeral=True)
        return False

    def items(self) -> list[dict]:
        if self.category is None: return []
        active_categories = get_active_shop_categories()
        if self.category not in active_categories: return []
        return active_categories[self.category]["items"]

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
            desc = "Welcome in! Pick a pack below to see what's on the shelves.\n\n✨ **NEW:** Expie Pack, just arrived!\n🎃 **LIMITED:** Halloween Pack, available until Nov 3!"
            embed = discord.Embed(title="Silly Shop", description=desc, colour=CREAM)
            embed.set_footer(text="Boosters get 1.5x Mups and Vups!")
        else:
            active_categories = get_active_shop_categories()
            if self.category in active_categories:
                category = active_categories[self.category]
                if category["items"]: body = "\n\n".join(shop_item_text(guild, item) for item in category["items"])
                else: body = SHOP_EMPTY_TEXT
                text = f"{self.notice}\n\n{body}" if self.notice else body

                flag_text = f" [{category['flag']}]" if category.get("flag") else ""
                embed = discord.Embed(title=f"{category['label']}{flag_text}", description=text, colour=CREAM)
            else:
                embed = discord.Embed(title="Error", description="This pack is no longer available.", colour=CREAM)

        embed.add_field(name="Your Mups", value=f"{data['mups']:,}", inline=True)
        embed.add_field(name="Your Vups", value=f"{data['vups']:,}", inline=True)
        return embed

@client.tree.command(name="shop", description="Visit the shop")
@app_commands.guild_only()
async def shop(interaction: discord.Interaction):
    view = ShopView(interaction.user)
    await interaction.response.send_message(embed=view.make_embed(), view=view)


# ======================================================================
#  Admin commands (Setup & Give Currency & Global Multipliers)
# ======================================================================
@client.tree.command(name="event_2x_xp", description="Toggle global 2x XP (Admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def event_2x_xp(interaction: discord.Interaction):
    current = float(db.get_setting("global_xp_mult", "1.0"))
    if current == 1.0:
        db.set_setting("global_xp_mult", "2.0")
        await interaction.response.send_message("🚀 **GLOBAL 2x XP IS NOW ACTIVE!**", ephemeral=False)
    else:
        db.set_setting("global_xp_mult", "1.0")
        await interaction.response.send_message("🛑 **Global 2x XP has ended!**", ephemeral=False)

@client.tree.command(name="event_2x_currency", description="Toggle global 2x Currency (Admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def event_2x_currency(interaction: discord.Interaction):
    current = float(db.get_setting("global_curr_mult", "1.0"))
    if current == 1.0:
        db.set_setting("global_curr_mult", "2.0")
        await interaction.response.send_message("💰 **GLOBAL 2x CURRENCY IS NOW ACTIVE!**", ephemeral=False)
    else:
        db.set_setting("global_curr_mult", "1.0")
        await interaction.response.send_message("🛑 **Global 2x Currency has ended!**", ephemeral=False)

@client.tree.command(name="give", description="Give Mups or Vups to a user (Admins/Owner only)")
@app_commands.describe(user="The user to give currency to", amount="Amount to give", currency="Which currency?")
@app_commands.choices(currency=[
    app_commands.Choice(name="Mups", value="mups"),
    app_commands.Choice(name="Vups", value="vups"),
])
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def give_currency(interaction: discord.Interaction, user: discord.Member, amount: int, currency: app_commands.Choice[str]):
    if interaction.user.id != interaction.guild.owner_id and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Only the server creator or admins can do this.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("Amount must be a positive number.", ephemeral=True)
        return
    if currency.value == "mups": db.add_currency(interaction.guild.id, user.id, amount, 0)
    else: db.add_currency(interaction.guild.id, user.id, 0, amount)
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
