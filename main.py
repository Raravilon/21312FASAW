import os
import random
import re
import sqlite3
import time
import datetime
import asyncio
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

MOD_LOG_CHANNEL_ID = int(os.getenv("MOD_LOG_CHANNEL_ID", "0"))
# ID канала, где сама спавнится коробка каждые 10 минут!
GENERAL_CHANNEL_ID = int(os.getenv("GENERAL_CHANNEL_ID", "0"))

JOINED_ROLE_NAME = os.getenv("JOINED_ROLE_NAME", "Just joined!")
NEWCOMER_ROLE_NAME = os.getenv("NEWCOMER_ROLE_NAME", "Newcomer :3")
MUTED_ROLE_NAME = os.getenv("MUTED_ROLE_NAME", "Muted")

NIGHT_UTC_OFFSET = int(os.getenv("NIGHT_UTC_OFFSET", "0"))

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
XP_MIN = 20
XP_MAX = 35
XP_COOLDOWN_RANGE = (5, 8)    

FATIGUE_STEPS = [(100, 1.0), (200, 0.7), (500, 0.4), (10**9, 0.15)]
MAX_XP_PER_HOUR = 500

SINGLE_WORD_MULT = 1.0          
SHORT_MULT = 1.0                
LONG_MULT = 1.2                 

COMPANY_WINDOW = 15 * 60        
SOLO_MULTIPLIER = 1.0
REPLY_BONUS = 1.15              

MECH_MESSAGES = 8               
MECH_TOLERANCE = 1.5            
MECH_MIN_GAP = 3.0              
SUSPICIOUS_SECONDS = 30 * 60    
SUSPICIOUS_MULTIPLIER = 0.2

VOICE_XP_PER_MINUTE = 5
XP_CHANNELS = []
XP_BOOSTS_STACK = False

MIN_LETTERS = 2
MIN_UNIQUE_LETTERS = 2
SPAM_HISTORY = 5
SIMILARITY_LIMIT = 0.85

MESSAGES_PER_MUPS = 25
MUPS_REWARD = 15
MUPS_ELIGIBLE_PER_HOUR = 500     
VOICE_SECONDS_PER_VUPS = 1800
VUPS_REWARD = 1

VOICE_MAX_LIMIT = 10
VOICE_IDLE_SECONDS = 6 * 60 * 60
BLOCKED_WORDS: list[str] = ["Nigger", "Niger", "Fuck", "sex", "NSFW", "18", "roleplay"]

PRONOUN_ROLES = [
    ("He/Him", "He/Him"), ("She/Her", "She/Her"), ("They/Them", "They/Them"),
    ("It/Its", "It/I"), ("Cat/cats", "Cat/cats"), ("Ask for pronouns", "Ask for pronouns"),
    ("Neopronouns", "Neopronouns"),
]
COLOR_ROLES = [
    ("Purple", "Purple"), ("White", "White"), ("Red", "Red"), ("Blue", "Blue"),
    ("Light Green", "Light Green"), ("Beige", ("Beige", "Biege")), ("Green", "Green"),
    ("Pink", "Pink"), ("Orange", "Orange"), ("Yellow", "Yellow"),
    ("Dark Purple", "Dark Purple"), ("Black", "Black"),
]
PING_ROLES = [("Gamenights", "Gamenight"), ("Chat.. revive!", "Chat.. REVIVE")]

DB_PATH = os.getenv("DB_PATH", "data/bot.db")

PINK = discord.Colour.from_str("#f4b6c2")
CREAM = discord.Colour.from_str("#f7e7d3")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

DAILY_COOLDOWN = 24 * 3600
DAILY_COOLDOWN_TRUFFLE = 19 * 3600     
DAILY_WINDOW = 26 * 3600               
DAILY_WINDOW_COCOA = 30 * 3600         
DAILY_BASE_MUPS = 0.5
DAILY_BASE_VUPS = 0.1
DAILY_MUPS_STEP = 0.01                 
DAILY_VUPS_STEP = 0.01                 
DAILY_BASE_XP = 1                      
DAILY_XP_STEP = 0.5                    
WEEK_SECONDS = 7 * 24 * 3600

# ======================================================================
#  Database
# ======================================================================
class Database:
    def __init__(self, path: str):
        folder = os.path.dirname(path)
        if folder: os.makedirs(folder, exist_ok=True)
        self.conn = sqlite3.connect(path)

        self.conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, xp INTEGER NOT NULL DEFAULT 0,
                messages INTEGER NOT NULL DEFAULT 0, pings INTEGER NOT NULL DEFAULT 0,
                voice_seconds INTEGER NOT NULL DEFAULT 0, mups INTEGER NOT NULL DEFAULT 0,
                vups INTEGER NOT NULL DEFAULT 0, xp_messages INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(users)")}
        for column in ("mups", "vups", "xp_messages", "cookies_received", "cookies_given",
                       "last_cookie_given", "tot_cooldown", "xp_buff_end", "karma", "tot_starter"):
            if column not in columns:
                self.conn.execute(f"ALTER TABLE users ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")

        if "xp_buff_value" not in columns: self.conn.execute("ALTER TABLE users ADD COLUMN xp_buff_value REAL NOT NULL DEFAULT 1.0")
        if "ghost_shields" not in columns: self.conn.execute("ALTER TABLE users ADD COLUMN ghost_shields INTEGER NOT NULL DEFAULT 0")
        if "xp_frac" not in columns: self.conn.execute("ALTER TABLE users ADD COLUMN xp_frac REAL NOT NULL DEFAULT 0.0")

        self.conn.execute("CREATE TABLE IF NOT EXISTS voices (channel_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, owner_id INTEGER NOT NULL, last_active INTEGER NOT NULL)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS treats (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, treat TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (guild_id, user_id, treat))")
        self.conn.execute("CREATE TABLE IF NOT EXISTS user_flags (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, key TEXT NOT NULL, value REAL NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, user_id, key))")
        self.conn.execute("CREATE TABLE IF NOT EXISTS mutes (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, until INTEGER NOT NULL, role_ids TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', moderator_id INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, user_id))")
        self.conn.execute("CREATE TABLE IF NOT EXISTS treat_pot (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, amount INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, user_id))")
        self.conn.commit()

    def get_setting(self, key: str, default: str) -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str):
        self.conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()

    def _ensure(self, guild_id: int, user_id: int):
        self.conn.execute("INSERT OR IGNORE INTO users (guild_id, user_id) VALUES (?, ?)", (guild_id, user_id))

    def get_flag(self, guild_id: int, user_id: int, key: str, default: float = 0.0) -> float:
        row = self.conn.execute("SELECT value FROM user_flags WHERE guild_id = ? AND user_id = ? AND key = ?", (guild_id, user_id, key)).fetchone()
        return row[0] if row else default

    def set_flag(self, guild_id: int, user_id: int, key: str, value: float):
        self.conn.execute("INSERT OR REPLACE INTO user_flags (guild_id, user_id, key, value) VALUES (?, ?, ?, ?)", (guild_id, user_id, key, value))
        self.conn.commit()

    def add_message(self, guild_id: int, user_id: int, pings: int, xp_gain: int, counted: bool = True, booster_mult: float = 1.0, count_for_mups: bool = True):
        self._ensure(guild_id, user_id)
        old_xp, old_xp_messages = self.conn.execute("SELECT xp, xp_messages FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
        new_xp_messages = old_xp_messages + (1 if (xp_gain > 0 and count_for_mups) else 0)
        cycles_old = old_xp_messages // MESSAGES_PER_MUPS
        cycles_new = new_xp_messages // MESSAGES_PER_MUPS
        global_curr = float(self.get_setting("global_curr_mult", "1.0"))
        mups_gain = round((cycles_new - cycles_old) * MUPS_REWARD * booster_mult * global_curr, 2)
        self.conn.execute("UPDATE users SET messages = messages + 1, pings = pings + ?, xp = xp + ?, xp_messages = ?, mups = ROUND(mups + ?, 2) WHERE guild_id = ? AND user_id = ?",
                          (pings, xp_gain, new_xp_messages, mups_gain, guild_id, user_id))
        self.conn.commit()
        return old_xp, old_xp + xp_gain, mups_gain

    def add_xp(self, guild_id: int, user_id: int, amount: int):
        self._ensure(guild_id, user_id)
        old_xp = self.conn.execute("SELECT xp FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()[0]
        new_xp = max(0, old_xp + amount)
        self.conn.execute("UPDATE users SET xp = ? WHERE guild_id = ? AND user_id = ?", (new_xp, guild_id, user_id))
        self.conn.commit()
        return old_xp, new_xp

    def add_voice(self, guild_id: int, user_id: int, seconds: int, booster_mult: float = 1.0, treat_mult: float = 1.0):
        self._ensure(guild_id, user_id)
        old_seconds = self.conn.execute("SELECT voice_seconds FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()[0]
        new_seconds = old_seconds + seconds
        global_curr = float(self.get_setting("global_curr_mult", "1.0"))
        vups_gain = round((new_seconds // VOICE_SECONDS_PER_VUPS - old_seconds // VOICE_SECONDS_PER_VUPS) * VUPS_REWARD * booster_mult * global_curr * treat_mult, 2)
        self.conn.execute("UPDATE users SET voice_seconds = ?, vups = ROUND(vups + ?, 2) WHERE guild_id = ? AND user_id = ?", (new_seconds, vups_gain, guild_id, user_id))
        self.conn.commit()
        return vups_gain

    def get(self, guild_id: int, user_id: int):
        row = self.conn.execute("SELECT xp, messages, pings, voice_seconds, mups, vups, cookies_received, cookies_given, last_cookie_given, tot_cooldown, xp_buff_end, xp_buff_value, ghost_shields FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
        if row is None:
            return {"xp": 0, "messages": 0, "pings": 0, "voice_seconds": 0, "mups": 0, "vups": 0, "cookies_received": 0, "cookies_given": 0, "last_cookie_given": 0, "tot_cooldown": 0, "xp_buff_end": 0, "xp_buff_value": 1.0, "ghost_shields": 0}
        return {"xp": row[0], "messages": row[1], "pings": row[2], "voice_seconds": row[3], "mups": row[4], "vups": row[5], "cookies_received": row[6], "cookies_given": row[7], "last_cookie_given": row[8], "tot_cooldown": row[9], "xp_buff_end": row[10], "xp_buff_value": row[11], "ghost_shields": row[12]}

    def rank(self, guild_id: int, xp: int) -> int: return self.conn.execute("SELECT COUNT(*) + 1 FROM users WHERE guild_id = ? AND xp > ?", (guild_id, xp)).fetchone()[0]
    def top(self, guild_id: int, column: str, limit: int): return self.conn.execute(f"SELECT user_id, xp, messages, voice_seconds FROM users WHERE guild_id = ? AND {column} > 0 ORDER BY {column} DESC LIMIT ?", (guild_id, limit)).fetchall()
    def top_currency(self, guild_id: int, limit: int): return self.conn.execute("SELECT user_id, mups FROM users WHERE guild_id = ? AND mups > 0 ORDER BY mups DESC LIMIT ?", (guild_id, limit)).fetchall()
    def top_cookies(self, guild_id: int, limit: int): return self.conn.execute("SELECT user_id, cookies_received FROM users WHERE guild_id = ? AND cookies_received > 0 ORDER BY cookies_received DESC LIMIT ?", (guild_id, limit)).fetchall()

    def spend(self, guild_id: int, user_id: int, mups: float, vups: float) -> bool:
        self._ensure(guild_id, user_id)
        cursor = self.conn.execute("UPDATE users SET mups = ROUND(mups - ?, 2), vups = ROUND(vups - ?, 2) WHERE guild_id = ? AND user_id = ? AND mups >= ? AND vups >= ?", (mups, vups, guild_id, user_id, mups, vups))
        self.conn.commit()
        return cursor.rowcount == 1
    def add_currency(self, guild_id: int, user_id: int, mups: float, vups: float):
        self._ensure(guild_id, user_id)
        self.conn.execute("UPDATE users SET mups = ROUND(mups + ?, 2), vups = ROUND(vups + ?, 2) WHERE guild_id = ? AND user_id = ?", (mups, vups, guild_id, user_id))
        self.conn.commit()
    def take_currency(self, guild_id: int, user_id: int, mups: float, vups: float):
        self._ensure(guild_id, user_id)
        have_m, have_v = self.conn.execute("SELECT mups, vups FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
        take_m = round(max(0, min(mups, have_m)), 2)
        take_v = round(max(0, min(vups, have_v)), 2)
        self.conn.execute("UPDATE users SET mups = ROUND(mups - ?, 2), vups = ROUND(vups - ?, 2) WHERE guild_id = ? AND user_id = ?", (take_m, take_v, guild_id, user_id))
        self.conn.commit()
        return take_m, take_v

    def get_random_user(self, guild_id: int) -> int:
        row = self.conn.execute("SELECT user_id FROM users WHERE guild_id = ? ORDER BY RANDOM() LIMIT 1", (guild_id,)).fetchone()
        return row[0] if row else 0

    def give_cookie(self, guild_id: int, giver_id: int, receiver_id: int, now: int, amount: int = 1):
        self._ensure(guild_id, giver_id); self._ensure(guild_id, receiver_id)
        self.conn.execute("UPDATE users SET cookies_given = cookies_given + ?, last_cookie_given = ? WHERE guild_id = ? AND user_id = ?", (amount, now, guild_id, giver_id))
        self.conn.execute("UPDATE users SET cookies_received = cookies_received + ? WHERE guild_id = ? AND user_id = ?", (amount, guild_id, receiver_id))
        self.conn.commit()
    def set_tot_cooldown(self, guild_id: int, user_id: int, now: int):
        self._ensure(guild_id, user_id)
        self.conn.execute("UPDATE users SET tot_cooldown = ? WHERE guild_id = ? AND user_id = ?", (now, guild_id, user_id))
        self.conn.commit()
    def set_xp_buff(self, guild_id: int, user_id: int, end_time: int, value: float):
        self._ensure(guild_id, user_id)
        self.conn.execute("UPDATE users SET xp_buff_end = ?, xp_buff_value = ? WHERE guild_id = ? AND user_id = ?", (end_time, value, guild_id, user_id))
        self.conn.commit()
    def set_ghost_shield(self, guild_id: int, user_id: int, amount: int):
        self._ensure(guild_id, user_id)
        self.conn.execute("UPDATE users SET ghost_shields = ghost_shields + ? WHERE guild_id = ? AND user_id = ?", (amount, guild_id, user_id))
        self.conn.commit()
    def use_ghost_shield(self, guild_id: int, user_id: int) -> bool:
        shields = self.conn.execute("SELECT ghost_shields FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()[0]
        if shields > 0:
            self.conn.execute("UPDATE users SET ghost_shields = ghost_shields - 1 WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            self.conn.commit()
            return True
        return False

    def add_voice_channel(self, guild_id: int, channel_id: int, owner_id: int, now: int): self.conn.execute("INSERT OR REPLACE INTO voices (channel_id, guild_id, owner_id, last_active) VALUES (?, ?, ?, ?)", (channel_id, guild_id, owner_id, now)); self.conn.commit()
    def get_voice_by_owner(self, guild_id: int, owner_id: int): row = self.conn.execute("SELECT channel_id FROM voices WHERE guild_id = ? AND owner_id = ?", (guild_id, owner_id)).fetchone(); return row[0] if row else None
    def list_voices(self, guild_id: int): return self.conn.execute("SELECT channel_id, last_active FROM voices WHERE guild_id = ?", (guild_id,)).fetchall()
    def touch_voice(self, channel_id: int, now: int): self.conn.execute("UPDATE voices SET last_active = ? WHERE channel_id = ?", (now, channel_id)); self.conn.commit()
    def remove_voice(self, channel_id: int): self.conn.execute("DELETE FROM voices WHERE channel_id = ?", (channel_id,)); self.conn.commit()

    # Treats
    def get_treats(self, guild_id: int, user_id: int) -> dict:
        rows = self.conn.execute("SELECT treat, count FROM treats WHERE guild_id = ? AND user_id = ? AND count > 0", (guild_id, user_id)).fetchall()
        return {row[0]: row[1] for row in rows}
    def add_treat(self, guild_id: int, user_id: int, treat: str, amount: int = 1) -> bool:
        row = self.conn.execute("SELECT count FROM treats WHERE guild_id = ? AND user_id = ? AND treat = ?", (guild_id, user_id, treat)).fetchone()
        if row is None:
            self.conn.execute("INSERT INTO treats (guild_id, user_id, treat, count) VALUES (?, ?, ?, ?)", (guild_id, user_id, treat, amount))
            self.conn.commit()
            return True
        self.conn.execute("UPDATE treats SET count = count + ? WHERE guild_id = ? AND user_id = ? AND treat = ?", (amount, guild_id, user_id, treat))
        self.conn.commit()
        return row[0] <= 0
    def remove_treat(self, guild_id: int, user_id: int, treat: str, amount: int = 1):
        self.conn.execute("UPDATE treats SET count = count - ? WHERE guild_id = ? AND user_id = ? AND treat = ?", (amount, guild_id, user_id, treat))
        self.conn.execute("DELETE FROM treats WHERE guild_id = ? AND user_id = ? AND treat = ? AND count <= 0", (guild_id, user_id, treat))
        self.conn.commit()
    def treat_totals(self, guild_id: int): return self.conn.execute("SELECT user_id, COUNT(*) FROM treats WHERE guild_id = ? AND count > 0 GROUP BY user_id", (guild_id,)).fetchall()
    def treat_owners(self, guild_id: int, treat: str) -> list[int]: return [row[0] for row in self.conn.execute("SELECT user_id FROM treats WHERE guild_id = ? AND treat = ? AND count > 0", (guild_id, treat)).fetchall()]
    def treat_holders(self, guild_id: int, treat: str): return self.conn.execute("SELECT user_id, count FROM treats WHERE guild_id = ? AND treat = ? AND count > 0 ORDER BY count DESC", (guild_id, treat)).fetchall()
    def treat_stats(self, guild_id: int, limit: int): return self.conn.execute("SELECT user_id, COUNT(*), SUM(count) FROM treats WHERE guild_id = ? AND count > 0 GROUP BY user_id ORDER BY COUNT(*) DESC, SUM(count) DESC LIMIT ?", (guild_id, limit)).fetchall()
    def tot_has_starter(self, guild_id: int, user_id: int) -> bool: return bool(self.conn.execute("SELECT tot_starter FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()[0])
    def tot_give_starter(self, guild_id: int, user_id: int, treat: str):
        self._ensure(guild_id, user_id); self.add_treat(guild_id, user_id, treat)
        self.conn.execute("UPDATE users SET tot_starter = 1 WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)); self.conn.commit()
    def reset_karma(self, guild_id: int): self.conn.execute("UPDATE users SET karma = 0 WHERE guild_id = ?", (guild_id,)); self.conn.commit()
    def add_karma(self, guild_id: int, user_id: int, amount: int): self._ensure(guild_id, user_id); self.conn.execute("UPDATE users SET karma = karma + ? WHERE guild_id = ? AND user_id = ?", (amount, guild_id, user_id)); self.conn.commit()
    def get_karma(self, guild_id: int, user_id: int) -> int: row = self.conn.execute("SELECT karma FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone(); return row[0] if row else 0
    def top_karma(self, guild_id: int, limit: int): return self.conn.execute("SELECT user_id, karma FROM users WHERE guild_id = ? AND karma > 0 ORDER BY karma DESC LIMIT ?", (guild_id, limit)).fetchall()
    def steal_currency(self, guild_id: int, victim_id: int, thief_id: int, amount: float, column: str) -> float:
        self._ensure(guild_id, victim_id); self._ensure(guild_id, thief_id)
        have = self.conn.execute(f"SELECT {column} FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, victim_id)).fetchone()[0]
        taken = round(max(0, min(amount, have)), 2)
        if taken:
            self.conn.execute(f"UPDATE users SET {column} = ROUND({column} - ?, 2) WHERE guild_id = ? AND user_id = ?", (taken, guild_id, victim_id))
            self.conn.execute(f"UPDATE users SET {column} = ROUND({column} + ?, 2) WHERE guild_id = ? AND user_id = ?", (taken, guild_id, thief_id))
            self.conn.commit()
        return taken

    # Mutes (No DMs)
    def add_mute(self, guild_id: int, user_id: int, until: int, role_ids: str, reason: str, moderator_id: int): self.conn.execute("INSERT OR REPLACE INTO mutes (guild_id, user_id, until, role_ids, reason, moderator_id) VALUES (?, ?, ?, ?, ?, ?)", (guild_id, user_id, until, role_ids, reason, moderator_id)); self.conn.commit()
    def get_mute(self, guild_id: int, user_id: int): return self.conn.execute("SELECT until, role_ids, reason, moderator_id FROM mutes WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
    def list_mutes(self, guild_id: int): return self.conn.execute("SELECT user_id, until FROM mutes WHERE guild_id = ?", (guild_id,)).fetchall()
    def remove_mute(self, guild_id: int, user_id: int): self.conn.execute("DELETE FROM mutes WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)); self.conn.commit()

    # Treat Pot
    def get_pot_total(self, guild_id: int) -> int:
        row = self.conn.execute("SELECT SUM(amount) FROM treat_pot WHERE guild_id = ?", (guild_id,)).fetchone()
        return row[0] if row[0] else 0
    def get_pot_investors(self, guild_id: int) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM treat_pot WHERE guild_id = ? AND amount > 0", (guild_id,)).fetchone()
        return row[0] if row[0] else 0
    def add_to_pot(self, guild_id: int, user_id: int, amount: int):
        self.conn.execute("INSERT INTO treat_pot (guild_id, user_id, amount) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET amount = amount + ?", (guild_id, user_id, amount, amount))
        self.conn.commit()
    def clear_pot(self, guild_id: int):
        self.conn.execute("DELETE FROM treat_pot WHERE guild_id = ?", (guild_id,))
        self.conn.commit()
    def get_all_investors(self, guild_id: int):
        return [r[0] for r in self.conn.execute("SELECT user_id FROM treat_pot WHERE guild_id = ? AND amount > 0", (guild_id,)).fetchall()]

db = Database(DB_PATH)

def fmt_num(value) -> str:
    value = round(float(value), 2)
    if value == int(value): return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")

# ======================================================================
#  Role helpers
# ======================================================================
def _norm_role(text: str) -> str: return re.sub(r"[\W_]+", "", text.lower())
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
async def give_xp(member: discord.Member, amount: int):
    old_xp, new_xp = db.add_xp(member.guild.id, member.id, amount)
    new_level = level_from_xp(new_xp)[0]
    if new_level > level_from_xp(old_xp)[0]: await sync_level_roles(member, new_level)
def has_level_role(member: discord.Member) -> bool:
    if member.guild_permissions.administrator: return True
    for _, role_name in LEVEL_ROLES:
        role = find_role(member.guild, role_name)
        if role and role in member.roles: return True
    return False
def find_channel_mention(guild: discord.Guild, keyword: str) -> str:
    channel = discord.utils.find(lambda c: keyword in c.name.lower(), guild.channels)
    return channel.mention if channel else f"#{keyword}"
def is_moderator(member: discord.Member) -> bool:
    perms = member.guild_permissions
    return member.id == member.guild.owner_id or perms.administrator or perms.moderate_members

DURATION_RE = re.compile(r"^\s*(\d+)\s*([dhm])\s*$", re.IGNORECASE)
def parse_duration(text: str) -> int | None:
    match = DURATION_RE.match(text)
    if not match: return None
    unit = {"d": 86400, "h": 3600, "m": 60}[match.group(2).lower()]
    seconds = int(match.group(1)) * unit
    if seconds <= 0 or seconds > 365 * 86400: return None
    return seconds
def describe_duration(seconds: int) -> str:
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    parts = []
    if days: parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours: parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes or not parts: parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(parts)

async def release_mute(guild: discord.Guild, user_id: int) -> bool:
    row = db.get_mute(guild.id, user_id)
    if row is None: return False
    _, role_ids, _, _ = row
    db.remove_mute(guild.id, user_id)
    member = guild.get_member(user_id)
    if member is None: return True
    muted_role = find_role(guild, MUTED_ROLE_NAME)
    try:
        if muted_role and muted_role in member.roles:
            await member.remove_roles(muted_role, reason="Mute is over")
        restore = [guild.get_role(int(x)) for x in role_ids.split(",") if x]
        restore = [r for r in restore if r and r < guild.me.top_role and r not in member.roles]
        if restore:
            await member.add_roles(*restore, reason="Mute is over, roles returned")
    except (discord.Forbidden, discord.HTTPException): pass
    return True

# ======================================================================
#  Shop & Treats
# ======================================================================
HALLOWEEN_EXPIRY = 1793750400   
SHOP_CATEGORIES = {
    "expie": {
        "label": "Expie Pack", "flag": "NEW",
        "items": [
            {"key": "expie_heart", "name": "Expie Heart", "emoji": "Expie_Heart", "role": "Expie Heart", "description": "idk jsut a hearatea weeweweweewe", "mups": 5, "vups": 0},
            {"key": "brain_damage", "name": "BRAIN DAMAGE", "emoji": "BRAIN_DAMAGE", "role": "BRAIN DAMAGE", "description": "Brain.exe has stopped working", "mups": 20, "vups": 0},
            {"key": "blehh_expie", "name": "Blehh Expie", "emoji": "De_plehxpie", "role": "Blehh Expie", "description": "Blehhh!", "mups": 50, "vups": 25},
        ],
    },
    "other": {
        "label": "Other", "flag": "",
        "items": [
            {"key": "cool_femboy", "name": "Cool femboy", "emoji": "Da_cool_femboy", "role": "Cool femboy", "description": "Da cool femboy!", "mups": 30, "vups": 0},
            {"key": "maplet", "name": "Maplet", "emoji": "Silly_Critter", "role": "Maplet", "description": "Little Maplefuw", "mups": 5, "vups": 0},
        ],
    },
    "halloween": {
        "label": "Halloween Pack", "flag": "LIMITED", "expiry": HALLOWEEN_EXPIRY,
        "items": [
            {"key": "halloween_pumpkin_cat", "name": "Halloween Pumpkin Cat", "emoji": "halloween_pupkin_cat", "role": "Halloween Pumpkin", "description": "Spooky month, silly critters!", "mups": 22, "vups": 2},
        ],
    },
    "silly": {
        "label": "Silly Pack", "flag": "",
        "items": [
            {"key": "blahaj_laying", "name": "Blahaj Laying", "emoji": "Blahaj_laying", "role": "Blahaj Laying", "description": "Classic?", "mups": 10, "vups": 0},
            {"key": "sus_cat", "name": "Sus cat", "emoji": "Cat_Sus", "role": "Sus cat", "description": "Looks suspicious.... Is probably fine...?", "mups": 150, "vups": 50},
            {"key": "true", "name": "TRUE...", "emoji": "De_true", "role": "TRUE...", "description": "who is he.?", "mups": 250, "vups": 0},
        ],
    },
    "boykisser": {
        "label": "Boykisser Pack", "flag": "",
        "items": [
            {"key": "shy_boykisser", "name": "Shy Boykisser", "emoji": "Shy_kisser", "role": "Shy Boykisser", "description": "For the shy ones ~w~", "mups": 20, "vups": 0},
            {"key": "blahaj_boykisser", "name": "Blahaj Boykisser", "emoji": "BoykisserBlahaj", "role": "Blahaj Boykisser", "description": "Hugg da sharkw!", "mups": 20, "vups": 0},
            {"key": "paws_boykisser", "name": "Paws Boykisser", "emoji": "Paws_Boykisser", "role": "Paws Boykisser", "description": "Show them your paws", "mups": 45, "vups": 2},
            {"key": "kiss_boykisser", "name": "Kiss boykisser", "emoji": "kissing_boykisser", "role": "Kiss boykisser", "description": "Kissing boykisser near you!", "mups": 60, "vups": 10},
            {"key": "ice_cream_boykisser", "name": "Ice Cream Boykisser", "emoji": "icecreambkisser", "role": "Ice Cream Boykisser", "description": "Kisser in da ice cream ^^", "mups": 50, "vups": 0},
        ],
    },
    "wyph": {
        "label": "WYPH PACK?", "flag": "",
        "items": [
            {"key": "wyphlet", "name": "Wyphlet", "emoji": "Wyphlet", "role": "Wyphlet", "description": "WAIT... A REAL WYP-", "mups": 500, "vups": 500},
        ],
    }
}

TREATS = [
    ("candy_bear", "Candy Bear"), ("black_bombob", "Black Bombob"), ("pumpkin_cookie", "Pumpkin Cookie"),
    ("ghost_marshmallow", "Ghost Marshmallow"), ("spooky_lollipop", "Spooky Lollipop"), ("witch_hat_truffle", "Witch Hat Truffle"),
    ("bat_wing_gummy", "Bat Wing Gummy"), ("cat_paw_fudge", "Cat Paw Fudge"), ("moon_caramel", "Moon Caramel"),
    ("cursed_candy_corn", "Cursed Candy Corn"), ("gloomy_gumdrop", "Gloomy Gumdrop"), ("skeleton_biscuit", "Skeleton Biscuit"),
    ("cobweb_cotton_candy", "Cobweb Cotton Candy"), ("cauldron_cocoa", "Cauldron Cocoa"), ("mummy_muffin", "Mummy Muffin"),
    ("sugar_skull", "Sugar Skull"), ("toffee_toad", "Toffee Toad"), ("maple_taffy", "Maple Taffy"),
    ("goober_jelly", "Goober Jelly"), ("chocolate_pawprint", "Chocolate Pawprint"),
]
TREAT_KEYS = [key for key, _ in TREATS]
TREAT_NAMES = dict(TREATS)

LOVELY_HORNET_ROLE = "Lovely Hornet"   
HORNET_HALLOW_ROLE = "Hornet Hallow"      
LMAO_HORNET_ROLE = "Lmao Hornet"          
GHOSTLY_ROLE = "Ghostly Role"                 
WITCH_ROLE = "Witch Role"

EVENT_NAME_EMOJIS = [
    {"name": "Lovely Hornet", "role": LOVELY_HORNET_ROLE},
    {"name": "Hornet Hallow", "role": HORNET_HALLOW_ROLE},
    {"name": "Lmao Hornet", "role": LMAO_HORNET_ROLE},
    {"name": "Ghostly Role", "role": GHOSTLY_ROLE},
    {"name": "Witch Role", "role": WITCH_ROLE},
]

TRICK_SUCCESS_CHANCE = 0.5      
GHOST_MARSHMALLOW_REDUCTION = 0.05
TRICK_XP_PERCENT = 20           
TRICK_CURRENCY_RANGE = (10, 20)
TREAT_REPEAT_CHANCE = 0.70      
LMAO_CHANCE = 0.01
GHOST_CHANCE = 0.01
TREAT_END_REWARD_MUPS = 5       

KARMA_TREAT = 2                 
KARMA_TRICK_FAIL = -1           

KNOCK_TIMEOUT = 90              
KNOCK_COOLDOWN = 180            
KNOCK_PAIR_COOLDOWN = 3600      

pending_knocks: set[int] = set()
knock_last: dict[int, float] = {}
knock_pair_last: dict[tuple[int, int], float] = {}
trick_last: dict[int, float] = {}

def treat_name(key: str) -> str: return TREAT_NAMES.get(key, key)
def tot_is_active() -> bool: return db.get_setting("tot_active", "0") == "1"
def ensure_treat_starter(guild_id: int, user_id: int):
    if not tot_is_active() or db.tot_has_starter(guild_id, user_id): return
    db.tot_give_starter(guild_id, user_id, random.choice(TREAT_KEYS))
def is_night() -> bool:
    hour = (datetime.datetime.now(datetime.timezone.utc).hour + NIGHT_UTC_OFFSET) % 24
    return hour >= 22 or hour < 6
def treat_currency_mult(treats: dict) -> float:
    mult = 1.0
    if "cursed_candy_corn" in treats: mult *= 1.03
    if "cat_paw_fudge" in treats and random.random() < 0.10: mult *= 2
    return mult
def gumdrop_refund(guild_id: int, user_id: int, mups: float, vups: float) -> str | None:
    if "gloomy_gumdrop" not in db.get_treats(guild_id, user_id): return None
    if random.random() >= 0.10: return None
    back_m, back_v = round(mups / 2, 2), round(vups / 2, 2)
    if not back_m and not back_v: return None
    db.add_currency(guild_id, user_id, back_m, back_v)
    parts = []
    if back_m: parts.append(f"{fmt_num(back_m)} Mups")
    if back_v: parts.append(f"{fmt_num(back_v)} Vups")
    return f"Your Gloomy Gumdrop gave back {' and '.join(parts)}!"
def get_active_shop_categories():
    active = {}
    now = time.time()
    for key, data in SHOP_CATEGORIES.items():
        if "expiry" in data and now > data["expiry"]: continue
        active[key] = data
    return active

def resolve_treat(guild_id: int, knocker_id: int, target_id: int) -> dict:
    mine = db.get_treats(guild_id, target_id)
    if not mine: return {"treat": None, "is_new": False, "count": 0, "lmao": False}
    theirs = db.get_treats(guild_id, knocker_id)
    repeat_pool = [key for key in mine if key in theirs]
    new_pool = [key for key in mine if key not in theirs]
    
    treat_luck = db.get_flag(guild_id, knocker_id, "treat_luck_end") > time.time()
    repeat_chance = 0.40 if treat_luck else TREAT_REPEAT_CHANCE
    
    if repeat_pool and (not new_pool or random.random() < repeat_chance): key = random.choice(repeat_pool)
    else: key = random.choice(new_pool)
    is_new = db.add_treat(guild_id, knocker_id, key)
    count = db.get_treats(guild_id, knocker_id).get(key, 1)
    db.add_karma(guild_id, target_id, KARMA_TREAT)
    lmao = db.get_setting("lmao_hornet_given", "0") != "1" and random.random() < LMAO_CHANCE
    return {"treat": key, "is_new": is_new, "count": count, "lmao": lmao}

def resolve_trick(guild_id: int, knocker_id: int, target_id: int, roll: float) -> dict:
    if db.use_ghost_shield(guild_id, knocker_id):
        db.add_karma(guild_id, target_id, KARMA_TRICK_FAIL)
        return {"success": False, "karma": KARMA_TRICK_FAIL, "shielded": True}

    chance = TRICK_SUCCESS_CHANCE
    if "ghost_marshmallow" in db.get_treats(guild_id, knocker_id): chance -= GHOST_MARSHMALLOW_REDUCTION
    if db.get_flag(guild_id, target_id, "thief_brew_end") > time.time(): chance = 0.8
    
    if roll >= chance:
        db.add_karma(guild_id, target_id, KARMA_TRICK_FAIL)
        return {"success": False, "karma": KARMA_TRICK_FAIL, "shielded": False}

    result = {"success": True, "kind": None, "amount": 0, "currency": None, "treat": None, "karma": 0, "ghost": False, "shielded": False}
    victim_treats = db.get_treats(guild_id, knocker_id)
    thief_treats = db.get_treats(guild_id, target_id)
    stealable = [key for key in victim_treats if key not in thief_treats]
    if stealable:
        treat = random.choice(stealable)
        db.remove_treat(guild_id, knocker_id, treat)
        db.add_treat(guild_id, target_id, treat)
        result["treat"] = treat

    loss = 0
    if random.random() < 0.5:
        data = db.get(guild_id, knocker_id)
        _, into_level, _ = level_from_xp(data["xp"])
        stolen = int(into_level * TRICK_XP_PERCENT / 100)
        result["kind"] = "xp"
        result["amount"] = stolen
        if stolen:
            db.add_xp(guild_id, knocker_id, -stolen)
            db.add_xp(guild_id, target_id, stolen)
            loss = min(10, 1 + stolen // 25)
    else:
        column = random.choice(["mups", "vups"])
        wanted = random.randint(*TRICK_CURRENCY_RANGE)
        taken = db.steal_currency(guild_id, knocker_id, target_id, wanted, column)
        result["kind"] = "currency"
        result["amount"] = taken
        result["currency"] = column.capitalize()
        if taken: loss = 1 + int(taken) // 5

    if result["treat"]: loss += 1
    if loss == 0: loss = 1
    result["karma"] = -loss
    db.add_karma(guild_id, target_id, -loss)
    result["ghost"] = random.random() < GHOST_CHANCE
    return result

async def check_hornet_hallow(guild: discord.Guild, member: discord.Member) -> bool:
    if len(db.get_treats(guild.id, member.id)) < len(TREAT_KEYS): return False
    role = await get_role(guild, HORNET_HALLOW_ROLE)
    if role is None or role in member.roles: return False
    try: await member.add_roles(role, reason="Collected every treat")
    except (discord.Forbidden, discord.HTTPException): return False
    return True

async def grant_role_by_name(member: discord.Member, role_name: str, reason: str) -> bool:
    role = await get_role(member.guild, role_name)
    if role is None or role in member.roles: return False
    try: await member.add_roles(role, reason=reason)
    except (discord.Forbidden, discord.HTTPException): return False
    return True


# ======================================================================
#  Views & UI Classes
# ======================================================================
class RoleGroupSelect(discord.ui.Select):
    def __init__(self, member: discord.Member, group: dict, placeholder: str, multiple: bool, row: int):
        held = set(member.roles)
        has_any = any(role in held for role in group.values() if role)
        options = []
        default_used = False
        for label, role in group.items():
            selected = (role in held) if role else (not has_any)
            if not multiple and selected:
                if default_used: selected = False
                default_used = True
            options.append(discord.SelectOption(label=label, value=label, default=selected))
        super().__init__(placeholder=placeholder, min_values=0, max_values=len(options) if multiple else 1, options=options[:25], row=row)
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
            await interaction.response.send_message("I can't change those roles.", ephemeral=True)
            return
        for option in self.options:
            if option.value == "None": option.default = not wanted
            else: option.default = option.value in chosen
        await interaction.response.edit_message(content="Saved!", view=self.view)

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
        for cat_data in SHOP_CATEGORIES.values(): all_items.extend(cat_data["items"])
        all_items.extend(EVENT_NAME_EMOJIS)
        seen = set()
        default_used = False
        for item in all_items:
            role = find_shop_role(guild, item["role"])
            if role is None or role.id in seen: continue
            seen.add(role.id)
            self.all_emoji_roles.append(role)
            if role in member.roles:
                is_default = not default_used
                default_used = True
                options.append(discord.SelectOption(label=item["name"], value=str(role.id), default=is_default))
        if len(options) == 1:
            options = [discord.SelectOption(label="You don't own any name emojis", value="None")]
            super().__init__(placeholder="Edit your Name emoji!", options=options, disabled=True, row=2)
        else:
            options[0].default = not default_used
            super().__init__(placeholder="Edit your Name emoji!", min_values=1, max_values=1, options=options[:25], row=2)
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
            await interaction.response.send_message("I don't have permission.", ephemeral=True)
            return
        for option in self.options: option.default = (option.value == val)
        await interaction.response.edit_message(content="Saved!", view=self.view)

class NameChangeModal(discord.ui.Modal, title="Change your name (100 Mups)"):
    new_name = discord.ui.TextInput(label="New Name", placeholder="Enter your new nickname...", max_length=32, required=True)
    async def on_submit(self, interaction: discord.Interaction):
        member = interaction.user
        guild = interaction.guild
        data = db.get(guild.id, member.id)
        if data["mups"] < 100: return await interaction.response.send_message("Not enough Mups!", ephemeral=True)
        try: await member.edit(nick=self.new_name.value.strip(), reason="Bought name change")
        except discord.Forbidden: return await interaction.response.send_message("No permission.", ephemeral=True)
        except discord.HTTPException: return await interaction.response.send_message("Failed.", ephemeral=True)
        db.spend(guild.id, member.id, 100, 0)
        text = f"Your name has been changed to **{self.new_name.value.strip()}** for 100 Mups!"
        back = gumdrop_refund(guild.id, member.id, 100, 0)
        if back: text += f"\n{back}"
        await interaction.response.send_message(text, ephemeral=True)

class EditNameButton(discord.ui.Button):
    def __init__(self): super().__init__(label="Edit your name! (100 Mups)", style=discord.ButtonStyle.success, row=3)
    async def callback(self, interaction: discord.Interaction): await interaction.response.send_modal(NameChangeModal())

class EditProfilePanel(discord.ui.View):
    def __init__(self, member: discord.Member):
        super().__init__(timeout=600)
        guild = member.guild
        color_group = {"None": None}
        for label, lookup in COLOR_ROLES:
            names = lookup if isinstance(lookup, (tuple, list)) else (lookup,)
            role = next((r for r in (find_role(guild, n) for n in names) if r), None)
            if role: color_group[label] = role
        if len(color_group) > 1: self.add_item(RoleGroupSelect(member, color_group, "Edit your Name color!", False, 0))
        pronoun_group = {"None": None}
        for label, lookup in PRONOUN_ROLES:
            names = lookup if isinstance(lookup, (tuple, list)) else (lookup,)
            role = next((r for r in (find_role(guild, n) for n in names) if r), None)
            if role: pronoun_group[label] = role
        if len(pronoun_group) > 1: self.add_item(RoleGroupSelect(member, pronoun_group, "Edit your pronouns!", True, 1))
        self.add_item(EmojiSelect(member, guild))
        self.add_item(EditNameButton())

class ProfileView(discord.ui.View):
    def __init__(self, member: discord.Member):
        super().__init__(timeout=600)
        self.member = member
    @discord.ui.button(label="Edit Server Profile!", style=discord.ButtonStyle.primary)
    async def edit_profile(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member.id: return await interaction.response.send_message("Not your profile!", ephemeral=True)
        try:
            view = EditProfilePanel(interaction.user)
            embed = discord.Embed(title="Edit Server Profile", description="Change your roles, emojis, and name here!", colour=CREAM)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception:
            if not interaction.response.is_done(): await interaction.response.send_message("Error.", ephemeral=True)

class ConfirmView(discord.ui.View):
    def __init__(self): super().__init__(timeout=300)
    @discord.ui.button(label="I agree, let me in", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user
        newcomer_role = await get_role(guild, NEWCOMER_ROLE_NAME)
        joined_role = find_role(guild, JOINED_ROLE_NAME)
        try:
            if newcomer_role: await member.add_roles(newcomer_role, reason="Passed verification")
            if joined_role and joined_role in member.roles: await member.remove_roles(joined_role, reason="Passed verification")
        except discord.Forbidden: return await interaction.response.edit_message(content="No permissions.", view=None)
        await interaction.response.edit_message(content="You're verified! Welcome to the server, have fun and be silly :3", view=None)

class VerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Verify", style=discord.ButtonStyle.primary, custom_id="silly_verify_button")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user
        newcomer_role = find_role(guild, NEWCOMER_ROLE_NAME)
        if newcomer_role and newcomer_role in member.roles: return await interaction.response.send_message("You're already verified!", ephemeral=True)
        rules = find_channel_mention(guild, "rules")
        know = find_channel_mention(guild, "what-to-know")
        await interaction.response.send_message(f"Clicking on verify, you accept that you have read channels: {rules} and {know}.\nPress the button below to confirm.", view=ConfirmView(), ephemeral=True)

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
        if not name: return await interaction.response.send_message("The name can't be empty.", ephemeral=True)
        try: limit = int(self.limit_input.value.strip())
        except ValueError: return await interaction.response.send_message(f"The limit must be a number from 1 to {VOICE_MAX_LIMIT}.", ephemeral=True)
        if not 1 <= limit <= VOICE_MAX_LIMIT: return await interaction.response.send_message(f"The limit must be a number from 1 to {VOICE_MAX_LIMIT}.", ephemeral=True)
        if is_blocked_name(name): return await interaction.response.send_message("Please choose a different name, that one isn't allowed.", ephemeral=True)
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
        else: access_text = f"**Private**: only you and {', '.join(m.mention for m in self.invited) or 'nobody picked yet'}"
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
        if self.name is None or self.limit is None: return await interaction.response.send_message("Please press **Set name and limit** first.", ephemeral=True)
        guild = interaction.guild
        existing_id = db.get_voice_by_owner(guild.id, self.owner.id)
        if existing_id:
            existing = guild.get_channel(existing_id)
            if existing: return await interaction.response.edit_message(content=f"You already have a voice chat: {existing.mention}", embed=None, view=None)
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

        try: channel = await guild.create_voice_channel(name=self.name, user_limit=self.limit, category=self.category, overwrites=overwrites, reason=f"Private voice created by {self.owner}")
        except (discord.Forbidden, discord.HTTPException): return await interaction.response.edit_message(content="I couldn't create the channel. Please tell a mod.", embed=None, view=None)

        db.add_voice_channel(guild.id, channel.id, self.owner.id, int(time.time()))
        self.stop()
        await interaction.response.edit_message(content=f"Your voice chat is ready: {channel.mention}", embed=None, view=None)
        if self.owner.voice is not None:
            try: await self.owner.move_to(channel)
            except: pass

class CreateVoiceView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Create Voice", style=discord.ButtonStyle.primary, custom_id="create_voice_button")
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        guild = interaction.guild
        if not has_level_role(member): return await interaction.response.send_message("Private voice chats are for Silly Critters and up.", ephemeral=True)
        existing_id = db.get_voice_by_owner(guild.id, member.id)
        if existing_id:
            existing = guild.get_channel(existing_id)
            if existing: return await interaction.response.send_message(f"You already have a voice chat: {existing.mention}", ephemeral=True)
            db.remove_voice(existing_id)
        category = getattr(interaction.channel, "category", None)
        view = VoiceSetupView(member, category)
        await interaction.response.send_message(embed=view.make_embed(), view=view, ephemeral=True)

class GetRolesView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Get roles", style=discord.ButtonStyle.primary, custom_id="get_roles_button")
    async def open_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="Your special roles", description="Pick what you like in the menus below. Changes are saved right away, and you can come back any time.", colour=CREAM)
        await interaction.response.send_message(embed=embed, view=RolesPanelView(interaction.user), ephemeral=True)

class MysteryBoxView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
        
    @discord.ui.button(label="Open", style=discord.ButtonStyle.primary, custom_id="mystery_box_open")
    async def open_box(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild.id
        user = interaction.user
        
        self.clear_items()
        await interaction.response.edit_message(view=self)
        
        outcome = random.choices([1, 2, 3, 4, 5, 6, 7, 8], weights=[10, 20, 15, 10, 15, 10, 10, 10])[0]
        
        msg = ""
        if outcome == 1:
            msg = f"{user.mention}... There was nothing.. sadly ~w~.."
        elif outcome == 2:
            amount = random.randint(10, 100)
            db.add_currency(guild_id, user.id, amount, 0)
            msg = f"Wow {user.mention}!! You found {amount} Mups"
        elif outcome == 3:
            db.add_xp(guild_id, user.id, 200)
            msg = f"{user.mention} !! 200 xp was in this box! Great ^^"
        elif outcome == 4:
            all_items = [item for cat in get_active_shop_categories().values() for item in cat["items"] if item["key"] != "wyphlet"]
            item = random.choice(all_items)
            role = find_shop_role(interaction.guild, item["role"])
            if role and role not in user.roles:
                try: 
                    await user.add_roles(role, reason="Mystery Box")
                    msg = f"There was \"{item['name']}\" congradulations {user.mention}!"
                except:
                    db.add_currency(guild_id, user.id, 50, 0)
                    msg = f"There was a role, but I couldn't give it! You got 50 Mups instead, {user.mention}!"
            else:
                db.add_currency(guild_id, user.id, 50, 0)
                msg = f"There was a shop item you already own! You got 50 Mups instead, {user.mention}!"
        elif outcome == 5:
            user_treats = db.get_treats(guild_id, user.id)
            missing = [k for k in TREAT_KEYS if k not in user_treats]
            if missing:
                t = random.choice(missing)
                db.add_treat(guild_id, user.id, t)
                msg = f"{user.mention} , There was a \"{treat_name(t)}\" treat! Yay!!"
            else:
                db.add_currency(guild_id, user.id, 100, 0)
                msg = f"{user.mention}, you own all treats! You found 100 Mups instead! Yay!!"
        elif outcome == 6:
            msg = "BOMB... IT EXPLODES IN 5"
            await interaction.followup.send(msg)
            bomb_msg = await interaction.original_response()
            for i in range(4, 0, -1):
                await asyncio.sleep(1)
                try: await bomb_msg.edit(content=f"BOMB... IT EXPLODES IN {i}")
                except: pass
            await asyncio.sleep(1)
            try: await interaction.channel.purge(limit=10)
            except: pass
            await interaction.channel.send(f"{user.mention} opened a trapped box! The chat was cleared!")
            return
        elif outcome == 7:
            amount = random.randint(10, 40)
            target = db.get_random_user(guild_id)
            if target and target != user.id:
                taken = db.steal_currency(guild_id, user.id, target, amount, "mups")
                target_user = interaction.guild.get_member(target)
                t_name = target_user.mention if target_user else "someone"
                msg = f"You lost {taken} Mups! They were given to {t_name}"
            else:
                db.take_currency(guild_id, user.id, amount, 0)
                msg = f"You lost {amount} Mups! They fell into the void."
        elif outcome == 8:
            target = db.get_random_user(guild_id)
            if target and target != user.id:
                t_cookies = db.get(guild_id, target)["cookies_received"]
                if t_cookies > 0:
                    stolen = min(random.randint(1, 3), t_cookies)
                    db.conn.execute("UPDATE users SET cookies_received = cookies_received - ? WHERE guild_id = ? AND user_id = ?", (stolen, guild_id, target))
                    db.conn.execute("UPDATE users SET cookies_received = cookies_received + ? WHERE guild_id = ? AND user_id = ?", (stolen, guild_id, user.id))
                    db.conn.commit()
                    target_user = interaction.guild.get_member(target)
                    t_name = target_user.mention if target_user else "someone"
                    msg = f"Sneaky! {user.mention} stole {stolen} cookies from {t_name}!"
                else:
                    msg = f"A ghost tried to steal cookies for {user.mention}, but the target had none!"
            else:
                msg = f"A ghost tried to steal cookies for {user.mention}, but couldn't find anyone!"
                
        await interaction.followup.send(msg)

class TreatPotModal(discord.ui.Modal, title="Insert a Treat"):
    amount = discord.ui.TextInput(label="Amount", placeholder="How many to insert?", max_length=4, required=True)
    def __init__(self, treat_key: str):
        super().__init__()
        self.treat_key = treat_key
    async def on_submit(self, interaction: discord.Interaction):
        try: amt = int(self.amount.value)
        except ValueError: return await interaction.response.send_message("Invalid amount!", ephemeral=True)
        if amt <= 0: return await interaction.response.send_message("Amount must be positive!", ephemeral=True)
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        owned = db.get_treats(guild_id, user_id).get(self.treat_key, 0)
        
        if amt > owned: return await interaction.response.send_message(f"You only have {owned} of this treat!", ephemeral=True)
            
        db.remove_treat(guild_id, user_id, self.treat_key, amt)
        db.add_to_pot(guild_id, user_id, amt)
        
        await interaction.response.send_message(f"You inserted {amt}x {treat_name(self.treat_key)} into the Pot! Buffs lost, but wait for the reward!", ephemeral=True)
        await update_treat_pot_msg(interaction.guild)

class TreatPotSelect(discord.ui.Select):
    def __init__(self, treats: dict):
        options = [discord.SelectOption(label=f"{treat_name(k)} (x{v})", value=k) for k, v in treats.items()]
        super().__init__(placeholder="Pick a treat to insert", min_values=1, max_values=1, options=options[:25])
    async def callback(self, interaction: discord.Interaction): await interaction.response.send_modal(TreatPotModal(self.values[0]))

class TreatPotView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Insert a Treat!", style=discord.ButtonStyle.success, custom_id="treat_pot_insert")
    async def insert(self, interaction: discord.Interaction, button: discord.ui.Button):
        treats = db.get_treats(interaction.guild.id, interaction.user.id)
        if not treats: return await interaction.response.send_message("You don't have any treats to insert!", ephemeral=True)
        view = discord.ui.View()
        view.add_item(TreatPotSelect(treats))
        await interaction.response.send_message("Pick a treat to insert, then pick an amount.", view=view, ephemeral=True)

async def update_treat_pot_msg(guild: discord.Guild):
    msg_id = db.get_setting("pot_msg_id", "")
    ch_id = db.get_setting("pot_ch_id", "")
    if not msg_id or not ch_id: return
    channel = guild.get_channel(int(ch_id))
    if not channel: return
    try:
        msg = await channel.fetch_message(int(msg_id))
        total_treats = db.get_pot_total(guild.id)
        investors = db.get_pot_investors(guild.id)
        xp_per = (total_treats * 15) // investors if investors else 0
        mups_per = (total_treats * 3) // investors if investors else 0
        vups_per = (total_treats * 2) // investors if investors else 0
        embed = discord.Embed(title="TREAT POT", description=(
            "Put your treats here to stack a buff for everyone! At the end of the event, all treats will turn into Xp and Mups with Vups! Then split to members who invested your treats!\n\n"
            f"**Treats:** {total_treats}\n**Investors:** {investors}\n**Xp Per user:** {xp_per}\n**Mups Per user:** {mups_per}\n**Vups Per user:** {vups_per}"
        ), colour=discord.Colour.orange())
        await msg.edit(embed=embed, view=TreatPotView())
    except: pass

CAULDRON_RECIPES = {
    "treat_luck": {"name": "Treat luck!", "desc": "Buff on 2 hours, while it your luck on getting a new treat is 30% -> 60%", "cost": {"pumpkin_cookie": 2, "gloomy_gumdrop": 2}},
    "ghostly_role": {"name": "Ghostly Role", "desc": "Ghost that somehow became a server role.. Only 3 users can obitain this!", "cost": {"ghost_marshmallow": 3, "mummy_muffin": 2}},
    "strange_xp": {"name": "Strange Xp Boost!", "desc": "It's.. and xp boost, but not at all..?", "cost": {"gloomy_gumdrop": 1}},
    "witch_role": {"name": "Witch Role", "desc": "Plehh.. A real witch??", "cost": {"witch_hat_truffle": 4, "bat_wing_gummy": 2, "spooky_lollipop": 2}},
    "thief_brew": {"name": "Thief's Brew", "desc": "Your chance tot trick someone 50% -> 80% for 6 hours", "cost": {"skeleton_biscuit": 2, "cauldron_cocoa": 1}},
    "ghost_shield": {"name": "Ghost Shield", "desc": "Get a ghostly shield to next 3 tricks on you!", "cost": {"skeleton_biscuit": 2, "ghost_marshmallow": 1}}
}

class CauldronCombineView(discord.ui.View):
    def __init__(self, r_key: str):
        super().__init__(timeout=None)
        self.r_key = r_key
    @discord.ui.button(label="Combine!", style=discord.ButtonStyle.success)
    async def combine(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        treats = db.get_treats(guild_id, user_id)
        r1, r2, rt, rt_c = db.get_setting("cauld_r1", ""), db.get_setting("cauld_r2", ""), db.get_setting("cauld_rt", ""), db.get_setting("cauld_rt_c", "")
        cost = {}
        is_random_treat = False
        if self.r_key == "random_treat": cost, is_random_treat = {rt_c: 2}, True
        elif self.r_key in CAULDRON_RECIPES: cost = CAULDRON_RECIPES[self.r_key]["cost"]
        else: return await interaction.response.send_message("Recipe expired!", ephemeral=True)
            
        for k, v in cost.items():
            if treats.get(k, 0) < v: return await interaction.response.send_message(f"You don't have enough {treat_name(k)}!", ephemeral=True)
                
        for k, v in cost.items(): db.remove_treat(guild_id, user_id, k, v)
            
        msg = ""
        now = int(time.time())
        if is_random_treat:
            db.add_treat(guild_id, user_id, rt)
            msg = f"You successfully brewed a **{treat_name(rt)}**!"
        elif self.r_key == "treat_luck":
            db.set_flag(guild_id, user_id, "treat_luck_end", now + 2*3600)
            msg = "You brewed Treat Luck! New treat chance is 60% for 2 hours."
        elif self.r_key == "strange_xp":
            mult = round(random.uniform(0.5, 3.0), 2)
            db.set_xp_buff(guild_id, user_id, now + 3600, mult)
            msg = f"You brewed Strange XP Boost! Your XP multiplier is now {mult}x for 1 hour."
        elif self.r_key == "ghostly_role":
            role = await get_role(interaction.guild, GHOSTLY_ROLE)
            if role and len(role.members) >= 3: msg = "The Ghostly Role is out of stock! Max 3 users. (Treats consumed anyway, magic is unstable!)"
            elif role:
                await interaction.user.add_roles(role, reason="Cauldron")
                msg = "You obtained the Ghostly Role!"
            else: msg = "Role missing."
        elif self.r_key == "witch_role":
            role = await get_role(interaction.guild, WITCH_ROLE)
            if role: 
                await interaction.user.add_roles(role, reason="Cauldron")
                msg = "You obtained the Witch Role!"
            else: msg = "Role missing."
        elif self.r_key == "thief_brew":
            db.set_flag(guild_id, user_id, "thief_brew_end", now + 6*3600)
            msg = "You brewed Thief's Brew! Trick chance is 80% for 6 hours."
        elif self.r_key == "ghost_shield":
            db.set_ghost_shield(guild_id, user_id, 3)
            msg = "You brewed a Ghost Shield! Block next 3 tricks."
        await interaction.response.send_message(msg, ephemeral=True)

class CauldronSelect(discord.ui.Select):
    def __init__(self, r1: str, r2: str, rt: str):
        options = []
        if r1 in CAULDRON_RECIPES: options.append(discord.SelectOption(label=CAULDRON_RECIPES[r1]["name"], value=r1))
        if r2 in CAULDRON_RECIPES: options.append(discord.SelectOption(label=CAULDRON_RECIPES[r2]["name"], value=r2))
        options.append(discord.SelectOption(label="Random Treat", value="random_treat"))
        super().__init__(placeholder="Select a recipe", options=options)
    async def callback(self, interaction: discord.Interaction):
        r_key = self.values[0]
        rt, rt_c = db.get_setting("cauld_rt", ""), db.get_setting("cauld_rt_c", "")
        name = "Random Treat"
        cost = {}
        if r_key == "random_treat":
            name = treat_name(rt)
            cost = {rt_c: 2}
        else:
            name = CAULDRON_RECIPES[r_key]["name"]
            cost = CAULDRON_RECIPES[r_key]["cost"]
        cost_str = "\n".join([f"x{v} {treat_name(k)}" for k, v in cost.items()])
        embed = discord.Embed(title="Witch's Cauldron", description=f"**{name}, Recipe!**\n\n{cost_str}", colour=discord.Colour.purple())
        await interaction.response.edit_message(embed=embed, view=CauldronCombineView(r_key))

class CauldronOpenView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Open Recipe", style=discord.ButtonStyle.primary, custom_id="cauldron_open")
    async def open(self, interaction: discord.Interaction, button: discord.ui.Button):
        r1, r2, rt = db.get_setting("cauld_r1", ""), db.get_setting("cauld_r2", ""), db.get_setting("cauld_rt", "")
        if not r1: return await interaction.response.send_message("Cauldron is empty!", ephemeral=True)
        view = discord.ui.View()
        view.add_item(CauldronSelect(r1, r2, rt))
        await interaction.response.send_message("What do you want to brew?", view=view, ephemeral=True)

async def update_cauldron_msg(guild: discord.Guild):
    msg_id, ch_id = db.get_setting("cauld_msg_id", ""), db.get_setting("cauld_ch_id", "")
    if not msg_id or not ch_id: return
    channel = guild.get_channel(int(ch_id))
    if not channel: return
    r1, r2, rt = db.get_setting("cauld_r1", ""), db.get_setting("cauld_r2", ""), db.get_setting("cauld_rt", "")
    nxt = int(db.get_setting("cauld_next", "0"))
    mins = max(0, (nxt - int(time.time())) // 60)
    desc = f"Combine your treats for buffs or other treats!\n\nWhat can you get [Reset in {mins} Minutes]\n\n"
    if r1 in CAULDRON_RECIPES: desc += f"**{CAULDRON_RECIPES[r1]['name']}**\n{CAULDRON_RECIPES[r1]['desc']}\n\n"
    if r2 in CAULDRON_RECIPES: desc += f"**{CAULDRON_RECIPES[r2]['name']}**\n{CAULDRON_RECIPES[r2]['desc']}\n\n"
    desc += f"**{treat_name(rt)}**\nA very cool Treat to have!"
    try:
        msg = await channel.fetch_message(int(msg_id))
        await msg.edit(embed=discord.Embed(title="Witch's Cauldron", description=desc, colour=discord.Colour.purple()), view=CauldronOpenView())
    except: pass

class ActionReplyView(discord.ui.View):
    def __init__(self, action: str, giver: discord.Member, target: discord.Member, first_line: str, image_url: str | None):
        super().__init__(timeout=ACTION_REPLY_SECONDS)
        self.action = action; self.giver = giver; self.target = target; self.first_line = first_line; self.image_url = image_url; self.answered = False
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.target.id: return True
        await interaction.response.send_message("This one isn't for you!", ephemeral=True)
        return False
    @discord.ui.button(label="Sure!", style=discord.ButtonStyle.success)
    async def sure(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.answered = True; self.stop()
        back = ACTIONS[self.action]["back"].format(a=self.giver.mention, b=self.target.mention)
        embed = discord.Embed(description=f"{self.first_line}\n{back}", colour=PINK)
        if self.image_url: embed.set_image(url=self.image_url)
        await interaction.response.edit_message(embed=embed, view=None)
    async def on_timeout(self):
        if self.answered or not hasattr(self, 'message') or not self.message: return
        noun = ACTIONS[self.action]["noun"]
        ignored = f"{self.target.mention} didn't answer {self.giver.mention}'s {noun}..."
        embed = discord.Embed(description=f"{self.first_line}\n{ignored}", colour=PINK)
        if self.image_url: embed.set_image(url=self.image_url)
        try: await self.message.edit(embed=embed, view=None)
        except: pass


# ======================================================================
#  Background Tasks Initialization
# ======================================================================
@tasks.loop(minutes=10)
async def box_loop():
    if not GENERAL_CHANNEL_ID: return
    guild = client.get_guild(GUILD_ID)
    if not guild: return
    channel = guild.get_channel(GENERAL_CHANNEL_ID)
    if not channel: return
    await channel.send("Wowwie! Strange cat left mysterious box here!", view=MysteryBoxView())
@box_loop.before_loop
async def before_box(): await client.wait_until_ready()

@tasks.loop(minutes=1)
async def cauldron_loop():
    guild = client.get_guild(GUILD_ID)
    if not guild: return
    now = int(time.time())
    nxt = int(db.get_setting("cauld_next", "0"))
    if now >= nxt:
        keys = list(CAULDRON_RECIPES.keys())
        r1, r2 = random.sample(keys, 2)
        rt = random.choice(TREAT_KEYS)
        rt_c = random.choice([k for k in TREAT_KEYS if k != rt])
        db.set_setting("cauld_r1", r1)
        db.set_setting("cauld_r2", r2)
        db.set_setting("cauld_rt", rt)
        db.set_setting("cauld_rt_c", rt_c)
        db.set_setting("cauld_next", str(now + 3600))
        await update_cauldron_msg(guild)
    elif now % 300 < 60: 
        await update_cauldron_msg(guild)
@cauldron_loop.before_loop
async def before_cauld(): await client.wait_until_ready()

@tasks.loop(seconds=30)
async def mute_checker():
    guild = client.get_guild(GUILD_ID)
    if guild is None: return
    now = int(time.time())
    for user_id, until in db.list_mutes(guild.id):
        if now >= until: await release_mute(guild, user_id)
@mute_checker.before_loop
async def before_mute_checker(): await client.wait_until_ready()

@tasks.loop(hours=1)
async def treat_weekly():
    guild = client.get_guild(GUILD_ID)
    if guild is None: return
    now = time.time()
    for user_id in db.treat_owners(guild.id, "skeleton_biscuit"):
        last = db.get_flag(guild.id, user_id, "skeleton_last")
        if last == 0: db.set_flag(guild.id, user_id, "skeleton_last", now); continue
        if now - last < WEEK_SECONDS: continue
        db.set_flag(guild.id, user_id, "skeleton_last", now)
        db.take_currency(guild.id, user_id, 2, 1)
        if db.get(guild.id, user_id)["mups"] > 100 and random.random() < 0.10: db.add_currency(guild.id, user_id, 10, 0)
    all_items = [item for category in get_active_shop_categories().values() for item in category["items"]]
    for user_id in db.treat_owners(guild.id, "maple_taffy"):
        last = db.get_flag(guild.id, user_id, "taffy_last")
        if last == 0: db.set_flag(guild.id, user_id, "taffy_last", now); continue
        if now - last < WEEK_SECONDS: continue
        db.set_flag(guild.id, user_id, "taffy_last", now)
        member = guild.get_member(user_id)
        if member is None or not all_items: continue
        item = random.choice(all_items)
        role = find_shop_role(guild, item["role"])
        if role is None: continue
        if role not in member.roles:
            try: await member.add_roles(role, reason="Maple Taffy weekly gift")
            except: continue
@treat_weekly.before_loop
async def before_treat_weekly(): await client.wait_until_ready()


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
        self.add_view(MysteryBoxView())
        self.add_view(TreatPotView())
        self.add_view(CauldronOpenView())
        
        voice_tracker.start()
        voice_cleanup.start()
        mute_checker.start()
        treat_weekly.start()
        box_loop.start()
        cauldron_loop.start()
        
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
    async def on_ready(self):
        print(f"Bot is online as {self.user}")
client = SillyBot()


# ======================================================================
#  Commands
# ======================================================================
@client.tree.command(name="mute", description="Mute someone: removes their roles and gives Muted (mods only)")
@app_commands.describe(user="Who to mute", duration="10d = 10 days, 10h = 10 hours, 10m = 10 minutes", reason="Why they are muted")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
async def mute(interaction: discord.Interaction, user: discord.Member, duration: str, reason: str = "No reason given"):
    guild = interaction.guild
    mod = interaction.user
    if not is_moderator(mod): return await interaction.response.send_message("Only moderators can do this.", ephemeral=True)
    if user.bot or user.id == mod.id or user.id == guild.owner_id: return await interaction.response.send_message("I can't mute that person.", ephemeral=True)
    if user.top_role >= guild.me.top_role: return await interaction.response.send_message("That person's role is higher than mine.", ephemeral=True)
    muted_role = find_role(guild, MUTED_ROLE_NAME)
    if muted_role is None: return await interaction.response.send_message(f"I can't find a role called {MUTED_ROLE_NAME}.", ephemeral=True)
    seconds = parse_duration(duration)
    if seconds is None: return await interaction.response.send_message("Use a duration like 10d, 10h or 10m (up to 365d).", ephemeral=True)
    if "bat_wing_gummy" in db.get_treats(guild.id, user.id): seconds = max(60, int(seconds * 0.99))
    await interaction.response.defer()
    existing = db.get_mute(guild.id, user.id)
    if existing: role_ids = existing[1]
    else:
        removable = [r for r in user.roles if r != guild.default_role and r != muted_role and not r.managed and r < guild.me.top_role]
        role_ids = ",".join(str(r.id) for r in removable)
        try:
            if removable: await user.remove_roles(*removable, reason=f"Muted by {mod}: {reason}")
        except: return await interaction.followup.send("I couldn't remove their roles.", ephemeral=True)
    try: await user.add_roles(muted_role, reason=f"Muted by {mod}: {reason}")
    except: return await interaction.followup.send("I couldn't give the Muted role.", ephemeral=True)
    until = int(time.time()) + seconds
    db.add_mute(guild.id, user.id, until, role_ids, reason, mod.id)
    length = describe_duration(seconds)
    await interaction.followup.send(embed=discord.Embed(title="Muted", description=f"{user.mention} was muted for **{length}** by {mod.mention}.\nReason: {reason}", colour=PINK))

@client.tree.command(name="unmute", description="End a mute early and give the roles back (mods only)")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
async def unmute(interaction: discord.Interaction, user: discord.Member):
    if not is_moderator(interaction.user): return await interaction.response.send_message("Only moderators can do this.", ephemeral=True)
    await interaction.response.defer()
    if await release_mute(interaction.guild, user.id): await interaction.followup.send(embed=discord.Embed(title="Unmuted", description=f"{user.mention} was unmuted and got their roles back.", colour=PINK))
    else: await interaction.followup.send("That person isn't muted by me.", ephemeral=True)

@client.tree.command(name="daily", description="Collect your daily Mups and Vups")
@app_commands.guild_only()
async def daily(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    member = interaction.user
    now = int(time.time())
    treats = db.get_treats(guild_id, member.id)
    cooldown = DAILY_COOLDOWN_TRUFFLE if "witch_hat_truffle" in treats else DAILY_COOLDOWN
    window = DAILY_WINDOW_COCOA if "cauldron_cocoa" in treats else DAILY_WINDOW
    last = int(db.get_flag(guild_id, member.id, "daily_last"))
    streak = int(db.get_flag(guild_id, member.id, "daily_streak"))

    if last and now - last < cooldown:
        remaining = cooldown - (now - last)
        hours, rest = divmod(remaining, 3600)
        return await interaction.response.send_message(embed=discord.Embed(title="Daily already collected", description=f"Come back in {hours}h {rest // 60}m.", colour=CREAM), ephemeral=True)

    broken = bool(last) and now - last > window
    can_revive = (broken and streak > 0 and "toffee_toad" in treats and int(db.get_flag(guild_id, member.id, "revive_month")) != (datetime.datetime.fromtimestamp(now, datetime.timezone.utc).year * 100 + datetime.datetime.fromtimestamp(now, datetime.timezone.utc).month))
    if can_revive and int(db.get_flag(guild_id, member.id, "revive_warned")) != last:
        db.set_flag(guild_id, member.id, "revive_warned", last)
        return await interaction.response.send_message(embed=discord.Embed(title="Your streak broke", description=f"Your streak of **{streak}** broke, but your Toffee Toad can bring it back.\nUse **/revive_streak** to revive it, or use **/daily** again to start over.", colour=CREAM), ephemeral=True)

    new_streak = streak + 1 if (last and not broken) else 1
    mups = DAILY_BASE_MUPS + DAILY_MUPS_STEP * (new_streak - 1)
    vups = DAILY_BASE_VUPS + DAILY_VUPS_STEP * ((new_streak - 1) // 2)
    has_booster = any("CERTIFIED PAWBOOSTER" in r.name.upper() for r in member.roles)
    mult = (1.5 if has_booster else 1.0) * float(db.get_setting("global_curr_mult", "1.0")) * treat_currency_mult(treats)
    mups, vups = round(mups * mult, 2), round(vups * mult, 2)
    xp_exact = DAILY_BASE_XP + DAILY_XP_STEP * ((new_streak - 1) // 2)
    total_xp = xp_exact + db.get_flag(guild_id, member.id, "xp_frac")
    whole_xp = int(total_xp)
    db.set_flag(guild_id, member.id, "xp_frac", round(total_xp - whole_xp, 2))
    db.add_currency(guild_id, member.id, mups, vups)
    db.set_flag(guild_id, member.id, "daily_last", now)
    db.set_flag(guild_id, member.id, "daily_streak", new_streak)
    if whole_xp: await give_xp(member, whole_xp)
    embed = discord.Embed(title="Daily reward collected!", description=f"Streak x{new_streak}\nYou gain {fmt_num(mups)} of Mups and {fmt_num(vups)} of Vups!\nAlso +{fmt_num(xp_exact)} XP.", colour=PINK)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@client.tree.command(name="revive_streak", description="Bring back a broken daily streak (needs a Toffee Toad, once a month)")
@app_commands.guild_only()
async def revive_streak(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    member = interaction.user
    now = int(time.time())
    treats = db.get_treats(guild_id, member.id)
    if "toffee_toad" not in treats: return await interaction.response.send_message(embed=discord.Embed(description="You need a Toffee Toad treat to revive a streak.", colour=CREAM), ephemeral=True)
    cooldown = DAILY_COOLDOWN_TRUFFLE if "witch_hat_truffle" in treats else DAILY_COOLDOWN
    window = DAILY_WINDOW_COCOA if "cauldron_cocoa" in treats else DAILY_WINDOW
    last = int(db.get_flag(guild_id, member.id, "daily_last"))
    streak = int(db.get_flag(guild_id, member.id, "daily_streak"))
    if not last or streak <= 0 or now - last <= window: return await interaction.response.send_message(embed=discord.Embed(description="Your streak isn't broken, there is nothing to revive.", colour=CREAM), ephemeral=True)
    month_val = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).year * 100 + datetime.datetime.fromtimestamp(now, datetime.timezone.utc).month
    if int(db.get_flag(guild_id, member.id, "revive_month")) == month_val: return await interaction.response.send_message(embed=discord.Embed(description="You already revived a streak this month. Try again next month!", colour=CREAM), ephemeral=True)
    db.set_flag(guild_id, member.id, "revive_month", month_val)
    db.set_flag(guild_id, member.id, "daily_last", now - cooldown)
    await interaction.response.send_message(embed=discord.Embed(title="Streak revived!", description=f"Your streak of **{streak}** is back. Use **/daily** to keep it going.", colour=PINK))

search_group = app_commands.Group(name="search", description="Search the server", guild_only=True)
@search_group.command(name="treat", description="See who owns a treat")
@app_commands.choices(treat=[app_commands.Choice(name=name, value=key) for key, name in TREATS])
async def search_treat(interaction: discord.Interaction, treat: str):
    guild = interaction.guild
    lines = []
    total = 0
    for user_id, copies in db.treat_holders(guild.id, treat):
        member = guild.get_member(user_id)
        if member is None: continue
        total += 1
        if len(lines) < 30: lines.append(f"**#{len(lines) + 1}** {member.display_name} | x{copies}")
    if total > len(lines): lines.append(f"...and {total - len(lines)} more")
    embed = discord.Embed(title=f"Who has {treat_name(treat)}?", description="\n".join(lines) if lines else "Nobody has this treat yet.", colour=PINK)
    embed.set_footer(text=f"{total} critter{'s' if total != 1 else ''} with this treat")
    await interaction.response.send_message(embed=embed)
client.tree.add_command(search_group)

@client.tree.command(name="treatboard", description="Shows who collected the most treats")
@app_commands.guild_only()
async def treatboard(interaction: discord.Interaction):
    guild = interaction.guild
    lines = []
    for user_id, different, copies in db.treat_stats(guild.id, 50):
        member = guild.get_member(user_id)
        if member is None: continue
        lines.append(f"**#{len(lines) + 1}** {member.display_name} | {different}/{len(TREAT_KEYS)} treats ({copies} in total)")
        if len(lines) == 10: break
    embed = discord.Embed(title="Treat Leaderboard", description="\n".join(lines) if lines else "Nobody has treats yet. Use /knock-knock!", colour=PINK)
    await interaction.response.send_message(embed=embed)

@client.tree.command(name="karmastats", description="See your karma and the kindest critters")
@app_commands.guild_only()
async def karmastats(interaction: discord.Interaction):
    guild = interaction.guild
    mine = db.get_karma(guild.id, interaction.user.id)
    lines = []
    for user_id, karma in db.top_karma(guild.id, 50):
        member = guild.get_member(user_id)
        if member is None: continue
        lines.append(f"**#{len(lines) + 1}** {member.display_name} | {karma} karma")
        if len(lines) == 10: break
    board = "\n".join(lines) if lines else "Nobody has karma yet. Be kind!"
    embed = discord.Embed(title="Karma", description=f"Your karma: **{mine}**\n\n**Kindest critters:**\n{board}\n\nThe kindest one gets the **{LOVELY_HORNET_ROLE}** role when the event ends.", colour=PINK)
    await interaction.response.send_message(embed=embed)

@client.tree.command(name="tot_start", description="Start the Trick or Treat event (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def tot_start(interaction: discord.Interaction):
    if not is_server_admin(interaction): return await interaction.response.send_message("Admins only.", ephemeral=True)
    if tot_is_active(): return await interaction.response.send_message("The event is already running. Use /tot_end first.", ephemeral=True)
    await interaction.response.defer()
    guild = interaction.guild
    db.reset_karma(guild.id)
    for member in guild.members:
        if member.bot or db.tot_has_starter(guild.id, member.id): continue
        db.tot_give_starter(guild.id, member.id, random.choice(TREAT_KEYS))
    db.set_setting("tot_active", "1")
    pending_knocks.clear(); knock_last.clear(); knock_pair_last.clear(); trick_last.clear()
    embed = discord.Embed(title="Trick or Treat has started!", description=(f"Everyone who has no treat yet just got **one random treat** out of {len(TREAT_KEYS)}.\n\nUse **/knock-knock @user** to knock on someone's door. They choose **Treat** (you get a treat) or **Trick** (50% chance to scare you and steal XP or Mups or Vups, and one of your treats).\n\nCheck your collection with **/inventory**.\nKind critters earn karma. Collect all {len(TREAT_KEYS)} treats to become a **{HORNET_HALLOW_ROLE}**!"), colour=PINK)
    await interaction.followup.send(embed=embed)

@client.tree.command(name="tot_end", description="End the Trick or Treat event and hand out rewards (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def tot_end(interaction: discord.Interaction):
    if not is_server_admin(interaction): return await interaction.response.send_message("Admins only.", ephemeral=True)
    if not tot_is_active(): return await interaction.response.send_message("The event isn't running.", ephemeral=True)
    await interaction.response.defer()
    guild = interaction.guild
    db.set_setting("tot_active", "0")
    pending_knocks.clear()
    rewarded = 0; completed = 0
    for user_id, count in db.treat_totals(guild.id):
        db.add_currency(guild.id, user_id, count * TREAT_END_REWARD_MUPS, 0)
        rewarded += 1
        if count >= len(TREAT_KEYS): completed += 1
    winner_text = "Nobody earned karma this time."
    top = db.top_karma(guild.id, 1)
    if top:
        winner_id, winner_karma = top[0]
        winner = guild.get_member(winner_id)
        role = await get_role(guild, LOVELY_HORNET_ROLE)
        if winner is not None and role is not None:
            try:
                for holder in list(role.members):
                    if holder.id != winner.id: await holder.remove_roles(role, reason="New kindest critter")
                await winner.add_roles(role, reason="Most karma")
                winner_text = f"The kindest critter is {winner.mention} with **{winner_karma} karma**! They got the **{role.name}** role."
            except: winner_text = f"The kindest critter is {winner.mention} with {winner_karma} karma, but I couldn't give the role."
    embed = discord.Embed(title="Trick or Treat is over!", description=f"Thank you for playing! Everyone received **{TREAT_END_REWARD_MUPS} Mups per different treat** they collected ({rewarded} players).\nPlayers who found all {len(TREAT_KEYS)} treats: **{completed}**.\n\n{winner_text}\n\nYour treats stay in /inventory.", colour=PINK)
    await interaction.followup.send(embed=embed)

def treat_grid(names: list[str]) -> str:
    if not names: return "Nothing here"
    rows = [", ".join(names[i:i + 3]) for i in range(0, len(names), 3)]
    return ",\n".join(rows)
def build_buffs_embed() -> discord.Embed:
    lines = [f"**{name}**\n*{TREAT_INFO[key]}*" for key, name in TREATS]
    return discord.Embed(title="Treat Buffs", description="\n\n".join(lines)[:4000], colour=CREAM)

class InventoryView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Treats Buffs", style=discord.ButtonStyle.primary, custom_id="inventory_buffs_button")
    async def buffs(self, interaction: discord.Interaction, button: discord.ui.Button): await interaction.response.send_message(embed=build_buffs_embed(), ephemeral=True)

@client.tree.command(name="inventory", description="See your treat collection, or someone else's")
@app_commands.guild_only()
async def inventory(interaction: discord.Interaction, user: discord.Member | None = None):
    target = user or interaction.user
    if target.bot: return await interaction.response.send_message("Bots don't collect treats.", ephemeral=True)
    guild_id = interaction.guild.id
    ensure_treat_starter(guild_id, target.id)
    owned = db.get_treats(guild_id, target.id)
    owned_names = [f"{name} (x{owned[key]})" if owned[key] > 1 else name for key, name in TREATS if key in owned]
    missing_names = [name for key, name in TREATS if key not in owned]
    embed = discord.Embed(title=f"{target.display_name}'s Inventory", description=f"**Treats Owned -**\n{treat_grid(owned_names)}\n\n**Treats Missing -**\n{treat_grid(missing_names)}\n\n**{len(owned)}/{len(TREAT_KEYS)}**\nKarma {db.get_karma(guild_id, target.id)}", colour=CREAM)
    await interaction.response.send_message(embed=embed, view=InventoryView())

class KnockView(discord.ui.View):
    def __init__(self, knocker: discord.Member, target: discord.Member):
        super().__init__(timeout=KNOCK_TIMEOUT)
        self.knocker = knocker; self.target = target; self.message = None; self.finished = False
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.target.id: return True
        await interaction.response.send_message("This door isn't yours to open!", ephemeral=True)
        return False
    def finish(self):
        self.finished = True; pending_knocks.discard(self.target.id); self.stop()
    async def show_result(self, text: str):
        try: await self.message.edit(content=None, embed=discord.Embed(title="Knock knock!", description=text, colour=PINK), view=None)
        except: pass
    @discord.ui.button(label="Treat!", style=discord.ButtonStyle.success)
    async def treat(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished: return await interaction.response.send_message("Too late.", ephemeral=True)
        self.finish()
        await interaction.response.defer()
        guild = interaction.guild
        result = resolve_treat(guild.id, self.knocker.id, self.target.id)
        knocker, target = self.knocker.mention, self.target.mention
        if result["treat"] is None: return await self.show_result(f"{target} chose **Treat**, but has no treats to give yet. The thought counts!")
        name = treat_name(result["treat"])
        if result["is_new"]: text = f"{target} chose **Treat** and gave {knocker} a brand new treat: **{name}**! (+{KARMA_TREAT} karma)"
        else: text = f"{target} chose **Treat** and gave {knocker} another **{name}**! Now they have {result['count']}. (+{KARMA_TREAT} karma)"
        if result["lmao"] and await grant_role_by_name(self.knocker, LMAO_HORNET_ROLE, "Lucky Treat drop"):
            db.set_setting("lmao_hornet_given", "1")
            text += f"\nA tiny hornet flew out of the bag... {knocker} got the limited **{LMAO_HORNET_ROLE}** role!"
        if await check_hornet_hallow(guild, self.knocker): text += f"\n{knocker} collected every treat and became a **{HORNET_HALLOW_ROLE}**!"
        await self.show_result(text)
    @discord.ui.button(label="Trick!", style=discord.ButtonStyle.danger)
    async def trick(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished: return await interaction.response.send_message("Too late.", ephemeral=True)
        now = time.time()
        left = TRICK_COOLDOWN - (now - trick_last.get(self.target.id, 0))
        if left > 0: return await interaction.response.send_message(f"Your tricks need a rest! You can trick again in {int(left // 60)}m {int(left % 60)}s. You can still choose Treat.", ephemeral=True)
        trick_last[self.target.id] = now
        self.finish()
        await interaction.response.defer()
        guild = interaction.guild
        result = resolve_trick(guild.id, self.knocker.id, self.target.id, random.random())
        knocker, target = self.knocker.mention, self.target.mention
        if not result["success"]:
            return await self.show_result(f"{target} chose **Trick**... but {knocker} wasn't scared at all! The trick failed ~w~ ({KARMA_TRICK_FAIL} karma)")
        treat_text = f"**{treat_name(result['treat'])}**" if result["treat"] else None
        karma_text = f"({result['karma']} karma)"
        if result["kind"] == "xp" and result["amount"]:
            text = f"{target} just stole {TRICK_XP_PERCENT}% of {knocker}'s XP"
            text += f" and their {treat_text} treat!! How evil!! {karma_text}" if treat_text else "!! How evil!! " + karma_text
        elif result["kind"] == "currency" and result["amount"]:
            text = f"{target} stole {fmt_num(result['amount'])} {result['currency']} from {knocker}"
            text += f" with their {treat_text} treat!! {karma_text}" if treat_text else "!! " + karma_text
        elif treat_text: text = f"{target} chose **Trick** and scared {knocker}! They only found their {treat_text} treat, and took it! {karma_text}"
        else: text = f"{target} chose **Trick** and scared {knocker}, but there was nothing to steal! {karma_text}"
        if await check_hornet_hallow(guild, self.target): text += f"\n{target} collected every treat and became a **{HORNET_HALLOW_ROLE}**!"
        await self.show_result(text)
        if result["ghost"] and await grant_role_by_name(self.knocker, GHOSTLY_ROLE, "Scared by a cat ghost"):
            try: await interaction.followup.send(content=knocker, embed=discord.Embed(description=f"WOAHH.. A GHOST!! You were scared by a cat ghost!... You got.. a special role. (/profile)", colour=CREAM), allowed_mentions=discord.AllowedMentions(users=[self.knocker]))
            except: pass
    async def on_timeout(self):
        if self.finished: return
        self.finish()
        try: await self.message.edit(content=None, embed=discord.Embed(title="Knock knock!", description=f"Nobody opened the door... {self.target.mention} didn't answer {self.knocker.mention}.", colour=PINK), view=None)
        except: pass

@client.tree.command(name="knock-knock", description="Knock on someone's door: will they give you a Treat or play a Trick?")
@app_commands.guild_only()
async def knock_knock(interaction: discord.Interaction, user: discord.Member):
    knocker = interaction.user
    guild_id = interaction.guild.id
    if not tot_is_active(): return await interaction.response.send_message("The Trick or Treat event isn't running right now.", ephemeral=True)
    if user.id == knocker.id: return await interaction.response.send_message("You can't knock on your own door!", ephemeral=True)
    if user.bot: return await interaction.response.send_message("Bots don't open doors, pick a person!", ephemeral=True)
    now = time.time()
    left = KNOCK_COOLDOWN - (now - knock_last.get(knocker.id, 0))
    if left > 0: return await interaction.response.send_message(f"Your knuckles need a rest! Try again in {int(left // 60)}m {int(left % 60) + 1}s.", ephemeral=True)
    left = KNOCK_PAIR_COOLDOWN - (now - knock_pair_last.get((knocker.id, user.id), 0))
    if left > 0: return await interaction.response.send_message(f"You already knocked on that door. Try again in {int(left // 60) + 1} minutes.", ephemeral=True)
    if user.id in pending_knocks: return await interaction.response.send_message("That door already has a visitor, wait a moment!", ephemeral=True)
    ensure_treat_starter(guild_id, knocker.id)
    ensure_treat_starter(guild_id, user.id)
    knock_last[knocker.id] = now
    knock_pair_last[(knocker.id, user.id)] = now
    pending_knocks.add(user.id)
    view = KnockView(knocker, user)
    embed = discord.Embed(title="Knock knock!", description=f"{knocker.mention} knocks on {user.mention}'s door...\n\n**Trick or treat?**", colour=PINK)
    await interaction.response.send_message(content=user.mention, embed=embed, view=view, allowed_mentions=discord.AllowedMentions(users=[user]))
    view.message = await interaction.original_response()

if not TOKEN: raise SystemExit("DISCORD_TOKEN environment variable is not set")
client.run(TOKEN)
