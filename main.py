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
GENERAL_CHANNEL_ID = int(os.getenv("GENERAL_CHANNEL_ID", "1564791988010881034"))

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
XP_COOLDOWN_RANGE = (10, 25)

FATIGUE_STEPS = [(50, 1.0), (80, 0.7), (110, 0.4), (10**9, 0.15)]

SINGLE_WORD_MULT = 0.5
SHORT_MULT = 0.8
LONG_MULT = 1.2

COMPANY_WINDOW = 15 * 60
SOLO_MULTIPLIER = 0.6
REPLY_BONUS = 1.15

MECH_MESSAGES = 8
MECH_TOLERANCE = 1.5
MECH_MIN_GAP = 3.0
SUSPICIOUS_SECONDS = 30 * 60
SUSPICIOUS_MULTIPLIER = 0.2

VOICE_XP_PER_MINUTE = 5

XP_CHANNELS: list[int] = []
XP_BOOSTS_STACK = False

MIN_LETTERS = 2
MIN_UNIQUE_LETTERS = 2
SPAM_HISTORY = 5
SIMILARITY_LIMIT = 0.85

MESSAGES_PER_MUPS = 25
MUPS_REWARD = 15
MUPS_ELIGIBLE_PER_HOUR = 60
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
DAILY_BASE_MUPS = 20
DAILY_BASE_VUPS = 5
DAILY_MUPS_STEP = 2
DAILY_VUPS_STEP = 1
DAILY_BASE_XP = 100
DAILY_XP_STEP = 10

WEEK_SECONDS = 7 * 24 * 3600


# ======================================================================
#  Database
# ======================================================================
class Database:
    def __init__(self, path: str):
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
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

        if "xp_buff_value" not in columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN xp_buff_value REAL NOT NULL DEFAULT 1.0")
        if "ghost_shields" not in columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN ghost_shields INTEGER NOT NULL DEFAULT 0")

        self.conn.execute("CREATE TABLE IF NOT EXISTS voices (channel_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, owner_id INTEGER NOT NULL, last_active INTEGER NOT NULL)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS treats (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, treat TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (guild_id, user_id, treat))")
        self.conn.execute("CREATE TABLE IF NOT EXISTS user_flags (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, key TEXT NOT NULL, value REAL NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, user_id, key))")
        self.conn.execute("CREATE TABLE IF NOT EXISTS mutes (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, until INTEGER NOT NULL, role_ids TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', moderator_id INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, user_id))")
        self.conn.execute("CREATE TABLE IF NOT EXISTS reminders (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, kind TEXT NOT NULL, due INTEGER NOT NULL, PRIMARY KEY (guild_id, user_id, kind))")
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
        self.conn.execute(
            "UPDATE users SET messages = messages + 1, pings = pings + ?, xp = xp + ?, xp_messages = ?, mups = ROUND(mups + ?, 2) WHERE guild_id = ? AND user_id = ?",
            (pings, xp_gain, new_xp_messages, mups_gain, guild_id, user_id)
        )
        self.conn.commit()
        return old_xp, old_xp + xp_gain, mups_gain

    def add_xp(self, guild_id: int, user_id: int, amount: int):
        self._ensure(guild_id, user_id)
        old_xp = self.conn.execute("SELECT xp FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()[0]
        new_xp = max(0, old_xp + amount)
        self.conn.execute("UPDATE users SET xp = ? WHERE guild_id = ? AND user_id = ?", (new_xp, guild_id, user_id))
        self.conn.commit()
        return old_xp, new_xp

    def add_voice(self, guild_id: int, user_id: int, seconds: int, booster_mult: float = 1.0):
        self._ensure(guild_id, user_id)
        old_seconds = self.conn.execute("SELECT voice_seconds FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()[0]
        new_seconds = old_seconds + seconds
        global_curr = float(self.get_setting("global_curr_mult", "1.0"))
        vups_gain = round((new_seconds // VOICE_SECONDS_PER_VUPS - old_seconds // VOICE_SECONDS_PER_VUPS) * VUPS_REWARD * booster_mult * global_curr, 2)
        self.conn.execute("UPDATE users SET voice_seconds = ?, vups = ROUND(vups + ?, 2) WHERE guild_id = ? AND user_id = ?", (new_seconds, vups_gain, guild_id, user_id))
        self.conn.commit()
        return vups_gain

    def get(self, guild_id: int, user_id: int):
        row = self.conn.execute(
            "SELECT xp, messages, pings, voice_seconds, mups, vups, cookies_received, cookies_given, last_cookie_given, tot_cooldown, xp_buff_end, xp_buff_value, ghost_shields FROM users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        ).fetchone()
        if row is None:
            return {"xp": 0, "messages": 0, "pings": 0, "voice_seconds": 0, "mups": 0, "vups": 0,
                    "cookies_received": 0, "cookies_given": 0, "last_cookie_given": 0,
                    "tot_cooldown": 0, "xp_buff_end": 0, "xp_buff_value": 1.0, "ghost_shields": 0}
        return {"xp": row[0], "messages": row[1], "pings": row[2], "voice_seconds": row[3],
                "mups": row[4], "vups": row[5], "cookies_received": row[6], "cookies_given": row[7],
                "last_cookie_given": row[8], "tot_cooldown": row[9], "xp_buff_end": row[10],
                "xp_buff_value": row[11], "ghost_shields": row[12]}

    def rank(self, guild_id: int, xp: int) -> int:
        return self.conn.execute("SELECT COUNT(*) + 1 FROM users WHERE guild_id = ? AND xp > ?", (guild_id, xp)).fetchone()[0]

    def top(self, guild_id: int, column: str, limit: int):
        return self.conn.execute(f"SELECT user_id, xp, messages, voice_seconds FROM users WHERE guild_id = ? AND {column} > 0 ORDER BY {column} DESC LIMIT ?", (guild_id, limit)).fetchall()

    def top_currency(self, guild_id: int, limit: int):
        return self.conn.execute("SELECT user_id, mups FROM users WHERE guild_id = ? AND mups > 0 ORDER BY mups DESC LIMIT ?", (guild_id, limit)).fetchall()

    def top_cookies(self, guild_id: int, limit: int):
        return self.conn.execute("SELECT user_id, cookies_received FROM users WHERE guild_id = ? AND cookies_received > 0 ORDER BY cookies_received DESC LIMIT ?", (guild_id, limit)).fetchall()

    def spend(self, guild_id: int, user_id: int, mups: float, vups: float) -> bool:
        self._ensure(guild_id, user_id)
        cursor = self.conn.execute(
            "UPDATE users SET mups = ROUND(mups - ?, 2), vups = ROUND(vups - ?, 2) WHERE guild_id = ? AND user_id = ? AND mups >= ? AND vups >= ?",
            (mups, vups, guild_id, user_id, mups, vups)
        )
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
        self._ensure(guild_id, giver_id)
        self._ensure(guild_id, receiver_id)
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
        row = self.conn.execute("SELECT ghost_shields FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
        if row and row[0] > 0:
            self.conn.execute("UPDATE users SET ghost_shields = ghost_shields - 1 WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            self.conn.commit()
            return True
        return False

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

    def treat_totals(self, guild_id: int):
        return self.conn.execute("SELECT user_id, COUNT(*) FROM treats WHERE guild_id = ? AND count > 0 GROUP BY user_id", (guild_id,)).fetchall()

    def treat_owners(self, guild_id: int, treat: str) -> list[int]:
        return [row[0] for row in self.conn.execute("SELECT user_id FROM treats WHERE guild_id = ? AND treat = ? AND count > 0", (guild_id, treat)).fetchall()]

    def treat_holders(self, guild_id: int, treat: str):
        return self.conn.execute("SELECT user_id, count FROM treats WHERE guild_id = ? AND treat = ? AND count > 0 ORDER BY count DESC", (guild_id, treat)).fetchall()

    def treat_stats(self, guild_id: int, limit: int):
        return self.conn.execute("SELECT user_id, COUNT(*), SUM(count) FROM treats WHERE guild_id = ? AND count > 0 GROUP BY user_id ORDER BY COUNT(*) DESC, SUM(count) DESC LIMIT ?", (guild_id, limit)).fetchall()

    def tot_has_starter(self, guild_id: int, user_id: int) -> bool:
        row = self.conn.execute("SELECT tot_starter FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
        return bool(row[0]) if row else False

    def tot_give_starter(self, guild_id: int, user_id: int, treat: str):
        self._ensure(guild_id, user_id)
        self.add_treat(guild_id, user_id, treat)
        self.conn.execute("UPDATE users SET tot_starter = 1 WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        self.conn.commit()

    def reset_karma(self, guild_id: int):
        self.conn.execute("UPDATE users SET karma = 0 WHERE guild_id = ?", (guild_id,))
        self.conn.commit()

    def add_karma(self, guild_id: int, user_id: int, amount: int):
        self._ensure(guild_id, user_id)
        self.conn.execute("UPDATE users SET karma = karma + ? WHERE guild_id = ? AND user_id = ?", (amount, guild_id, user_id))
        self.conn.commit()

    def get_karma(self, guild_id: int, user_id: int) -> int:
        row = self.conn.execute("SELECT karma FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
        return row[0] if row else 0

    def top_karma(self, guild_id: int, limit: int):
        return self.conn.execute("SELECT user_id, karma FROM users WHERE guild_id = ? AND karma > 0 ORDER BY karma DESC LIMIT ?", (guild_id, limit)).fetchall()

    def steal_currency(self, guild_id: int, victim_id: int, thief_id: int, amount: float, column: str) -> float:
        self._ensure(guild_id, victim_id)
        self._ensure(guild_id, thief_id)
        have = self.conn.execute(f"SELECT {column} FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, victim_id)).fetchone()[0]
        taken = round(max(0, min(amount, have)), 2)
        if taken:
            self.conn.execute(f"UPDATE users SET {column} = ROUND({column} - ?, 2) WHERE guild_id = ? AND user_id = ?", (taken, guild_id, victim_id))
            self.conn.execute(f"UPDATE users SET {column} = ROUND({column} + ?, 2) WHERE guild_id = ? AND user_id = ?", (taken, guild_id, thief_id))
            self.conn.commit()
        return taken

    # Mutes & Reminders
    def add_mute(self, guild_id: int, user_id: int, until: int, role_ids: str, reason: str, moderator_id: int):
        self.conn.execute("INSERT OR REPLACE INTO mutes (guild_id, user_id, until, role_ids, reason, moderator_id) VALUES (?, ?, ?, ?, ?, ?)", (guild_id, user_id, until, role_ids, reason, moderator_id))
        self.conn.commit()

    def get_mute(self, guild_id: int, user_id: int):
        return self.conn.execute("SELECT until, role_ids, reason, moderator_id FROM mutes WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()

    def list_mutes(self, guild_id: int):
        return self.conn.execute("SELECT user_id, until FROM mutes WHERE guild_id = ?", (guild_id,)).fetchall()

    def remove_mute(self, guild_id: int, user_id: int):
        self.conn.execute("DELETE FROM mutes WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        self.conn.commit()

    def set_reminder(self, guild_id: int, user_id: int, kind: str, due: int):
        self.conn.execute("INSERT OR REPLACE INTO reminders (guild_id, user_id, kind, due) VALUES (?, ?, ?, ?)", (guild_id, user_id, kind, due))
        self.conn.commit()

    def due_reminders(self, guild_id: int, now: int):
        return self.conn.execute("SELECT user_id, kind FROM reminders WHERE guild_id = ? AND due <= ?", (guild_id, now)).fetchall()

    def remove_reminder(self, guild_id: int, user_id: int, kind: str):
        self.conn.execute("DELETE FROM reminders WHERE guild_id = ? AND user_id = ? AND kind = ?", (guild_id, user_id, kind))
        self.conn.commit()

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


# ======================================================================
#  Core helpers
# ======================================================================
def fmt_num(value) -> str:
    value = round(float(value), 2)
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def level_from_xp(xp: int) -> tuple[int, int, int]:
    """Returns (level, xp_into_current_level, xp_needed_for_next_level)."""
    level = 0
    accumulated = 0
    while True:
        needed = 100 + level * 50
        if accumulated + needed > xp:
            return level, xp - accumulated, needed
        accumulated += needed
        level += 1


def xp_for_level(level: int) -> int:
    """Total XP required to reach a given level from 0."""
    return sum(100 + i * 50 for i in range(level))


def fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"


def parse_duration(s: str) -> int | None:
    """Parse '10m', '2h30m', '1d' etc. into seconds."""
    total = 0
    found = re.findall(r"(\d+)\s*([smhd])", s.strip().lower())
    if not found:
        try:
            return int(s) * 60
        except ValueError:
            return None
    for n, unit in found:
        total += int(n) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return total or None


# ======================================================================
#  Role helpers
# ======================================================================
def _norm_role(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def find_role(guild: discord.Guild, name: str) -> discord.Role | None:
    lowered = name.lower()
    for role in guild.roles:
        if role.name.lower() == lowered:
            return role
    for role in guild.roles:
        if role.name.lower().startswith(lowered):
            return role
    wanted = _norm_role(name)
    if wanted:
        for role in guild.roles:
            if _norm_role(role.name) == wanted:
                return role
    return None


def level_role_set(guild: discord.Guild) -> set:
    found = set()
    for _, role_name in LEVEL_ROLES:
        role = find_role(guild, role_name)
        if role:
            found.add(role)
    return found


def find_shop_role(guild: discord.Guild, name: str) -> discord.Role | None:
    excluded = level_role_set(guild)
    candidates = [r for r in guild.roles if r not in excluded]
    lowered = name.lower()
    for role in candidates:
        if role.name.lower() == lowered:
            return role
    for role in candidates:
        if role.name.lower().startswith(lowered):
            return role
    wanted = _norm_role(name)
    if wanted:
        for role in candidates:
            if _norm_role(role.name) == wanted:
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
            pass
    return granted


async def give_xp(member: discord.Member, amount: int):
    old_xp, new_xp = db.add_xp(member.guild.id, member.id, amount)
    new_level = level_from_xp(new_xp)[0]
    if new_level > level_from_xp(old_xp)[0]:
        await sync_level_roles(member, new_level)


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


def is_moderator(member: discord.Member) -> bool:
    perms = member.guild_permissions
    return member.id == member.guild.owner_id or perms.administrator or perms.moderate_members


def is_server_admin(interaction: discord.Interaction) -> bool:
    return is_moderator(interaction.user)


def is_blocked_name(name: str) -> bool:
    name_lower = name.lower()
    return any(word.lower() in name_lower for word in BLOCKED_WORDS)


def gumdrop_refund(guild_id: int, user_id: int, mups: float, vups: float) -> str:
    """If user has gloomy_gumdrop, refund 10% of spent currency."""
    treats = db.get_treats(guild_id, user_id)
    if "gloomy_gumdrop" not in treats:
        return ""
    refund_m = round(mups * 0.10, 2)
    refund_v = round(vups * 0.10, 2)
    if refund_m <= 0 and refund_v <= 0:
        return ""
    db.add_currency(guild_id, user_id, refund_m, refund_v)
    parts = []
    if refund_m:
        parts.append(f"{fmt_num(refund_m)} Mups")
    if refund_v:
        parts.append(f"{fmt_num(refund_v)} Vups")
    return f"🍬 Gloomy Gumdrop refunded {' & '.join(parts)}!"


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
    },
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


def treat_name(key: str) -> str:
    return TREAT_NAMES.get(key, key)


def tot_is_active() -> bool:
    return db.get_setting("tot_active", "0") == "1"


def ensure_treat_starter(guild_id: int, user_id: int):
    if not tot_is_active() or db.tot_has_starter(guild_id, user_id):
        return
    db.tot_give_starter(guild_id, user_id, random.choice(TREAT_KEYS))


def is_night() -> bool:
    hour = (datetime.datetime.now(datetime.timezone.utc).hour + NIGHT_UTC_OFFSET) % 24
    return hour >= 22 or hour < 6


def treat_currency_mult(treats: dict) -> float:
    mult = 1.0
    if "cursed_candy_corn" in treats:
        mult *= 1.03
    if "cat_paw_fudge" in treats and random.random() < 0.10:
        mult *= 2
    return mult


def get_active_shop_categories():
    active = {}
    now = time.time()
    for key, data in SHOP_CATEGORIES.items():
        if "expiry" in data and now > data["expiry"]:
            continue
        active[key] = data
    return active


def resolve_treat(guild_id: int, knocker_id: int, target_id: int) -> dict:
    mine = db.get_treats(guild_id, target_id)
    if not mine:
        return {"treat": None, "is_new": False, "count": 0, "lmao": False}
    theirs = db.get_treats(guild_id, knocker_id)
    repeat_pool = [key for key in mine if key in theirs]
    new_pool = [key for key in mine if key not in theirs]

    treat_luck = db.get_flag(guild_id, knocker_id, "treat_luck_end") > time.time()
    repeat_chance = 0.40 if treat_luck else TREAT_REPEAT_CHANCE

    if repeat_pool and (not new_pool or random.random() < repeat_chance):
        key = random.choice(repeat_pool)
    else:
        key = random.choice(new_pool)

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
    if "ghost_marshmallow" in db.get_treats(guild_id, knocker_id):
        chance -= GHOST_MARSHMALLOW_REDUCTION
    if db.get_flag(guild_id, target_id, "thief_brew_end") > time.time():
        chance = 0.8

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
        if taken:
            loss = 1 + int(taken) // 5

    if result["treat"]:
        loss += 1
    if loss == 0:
        loss = 1
    result["karma"] = -loss
    db.add_karma(guild_id, target_id, -loss)
    result["ghost"] = random.random() < GHOST_CHANCE
    return result


async def check_hornet_hallow(guild: discord.Guild, member: discord.Member) -> bool:
    if len(db.get_treats(guild.id, member.id)) < len(TREAT_KEYS):
        return False
    role = await get_role(guild, HORNET_HALLOW_ROLE)
    if role is None or role in member.roles:
        return False
    try:
        await member.add_roles(role, reason="Collected every treat")
    except (discord.Forbidden, discord.HTTPException):
        return False
    return True


async def grant_role_by_name(member: discord.Member, role_name: str, reason: str) -> bool:
    role = await get_role(member.guild, role_name)
    if role is None or role in member.roles:
        return False
    try:
        await member.add_roles(role, reason=reason)
    except (discord.Forbidden, discord.HTTPException):
        return False
    return True


# ======================================================================
#  In-memory XP tracking
# ======================================================================
_xp_cooldowns: dict[int, float] = {}
_recent_msgs: dict[int, deque] = {}
_channel_activity: dict[int, deque] = {}
_daily_xp_counts: dict[tuple[int, int], int] = {}
_today_date: str = ""


def _get_daily_count(guild_id: int, user_id: int) -> int:
    global _today_date, _daily_xp_counts
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    if today != _today_date:
        _daily_xp_counts.clear()
        _today_date = today
    return _daily_xp_counts.get((guild_id, user_id), 0)


def _inc_daily_count(guild_id: int, user_id: int):
    _get_daily_count(guild_id, user_id)  # ensure date check
    key = (guild_id, user_id)
    _daily_xp_counts[key] = _daily_xp_counts.get(key, 0) + 1


def calc_xp_gain(message: discord.Message) -> tuple[int, bool]:
    """Returns (xp_amount, count_for_mups)."""
    now = time.time()
    uid = message.author.id
    gid = message.guild.id
    content = message.content.strip()

    if XP_CHANNELS and message.channel.id not in XP_CHANNELS:
        return 0, False

    letters = re.sub(r"[^a-zA-Zа-яА-ЯёЁ]", "", content)
    unique = set(letters.lower())
    if len(letters) < MIN_LETTERS or len(unique) < MIN_UNIQUE_LETTERS:
        return 0, False

    if now < _xp_cooldowns.get(uid, 0):
        return 0, False

    if uid not in _recent_msgs:
        _recent_msgs[uid] = deque(maxlen=MECH_MESSAGES)
    history = _recent_msgs[uid]

    for ts, prev in history:
        if SequenceMatcher(None, content.lower(), prev.lower()).ratio() > SIMILARITY_LIMIT:
            return 0, False

    suspicious = False
    if history:
        last_ts = history[-1][0]
        if now - last_ts > SUSPICIOUS_SECONDS:
            suspicious = True

    if len(history) >= MECH_MESSAGES:
        timestamps = [ts for ts, _ in history]
        if len(timestamps) >= 2:
            gaps = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
            avg_gap = sum(gaps) / len(gaps)
            if 0 < avg_gap < MECH_MIN_GAP:
                max_dev = max(abs(g - avg_gap) for g in gaps)
                if max_dev < avg_gap * (MECH_TOLERANCE - 1):
                    return 0, False

    history.append((now, content))

    xp = random.randint(XP_MIN, XP_MAX)

    words = content.split()
    if len(words) <= 1:
        xp = int(xp * SINGLE_WORD_MULT)
    elif len(words) <= 3:
        xp = int(xp * SHORT_MULT)
    elif len(words) >= 15:
        xp = int(xp * LONG_MULT)

    if message.reference:
        xp = int(xp * REPLY_BONUS)

    if message.channel.id not in _channel_activity:
        _channel_activity[message.channel.id] = deque()
    ch_act = _channel_activity[message.channel.id]
    cutoff = now - COMPANY_WINDOW
    while ch_act and ch_act[0][0] < cutoff:
        ch_act.popleft()
    recent_users = {u for _, u in ch_act}
    if len(recent_users) < 2 and uid not in recent_users:
        xp = int(xp * SOLO_MULTIPLIER)
    ch_act.append((now, uid))

    daily = _get_daily_count(gid, uid)
    fatigue = 1.0
    for threshold, mult in FATIGUE_STEPS:
        if daily < threshold:
            fatigue = mult
            break
    xp = int(xp * fatigue)

    if suspicious:
        xp = int(xp * SUSPICIOUS_MULTIPLIER)

    data = db.get(gid, uid)
    if data["xp_buff_end"] > now:
        xp = int(xp * data["xp_buff_value"])

    if xp <= 0:
        return 0, False

    _inc_daily_count(gid, uid)
    cooldown = random.randint(*XP_COOLDOWN_RANGE)
    _xp_cooldowns[uid] = now + cooldown
    return xp, True


# ======================================================================
#  Mystery Box & Treat Pot & Cauldron Systems
# ======================================================================
CAULDRON_RECIPES = {
    "treat_luck": {
        "name": "Treat luck!", "desc": "Buff on 2 hours, while it your luck on getting a new treat is 30% -> 60%",
        "cost": {"pumpkin_cookie": 2, "gloomy_gumdrop": 2}
    },
    "ghostly_role": {
        "name": "Ghostly Role", "desc": "Ghost that somehow became a server role.. Only 3 users can obtain this!",
        "cost": {"ghost_marshmallow": 3, "mummy_muffin": 2}
    },
    "strange_xp": {
        "name": "Strange Xp Boost!", "desc": "It's.. an xp boost, but not at all..?",
        "cost": {"gloomy_gumdrop": 1}
    },
    "witch_role": {
        "name": "Witch Role", "desc": "Plehh.. A real witch??",
        "cost": {"witch_hat_truffle": 4, "bat_wing_gummy": 2, "spooky_lollipop": 2}
    },
    "thief_brew": {
        "name": "Thief's Brew", "desc": "Your chance to trick someone 50% -> 80% for 6 hours",
        "cost": {"skeleton_biscuit": 2, "cauldron_cocoa": 1}
    },
    "ghost_shield": {
        "name": "Ghost Shield", "desc": "Get a ghostly shield for the next 3 tricks on you!",
        "cost": {"skeleton_biscuit": 2, "ghost_marshmallow": 1}
    },
}


class MysteryBoxView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

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
                    msg = f"There was \"{item['name']}\" congratulations {user.mention}!"
                except Exception:
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
                msg = f"{user.mention}, there was a **{treat_name(t)}** treat! Yay!!"
            else:
                db.add_currency(guild_id, user.id, 100, 0)
                msg = f"{user.mention}, you own all treats! You found 100 Mups instead! Yay!!"
        elif outcome == 6:
            msg = "BOMB... IT EXPLODES IN 5"
            await interaction.followup.send(msg)
            bomb_msg = await interaction.original_response()
            for i in range(4, 0, -1):
                await asyncio.sleep(1)
                try:
                    await bomb_msg.edit(content=f"BOMB... IT EXPLODES IN {i}")
                except Exception:
                    pass
            await asyncio.sleep(1)
            try:
                await interaction.channel.purge(limit=10)
            except Exception:
                pass
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
        try:
            amt = int(self.amount.value)
        except ValueError:
            return await interaction.response.send_message("Invalid amount!", ephemeral=True)
        if amt <= 0:
            return await interaction.response.send_message("Amount must be positive!", ephemeral=True)

        guild_id = interaction.guild.id
        user_id = interaction.user.id
        owned = db.get_treats(guild_id, user_id).get(self.treat_key, 0)

        if amt > owned:
            return await interaction.response.send_message(f"You only have {owned} of this treat!", ephemeral=True)

        db.remove_treat(guild_id, user_id, self.treat_key, amt)
        db.add_to_pot(guild_id, user_id, amt)

        await interaction.response.send_message(f"You inserted {amt}x {treat_name(self.treat_key)} into the Pot! Buffs lost, but wait for the reward!", ephemeral=True)
        await update_treat_pot_msg(interaction.guild)


class TreatPotSelect(discord.ui.Select):
    def __init__(self, treats: dict):
        options = [discord.SelectOption(label=f"{treat_name(k)} (x{v})", value=k) for k, v in treats.items()]
        super().__init__(placeholder="Pick a treat to insert", min_values=1, max_values=1, options=options[:25])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TreatPotModal(self.values[0]))


class TreatPotView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Insert a Treat!", style=discord.ButtonStyle.success, custom_id="treat_pot_insert")
    async def insert(self, interaction: discord.Interaction, button: discord.ui.Button):
        treats = db.get_treats(interaction.guild.id, interaction.user.id)
        if not treats:
            return await interaction.response.send_message("You don't have any treats to insert!", ephemeral=True)
        view = discord.ui.View()
        view.add_item(TreatPotSelect(treats))
        await interaction.response.send_message("Pick a treat to insert, then pick an amount.", view=view, ephemeral=True)


async def update_treat_pot_msg(guild: discord.Guild):
    msg_id = db.get_setting("pot_msg_id", "")
    ch_id = db.get_setting("pot_ch_id", "")
    if not msg_id or not ch_id:
        return
    channel = guild.get_channel(int(ch_id))
    if not channel:
        return
    try:
        msg = await channel.fetch_message(int(msg_id))
        total_treats = db.get_pot_total(guild.id)
        investors = db.get_pot_investors(guild.id)

        xp_per = (total_treats * 15) // investors if investors else 0
        mups_per = (total_treats * 3) // investors if investors else 0
        vups_per = (total_treats * 2) // investors if investors else 0

        embed = discord.Embed(title="TREAT POT", description=(
            "Put your treats here to stack a buff for everyone! At the end of the event, all treats will turn into Xp and Mups with Vups! Then split to members who invested your treats!\n\n"
            f"**Treats:** {total_treats}\n"
            f"**Investors:** {investors}\n"
            f"**Xp Per user:** {xp_per}\n"
            f"**Mups Per user:** {mups_per}\n"
            f"**Vups Per user:** {vups_per}"
        ), colour=discord.Colour.orange())
        await msg.edit(embed=embed, view=TreatPotView())
    except Exception:
        pass


class CauldronCombineView(discord.ui.View):
    def __init__(self, r_key: str):
        super().__init__(timeout=None)
        self.r_key = r_key

    @discord.ui.button(label="Combine!", style=discord.ButtonStyle.success)
    async def combine(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        treats = db.get_treats(guild_id, user_id)

        r1 = db.get_setting("cauld_r1", "")
        r2 = db.get_setting("cauld_r2", "")
        rt = db.get_setting("cauld_rt", "")
        rt_c = db.get_setting("cauld_rt_c", "")

        cost = {}
        is_random_treat = False

        if self.r_key == "random_treat":
            cost = {rt_c: 2}
            is_random_treat = True
        elif self.r_key in CAULDRON_RECIPES:
            cost = CAULDRON_RECIPES[self.r_key]["cost"]
        else:
            return await interaction.response.send_message("Recipe expired!", ephemeral=True)

        for k, v in cost.items():
            if treats.get(k, 0) < v:
                return await interaction.response.send_message(f"You don't have enough {treat_name(k)}!", ephemeral=True)

        for k, v in cost.items():
            db.remove_treat(guild_id, user_id, k, v)

        msg = ""
        now = int(time.time())
        if is_random_treat:
            db.add_treat(guild_id, user_id, rt)
            msg = f"You successfully brewed a **{treat_name(rt)}**!"
        elif self.r_key == "treat_luck":
            db.set_flag(guild_id, user_id, "treat_luck_end", now + 2 * 3600)
            msg = "You brewed Treat Luck! New treat chance is 60% for 2 hours."
        elif self.r_key == "strange_xp":
            mult = round(random.uniform(0.5, 3.0), 2)
            db.set_xp_buff(guild_id, user_id, now + 3600, mult)
            msg = f"You brewed Strange XP Boost! Your XP multiplier is now {mult}x for 1 hour."
        elif self.r_key == "ghostly_role":
            role = await get_role(interaction.guild, GHOSTLY_ROLE)
            if role and len(role.members) >= 3:
                msg = "The Ghostly Role is out of stock! Max 3 users. (Treats consumed anyway, magic is unstable!)"
            elif role:
                await interaction.user.add_roles(role, reason="Cauldron")
                msg = "You obtained the Ghostly Role!"
            else:
                msg = "Role missing."
        elif self.r_key == "witch_role":
            role = await get_role(interaction.guild, WITCH_ROLE)
            if role:
                await interaction.user.add_roles(role, reason="Cauldron")
                msg = "You obtained the Witch Role!"
            else:
                msg = "Role missing."
        elif self.r_key == "thief_brew":
            db.set_flag(guild_id, user_id, "thief_brew_end", now + 6 * 3600)
            msg = "You brewed Thief's Brew! Trick chance is 80% for 6 hours."
        elif self.r_key == "ghost_shield":
            db.set_ghost_shield(guild_id, user_id, 3)
            msg = "You brewed a Ghost Shield! Block next 3 tricks."

        await interaction.response.send_message(msg, ephemeral=True)


class CauldronSelect(discord.ui.Select):
    def __init__(self, r1: str, r2: str, rt: str):
        options = []
        if r1 in CAULDRON_RECIPES:
            options.append(discord.SelectOption(label=CAULDRON_RECIPES[r1]["name"], value=r1))
        if r2 in CAULDRON_RECIPES:
            options.append(discord.SelectOption(label=CAULDRON_RECIPES[r2]["name"], value=r2))
        options.append(discord.SelectOption(label="Random Treat", value="random_treat"))
        super().__init__(placeholder="Select a recipe", options=options)

    async def callback(self, interaction: discord.Interaction):
        r_key = self.values[0]
        rt = db.get_setting("cauld_rt", "")
        rt_c = db.get_setting("cauld_rt_c", "")

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
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Recipe", style=discord.ButtonStyle.primary, custom_id="cauldron_open")
    async def open(self, interaction: discord.Interaction, button: discord.ui.Button):
        r1 = db.get_setting("cauld_r1", "")
        r2 = db.get_setting("cauld_r2", "")
        rt = db.get_setting("cauld_rt", "")
        if not r1:
            return await interaction.response.send_message("Cauldron is empty!", ephemeral=True)
        view = discord.ui.View()
        view.add_item(CauldronSelect(r1, r2, rt))
        await interaction.response.send_message("What do you want to brew?", view=view, ephemeral=True)


async def update_cauldron_msg(guild: discord.Guild):
    msg_id = db.get_setting("cauld_msg_id", "")
    ch_id = db.get_setting("cauld_ch_id", "")
    if not msg_id or not ch_id:
        return
    channel = guild.get_channel(int(ch_id))
    if not channel:
        return

    r1 = db.get_setting("cauld_r1", "")
    r2 = db.get_setting("cauld_r2", "")
    rt = db.get_setting("cauld_rt", "")
    nxt = int(db.get_setting("cauld_next", "0"))
    mins = max(0, (nxt - int(time.time())) // 60)

    desc = f"Combine your treats for buffs or other treats!\n\nWhat can you get [Reset in {mins} Minutes]\n\n"
    if r1 in CAULDRON_RECIPES:
        desc += f"**{CAULDRON_RECIPES[r1]['name']}**\n{CAULDRON_RECIPES[r1]['desc']}\n\n"
    if r2 in CAULDRON_RECIPES:
        desc += f"**{CAULDRON_RECIPES[r2]['name']}**\n{CAULDRON_RECIPES[r2]['desc']}\n\n"
    desc += f"**{treat_name(rt)}**\nA very cool Treat to have!"

    try:
        msg = await channel.fetch_message(int(msg_id))
        await msg.edit(
            embed=discord.Embed(title="Witch's Cauldron", description=desc, colour=discord.Colour.purple()),
            view=CauldronOpenView()
        )
    except Exception:
        pass


# ======================================================================
#  Background Tasks
# ======================================================================
@tasks.loop(minutes=1)
async def voice_tracker():
    guild = client.get_guild(GUILD_ID)
    if not guild:
        return
    now = int(time.time())
    voice_channel_ids = {ch_id for ch_id, _ in db.list_voices(guild.id)}
    for channel in guild.voice_channels:
        members = [m for m in channel.members if not m.bot]
        if not members:
            continue
        for member in members:
            data = db.get(guild.id, member.id)
            buff = data["xp_buff_value"] if data["xp_buff_end"] > now else 1.0
            db.add_voice(guild.id, member.id, 60, buff)
            xp = int(VOICE_XP_PER_MINUTE * buff)
            old_xp, new_xp = db.add_xp(guild.id, member.id, xp)
            if level_from_xp(new_xp)[0] > level_from_xp(old_xp)[0]:
                await sync_level_roles(member, level_from_xp(new_xp)[0])
        if channel.id in voice_channel_ids:
            db.touch_voice(channel.id, now)


@voice_tracker.before_loop
async def before_voice_tracker():
    await client.wait_until_ready()


@tasks.loop(minutes=10)
async def voice_cleanup():
    guild = client.get_guild(GUILD_ID)
    if not guild:
        return
    now = int(time.time())
    for channel_id, last_active in db.list_voices(guild.id):
        channel = guild.get_channel(channel_id)
        if channel is None:
            db.remove_voice(channel_id)
            continue
        real_members = [m for m in channel.members if not m.bot]
        if not real_members and (now - last_active) > VOICE_IDLE_SECONDS:
            try:
                await channel.delete(reason="Idle voice channel cleanup")
            except (discord.Forbidden, discord.HTTPException):
                pass
            db.remove_voice(channel_id)


@voice_cleanup.before_loop
async def before_voice_cleanup():
    await client.wait_until_ready()


@tasks.loop(seconds=30)
async def mute_checker():
    guild = client.get_guild(GUILD_ID)
    if not guild:
        return
    now = int(time.time())
    for user_id, until in db.list_mutes(guild.id):
        if now < until:
            continue
        mute_data = db.get_mute(guild.id, user_id)
        if not mute_data:
            continue
        _, role_ids_str, _, _ = mute_data
        member = guild.get_member(user_id)
        if member:
            muted_role = find_role(guild, MUTED_ROLE_NAME)
            if muted_role and muted_role in member.roles:
                try:
                    await member.remove_roles(muted_role, reason="Unmuted: time expired")
                except (discord.Forbidden, discord.HTTPException):
                    pass
            if role_ids_str:
                for rid in role_ids_str.split(","):
                    rid = rid.strip()
                    if rid:
                        role = guild.get_role(int(rid))
                        if role:
                            try:
                                await member.add_roles(role, reason="Unmuted: restored roles")
                            except (discord.Forbidden, discord.HTTPException):
                                pass
        db.remove_mute(guild.id, user_id)


@mute_checker.before_loop
async def before_mute_checker():
    await client.wait_until_ready()


@tasks.loop(seconds=60)
async def reminder_loop():
    guild = client.get_guild(GUILD_ID)
    if not guild:
        return
    now = int(time.time())
    for user_id, kind in db.due_reminders(guild.id, now):
        member = guild.get_member(user_id)
        if member:
            try:
                await member.send(f"⏰ Reminder: **{kind}**")
            except (discord.Forbidden, discord.HTTPException):
                pass
        db.remove_reminder(guild.id, user_id, kind)


@reminder_loop.before_loop
async def before_reminder_loop():
    await client.wait_until_ready()


@tasks.loop(hours=168)
async def treat_weekly():
    guild = client.get_guild(GUILD_ID)
    if not guild:
        return
    db.reset_karma(guild.id)


@treat_weekly.before_loop
async def before_treat_weekly():
    await client.wait_until_ready()


@tasks.loop(minutes=10)
async def box_loop():
    if not GENERAL_CHANNEL_ID:
        return
    guild = client.get_guild(GUILD_ID)
    if not guild:
        return
    channel = guild.get_channel(GENERAL_CHANNEL_ID)
    if not channel:
        return
    await channel.send("Wowwie! Strange cat left mysterious box here!", view=MysteryBoxView())


@box_loop.before_loop
async def before_box():
    await client.wait_until_ready()


@tasks.loop(minutes=1)
async def cauldron_loop():
    guild = client.get_guild(GUILD_ID)
    if not guild:
        return
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
async def before_cauld():
    await client.wait_until_ready()


# ======================================================================
#  Role / Verify / Voice Views
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
                if default_used:
                    selected = False
                default_used = True
            options.append(discord.SelectOption(label=label, value=label, default=selected))
        super().__init__(placeholder=placeholder, min_values=0, max_values=len(options) if multiple else 1, options=options[:25], row=row)
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
            await interaction.response.send_message("I can't change those roles.", ephemeral=True)
            return
        for option in self.options:
            if option.value == "None":
                option.default = not wanted
            else:
                option.default = option.value in chosen
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
                if role:
                    group[label] = role
            if not any(group.values()):
                continue
            self.add_item(RoleGroupSelect(member, group, placeholder, multiple, row))


class EmojiSelect(discord.ui.Select):
    def __init__(self, member: discord.Member, guild: discord.Guild):
        self.all_emoji_roles = []
        options = [discord.SelectOption(label="None", value="None")]
        all_items = []
        for cat_data in SHOP_CATEGORIES.values():
            all_items.extend(cat_data["items"])
        all_items.extend(EVENT_NAME_EMOJIS)
        seen = set()
        default_used = False
        for item in all_items:
            role = find_shop_role(guild, item["role"])
            if role is None or role.id in seen:
                continue
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
            if to_remove:
                await member.remove_roles(*to_remove, reason="Changed name emoji")
            if to_add:
                await member.add_roles(*to_add, reason="Changed name emoji")
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission.", ephemeral=True)
            return
        for option in self.options:
            option.default = (option.value == val)
        await interaction.response.edit_message(content="Saved!", view=self.view)


class NameChangeModal(discord.ui.Modal, title="Change your name (100 Mups)"):
    new_name = discord.ui.TextInput(label="New Name", placeholder="Enter your new nickname...", max_length=32, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        member = interaction.user
        guild = interaction.guild
        data = db.get(guild.id, member.id)
        if data["mups"] < 100:
            return await interaction.response.send_message("Not enough Mups!", ephemeral=True)
        try:
            await member.edit(nick=self.new_name.value.strip(), reason="Bought name change")
        except discord.Forbidden:
            return await interaction.response.send_message("No permission.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.response.send_message("Failed.", ephemeral=True)
        db.spend(guild.id, member.id, 100, 0)
        text = f"Your name has been changed to **{self.new_name.value.strip()}** for 100 Mups!"
        back = gumdrop_refund(guild.id, member.id, 100, 0)
        if back:
            text += f"\n{back}"
        await interaction.response.send_message(text, ephemeral=True)


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
            if role:
                color_group[label] = role
        if len(color_group) > 1:
            self.add_item(RoleGroupSelect(member, color_group, "Edit your Name color!", False, 0))
        pronoun_group = {"None": None}
        for label, lookup in PRONOUN_ROLES:
            names = lookup if isinstance(lookup, (tuple, list)) else (lookup,)
            role = next((r for r in (find_role(guild, n) for n in names) if r), None)
            if role:
                pronoun_group[label] = role
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
            return await interaction.response.send_message("Not your profile!", ephemeral=True)
        try:
            view = EditProfilePanel(interaction.user)
            embed = discord.Embed(title="Edit Server Profile", description="Change your roles, emojis, and name here!", colour=CREAM)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message("Error.", ephemeral=True)


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
            if newcomer_role:
                await member.add_roles(newcomer_role, reason="Passed verification")
            if joined_role and joined_role in member.roles:
                await member.remove_roles(joined_role, reason="Passed verification")
        except discord.Forbidden:
            return await interaction.response.edit_message(content="No permissions.", view=None)
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
            return await interaction.response.send_message("You're already verified!", ephemeral=True)
        rules = find_channel_mention(guild, "rules")
        know = find_channel_mention(guild, "what-to-know")
        await interaction.response.send_message(
            f"Clicking on verify, you accept that you have read channels: {rules} and {know}.\nPress the button below to confirm.",
            view=ConfirmView(), ephemeral=True
        )


class NameLimitModal(discord.ui.Modal, title="Your voice chat"):
    name_input = discord.ui.TextInput(label="What's your voice name?", placeholder="Cozy corner", max_length=50, required=True)
    limit_input = discord.ui.TextInput(label="How much is limit for people in your voice?", placeholder=f"A number from 1 to {VOICE_MAX_LIMIT}", max_length=2, required=True)

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
            return await interaction.response.send_message("The name can't be empty.", ephemeral=True)
        try:
            limit = int(self.limit_input.value.strip())
        except ValueError:
            return await interaction.response.send_message(f"The limit must be a number from 1 to {VOICE_MAX_LIMIT}.", ephemeral=True)
        if not 1 <= limit <= VOICE_MAX_LIMIT:
            return await interaction.response.send_message(f"The limit must be a number from 1 to {VOICE_MAX_LIMIT}.", ephemeral=True)
        if is_blocked_name(name):
            return await interaction.response.send_message("Please choose a different name, that one isn't allowed.", ephemeral=True)
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
        if self.access == "public":
            access_text = "**Public**: all Newcomers and Silly Critters can join"
        else:
            access_text = f"**Private**: only you and {', '.join(m.mention for m in self.invited) or 'nobody picked yet'}"
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
            return await interaction.response.send_message("Please press **Set name and limit** first.", ephemeral=True)
        guild = interaction.guild
        existing_id = db.get_voice_by_owner(guild.id, self.owner.id)
        if existing_id:
            existing = guild.get_channel(existing_id)
            if existing:
                return await interaction.response.edit_message(content=f"You already have a voice chat: {existing.mention}", embed=None, view=None)
            db.remove_voice(existing_id)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True, move_members=True),
            self.owner: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, manage_channels=True, manage_permissions=True, move_members=True, mute_members=True),
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
                name=self.name, user_limit=self.limit, category=self.category,
                overwrites=overwrites, reason=f"Private voice created by {self.owner}"
            )
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.response.edit_message(content="I couldn't create the channel. Please tell a mod.", embed=None, view=None)
        db.add_voice_channel(guild.id, channel.id, self.owner.id, int(time.time()))
        self.stop()
        await interaction.response.edit_message(content=f"Your voice chat is ready: {channel.mention}", embed=None, view=None)
        if self.owner.voice is not None:
            try:
                await self.owner.move_to(channel)
            except (discord.Forbidden, discord.HTTPException):
                pass


class CreateVoiceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Voice", style=discord.ButtonStyle.primary, custom_id="create_voice_button")
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        guild = interaction.guild
        if not has_level_role(member):
            return await interaction.response.send_message("Private voice chats are for Silly Critters and up.", ephemeral=True)
        existing_id = db.get_voice_by_owner(guild.id, member.id)
        if existing_id:
            existing = guild.get_channel(existing_id)
            if existing:
                return await interaction.response.send_message(f"You already have a voice chat: {existing.mention}", ephemeral=True)
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
#  Knock / Trick-or-Treat View
# ======================================================================
class KnockView(discord.ui.View):
    def __init__(self, knocker: discord.Member, target: discord.Member, guild: discord.Guild):
        super().__init__(timeout=KNOCK_TIMEOUT)
        self.knocker = knocker
        self.target = target
        self.guild = guild
        self.done = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("This knock isn't for you!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Give Treat 🍬", style=discord.ButtonStyle.success)
    async def give_treat(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.done:
            return
        self.done = True
        self.stop()
        pending_knocks.discard(self.target.id)

        result = resolve_treat(self.guild.id, self.knocker.id, self.target.id)
        if result["treat"] is None:
            await interaction.response.edit_message(content="You opened the door but had no treats to give!", view=None)
            return

        t = treat_name(result["treat"])
        msg = f"🍬 **{self.target.display_name}** gave **{self.knocker.display_name}** a **{t}**!"
        if result["is_new"]:
            msg += " ✨ *New treat!*"
        if result["lmao"]:
            lmao_role = await get_role(self.guild, LMAO_HORNET_ROLE)
            if lmao_role:
                try:
                    await self.knocker.add_roles(lmao_role, reason="Lmao Hornet from treat")
                    db.set_setting("lmao_hornet_given", "1")
                    msg += f"\n🎉 **{self.knocker.display_name}** also got the **{LMAO_HORNET_ROLE}**!"
                except Exception:
                    pass

        if await check_hornet_hallow(self.guild, self.knocker):
            msg += f"\n🏆 **{self.knocker.display_name}** collected ALL treats and got **{HORNET_HALLOW_ROLE}**!"

        ensure_treat_starter(self.guild.id, self.target.id)
        await interaction.response.edit_message(content=msg, view=None)

    @discord.ui.button(label="Trick! 🎭", style=discord.ButtonStyle.danger)
    async def do_trick(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.done:
            return
        self.done = True
        self.stop()
        pending_knocks.discard(self.target.id)
        trick_last[self.target.id] = time.time()

        roll = random.random()
        result = resolve_trick(self.guild.id, self.knocker.id, self.target.id, roll)

        if result.get("shielded"):
            await interaction.response.edit_message(
                content=f"👻 **{self.knocker.display_name}**'s Ghost Shield blocked the trick!", view=None
            )
            return

        if not result["success"]:
            await interaction.response.edit_message(
                content=f"😈 **{self.target.display_name}** tried to trick **{self.knocker.display_name}** but FAILED!", view=None
            )
            return

        msg = f"😈 **{self.target.display_name}** successfully tricked **{self.knocker.display_name}**!\n"
        if result["kind"] == "xp":
            msg += f"Stole **{result['amount']} XP**!"
        elif result["kind"] == "currency":
            msg += f"Stole **{fmt_num(result['amount'])} {result['currency']}**!"
        if result.get("treat"):
            msg += f"\n🍬 Also stole a **{treat_name(result['treat'])}**!"
        if result.get("ghost"):
            db.set_ghost_shield(self.guild.id, self.knocker.id, 1)
            msg += f"\n👻 A ghost appeared! **{self.knocker.display_name}** got a Ghost Shield!"

        await interaction.response.edit_message(content=msg, view=None)

    async def on_timeout(self):
        pending_knocks.discard(self.target.id)


# ======================================================================
#  Bot Client
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
        reminder_loop.start()
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
#  Event Handlers
# ======================================================================
@client.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return
    joined_role = await get_role(member.guild, JOINED_ROLE_NAME)
    if joined_role:
        try:
            await member.add_roles(joined_role, reason="New member")
        except discord.Forbidden:
            pass
    if WELCOME_CHANNEL_ID:
        channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
        if channel:
            embed = discord.Embed(
                title=f"Welcome, {member.display_name}! 🎉",
                description=f"Hey {member.mention}! Welcome to **{member.guild.name}**!\nHead over to the verify channel to get access!",
                colour=PINK,
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    if message.guild.id != GUILD_ID:
        return

    xp_gain, count_for_mups = calc_xp_gain(message)
    pings = len(message.mentions)

    old_xp, new_xp, mups_gained = db.add_message(
        message.guild.id, message.author.id, pings, xp_gain,
        count_for_mups=count_for_mups
    )

    if xp_gain > 0:
        new_level = level_from_xp(new_xp)[0]
        old_level = level_from_xp(old_xp)[0]
        if new_level > old_level:
            granted = await sync_level_roles(message.author, new_level)
            role_str = f" You earned: {', '.join(r.mention for r in granted)}!" if granted else ""
            await message.channel.send(
                f"🎉 {message.author.mention} reached **Level {new_level}**!{role_str}",
                delete_after=15,
            )

    if tot_is_active():
        ensure_treat_starter(message.guild.id, message.author.id)


@client.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.bot:
        return
    # Clean up empty private voice channels immediately
    if before.channel and before.channel != after.channel:
        real_members = [m for m in before.channel.members if not m.bot]
        if not real_members:
            owned = db.get_voice_by_owner(member.guild.id, member.id)
            if owned == before.channel.id:
                try:
                    await before.channel.delete(reason="Owner left, channel empty")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                db.remove_voice(before.channel.id)


# ======================================================================
#  Admin Commands
# ======================================================================
@client.tree.command(name="spawn_box", description="Spawn a Mystery Box (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def spawn_box(interaction: discord.Interaction):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    await interaction.channel.send("Wowwie! Strange cat left mysterious box here!", view=MysteryBoxView())
    await interaction.response.send_message("Box spawned.", ephemeral=True)


@client.tree.command(name="setup_treat_pot", description="Setup Treat Pot (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_treat_pot(interaction: discord.Interaction):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    embed = discord.Embed(title="TREAT POT", description="Loading...", colour=discord.Colour.orange())
    await interaction.response.send_message("Pot setup.", ephemeral=True)
    msg = await interaction.channel.send(embed=embed, view=TreatPotView())
    db.set_setting("pot_msg_id", str(msg.id))
    db.set_setting("pot_ch_id", str(interaction.channel.id))
    await update_treat_pot_msg(interaction.guild)


@client.tree.command(name="treat_pot_end", description="End Treat Pot and distribute rewards (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def treat_pot_end(interaction: discord.Interaction):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    guild = interaction.guild
    total_treats = db.get_pot_total(guild.id)
    investors = db.get_all_investors(guild.id)

    if not investors:
        return await interaction.response.send_message("Pot is empty!", ephemeral=True)

    xp_per = (total_treats * 15) // len(investors)
    mups_per = (total_treats * 3) // len(investors)
    vups_per = (total_treats * 2) // len(investors)

    for uid in investors:
        db.add_xp(guild.id, uid, xp_per)
        db.add_currency(guild.id, uid, mups_per, vups_per)

    db.clear_pot(guild.id)

    embed = discord.Embed(title="TREAT POT EVENT ENDED", description=(
        f"**Treats invested:** {total_treats}\n"
        f"**Xp in Pot:** {total_treats * 15}\n"
        f"**Mups In Pot:** {total_treats * 3}\n"
        f"**Vups in Pot:** {total_treats * 2}\n\n"
        f"**You got:** {xp_per} Xp, {mups_per} Mups, {vups_per} Vups."
    ), colour=discord.Colour.orange())

    await interaction.channel.send("@here", embed=embed)
    await interaction.response.send_message("Pot ended and distributed!", ephemeral=True)


@client.tree.command(name="setup_cauldron", description="Setup Cauldron (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_cauldron(interaction: discord.Interaction):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    embed = discord.Embed(title="Witch's Cauldron", description="Loading...", colour=discord.Colour.purple())
    await interaction.response.send_message("Cauldron setup.", ephemeral=True)
    msg = await interaction.channel.send(embed=embed, view=CauldronOpenView())
    db.set_setting("cauld_msg_id", str(msg.id))
    db.set_setting("cauld_ch_id", str(interaction.channel.id))
    await update_cauldron_msg(interaction.guild)


@client.tree.command(name="setup_verify", description="Send verify button (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_verify(interaction: discord.Interaction):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    embed = discord.Embed(title="Verification", description="Click the button below to get access to the server!", colour=PINK)
    await interaction.channel.send(embed=embed, view=VerifyView())
    await interaction.response.send_message("Verify panel sent.", ephemeral=True)


@client.tree.command(name="setup_voices", description="Send create-voice button (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_voices(interaction: discord.Interaction):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    embed = discord.Embed(title="Private Voice Chats", description="Click the button to create your own private voice channel!", colour=CREAM)
    await interaction.channel.send(embed=embed, view=CreateVoiceView())
    await interaction.response.send_message("Voice panel sent.", ephemeral=True)


@client.tree.command(name="setup_roles", description="Send roles button (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_roles(interaction: discord.Interaction):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    embed = discord.Embed(title="Server Roles", description="Pick your pronouns, colours, and ping preferences!", colour=CREAM)
    await interaction.channel.send(embed=embed, view=GetRolesView())
    await interaction.response.send_message("Roles panel sent.", ephemeral=True)


@client.tree.command(name="give_xp", description="Give or take XP from a user (admins only)")
@app_commands.describe(member="Target member", amount="Amount of XP (negative to remove)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def give_xp_cmd(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    old_xp, new_xp = db.add_xp(interaction.guild.id, member.id, amount)
    new_level = level_from_xp(new_xp)[0]
    if new_level > level_from_xp(old_xp)[0]:
        await sync_level_roles(member, new_level)
    sign = "+" if amount >= 0 else ""
    await interaction.response.send_message(f"{sign}{fmt_num(amount)} XP → {member.mention} (now {fmt_num(new_xp)} XP, Level {new_level})", ephemeral=True)


@client.tree.command(name="give_currency", description="Give Mups/Vups to a user (admins only)")
@app_commands.describe(member="Target member", mups="Mups amount", vups="Vups amount")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def give_currency(interaction: discord.Interaction, member: discord.Member, mups: float = 0, vups: float = 0):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    db.add_currency(interaction.guild.id, member.id, mups, vups)
    await interaction.response.send_message(f"Gave {member.mention}: **{fmt_num(mups)} Mups** and **{fmt_num(vups)} Vups**.", ephemeral=True)


@client.tree.command(name="take_currency", description="Take Mups/Vups from a user (admins only)")
@app_commands.describe(member="Target member", mups="Mups amount", vups="Vups amount")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def take_currency_cmd(interaction: discord.Interaction, member: discord.Member, mups: float = 0, vups: float = 0):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    taken_m, taken_v = db.take_currency(interaction.guild.id, member.id, mups, vups)
    await interaction.response.send_message(f"Took **{fmt_num(taken_m)} Mups** and **{fmt_num(taken_v)} Vups** from {member.mention}.", ephemeral=True)


@client.tree.command(name="mute", description="Mute a member (mods only)")
@app_commands.describe(member="Member to mute", duration="Duration e.g. 10m, 2h, 1d", reason="Reason")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
async def mute_cmd(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str = "No reason given"):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("Mods only.", ephemeral=True)
    seconds = parse_duration(duration)
    if not seconds:
        return await interaction.response.send_message("Invalid duration. Use formats like `10m`, `2h`, `1d`.", ephemeral=True)

    guild = interaction.guild
    muted_role = await get_role(guild, MUTED_ROLE_NAME)
    if not muted_role:
        return await interaction.response.send_message("Muted role not found.", ephemeral=True)

    saved_roles = [r for r in member.roles if r != guild.default_role and r != muted_role]
    role_ids = ",".join(str(r.id) for r in saved_roles)

    try:
        await member.remove_roles(*saved_roles, reason=f"Muting: {reason}")
        await member.add_roles(muted_role, reason=f"Muted by {interaction.user}: {reason}")
    except discord.Forbidden:
        return await interaction.response.send_message("No permission to mute that member.", ephemeral=True)

    until = int(time.time()) + seconds
    db.add_mute(guild.id, member.id, until, role_ids, reason, interaction.user.id)

    await interaction.response.send_message(
        f"🔇 {member.mention} muted for **{fmt_duration(seconds)}**.\nReason: {reason}", ephemeral=False
    )

    if MOD_LOG_CHANNEL_ID:
        log_ch = guild.get_channel(MOD_LOG_CHANNEL_ID)
        if log_ch:
            embed = discord.Embed(title="Member Muted", colour=discord.Colour.red())
            embed.add_field(name="Member", value=member.mention)
            embed.add_field(name="Moderator", value=interaction.user.mention)
            embed.add_field(name="Duration", value=fmt_duration(seconds))
            embed.add_field(name="Reason", value=reason, inline=False)
            await log_ch.send(embed=embed)


@client.tree.command(name="unmute", description="Unmute a member (mods only)")
@app_commands.describe(member="Member to unmute")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
async def unmute_cmd(interaction: discord.Interaction, member: discord.Member):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("Mods only.", ephemeral=True)
    guild = interaction.guild
    mute_data = db.get_mute(guild.id, member.id)
    if not mute_data:
        return await interaction.response.send_message("That member is not muted.", ephemeral=True)

    _, role_ids_str, _, _ = mute_data
    muted_role = find_role(guild, MUTED_ROLE_NAME)
    if muted_role and muted_role in member.roles:
        try:
            await member.remove_roles(muted_role, reason=f"Unmuted by {interaction.user}")
        except discord.Forbidden:
            pass
    if role_ids_str:
        for rid in role_ids_str.split(","):
            rid = rid.strip()
            if rid:
                role = guild.get_role(int(rid))
                if role:
                    try:
                        await member.add_roles(role, reason="Unmuted: restored roles")
                    except discord.Forbidden:
                        pass
    db.remove_mute(guild.id, member.id)
    await interaction.response.send_message(f"🔊 {member.mention} has been unmuted.", ephemeral=False)


@client.tree.command(name="xp_buff", description="Give a user an XP buff (admins only)")
@app_commands.describe(member="Target", multiplier="XP multiplier e.g. 1.5 for 150%", hours="How many hours")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def xp_buff_cmd(interaction: discord.Interaction, member: discord.Member, multiplier: float, hours: int):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    end = int(time.time()) + hours * 3600
    db.set_xp_buff(interaction.guild.id, member.id, end, multiplier)
    await interaction.response.send_message(f"✨ {member.mention} now has **{multiplier}x XP** for **{hours}h**.", ephemeral=True)


@client.tree.command(name="global_mult", description="Set the global currency multiplier (admins only)")
@app_commands.describe(value="Multiplier value e.g. 1.5")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def global_mult_cmd(interaction: discord.Interaction, value: float):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    db.set_setting("global_curr_mult", str(value))
    await interaction.response.send_message(f"Global currency multiplier set to **{value}x**.", ephemeral=True)


@client.tree.command(name="tot_toggle", description="Toggle Trick-or-Treat event (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def tot_toggle(interaction: discord.Interaction):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    current = db.get_setting("tot_active", "0")
    new_val = "0" if current == "1" else "1"
    db.set_setting("tot_active", new_val)
    state = "**ACTIVE** 🎃" if new_val == "1" else "**INACTIVE**"
    await interaction.response.send_message(f"Trick-or-Treat is now {state}.", ephemeral=False)


@client.tree.command(name="announce", description="Send an announcement (admins only)")
@app_commands.describe(text="Announcement text")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def announce_cmd(interaction: discord.Interaction, text: str):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    await interaction.channel.send(text)
    await interaction.response.send_message("Sent!", ephemeral=True)


# ======================================================================
#  User Commands
# ======================================================================
@client.tree.command(name="rank", description="See your XP rank")
@app_commands.describe(member="Member to check (leave empty for yourself)")
@app_commands.guild_only()
async def rank_cmd(interaction: discord.Interaction, member: discord.Member | None = None):
    target = member or interaction.user
    data = db.get(interaction.guild.id, target.id)
    xp = data["xp"]
    level, into_lvl, needed = level_from_xp(xp)
    rank = db.rank(interaction.guild.id, xp)
    progress = int(into_lvl / needed * 20) if needed else 20
    bar = "█" * progress + "░" * (20 - progress)

    embed = discord.Embed(title=f"{target.display_name}'s Rank", colour=PINK)
    embed.add_field(name="Level", value=str(level), inline=True)
    embed.add_field(name="Rank", value=f"#{rank}", inline=True)
    embed.add_field(name="XP", value=f"{fmt_num(xp)}", inline=True)
    embed.add_field(name="Progress", value=f"`{bar}` {fmt_num(into_lvl)}/{fmt_num(needed)}", inline=False)
    embed.add_field(name="Messages", value=fmt_num(data["messages"]), inline=True)
    embed.add_field(name="Voice", value=fmt_duration(data["voice_seconds"]), inline=True)
    embed.add_field(name="Mups 🪙", value=fmt_num(data["mups"]), inline=True)
    embed.add_field(name="Vups 💎", value=fmt_num(data["vups"]), inline=True)
    embed.add_field(name="Cookies 🍪", value=fmt_num(data["cookies_received"]), inline=True)
    if data["ghost_shields"] > 0:
        embed.add_field(name="Ghost Shields 👻", value=str(data["ghost_shields"]), inline=True)
    embed.set_thumbnail(url=target.display_avatar.url)
    await interaction.response.send_message(embed=embed, view=ProfileView(target))


@client.tree.command(name="leaderboard", description="See top members")
@app_commands.describe(category="What to rank by")
@app_commands.choices(category=[
    app_commands.Choice(name="XP", value="xp"),
    app_commands.Choice(name="Messages", value="messages"),
    app_commands.Choice(name="Voice", value="voice_seconds"),
    app_commands.Choice(name="Mups", value="mups"),
    app_commands.Choice(name="Cookies", value="cookies"),
])
@app_commands.guild_only()
async def leaderboard_cmd(interaction: discord.Interaction, category: str = "xp"):
    guild = interaction.guild
    if category == "cookies":
        rows = db.top_cookies(guild.id, 10)
        title = "🍪 Cookie Leaderboard"
        lines = []
        for i, (uid, val) in enumerate(rows, 1):
            m = guild.get_member(uid)
            name = m.display_name if m else f"<@{uid}>"
            lines.append(f"**{i}.** {name} — {fmt_num(val)} 🍪")
    elif category == "mups":
        rows = db.top_currency(guild.id, 10)
        title = "🪙 Mups Leaderboard"
        lines = []
        for i, (uid, val) in enumerate(rows, 1):
            m = guild.get_member(uid)
            name = m.display_name if m else f"<@{uid}>"
            lines.append(f"**{i}.** {name} — {fmt_num(val)} 🪙")
    else:
        rows = db.top(guild.id, category, 10)
        labels = {"xp": ("✨ XP Leaderboard", lambda r: f"{fmt_num(r[1])} XP"),
                  "messages": ("💬 Message Leaderboard", lambda r: f"{fmt_num(r[2])} messages"),
                  "voice_seconds": ("🎙️ Voice Leaderboard", lambda r: fmt_duration(r[3]))}
        title, val_fn = labels[category]
        lines = []
        for i, row in enumerate(rows, 1):
            m = guild.get_member(row[0])
            name = m.display_name if m else f"<@{row[0]}>"
            lines.append(f"**{i}.** {name} — {val_fn(row)}")

    embed = discord.Embed(title=title, description="\n".join(lines) or "No data yet!", colour=PINK)
    await interaction.response.send_message(embed=embed)


@client.tree.command(name="shop", description="Browse the item shop")
@app_commands.guild_only()
async def shop_cmd(interaction: discord.Interaction):
    cats = get_active_shop_categories()
    embed = discord.Embed(title="🛍️ Shop", colour=CREAM)
    for cat_data in cats.values():
        flag = f" `{cat_data['flag']}`" if cat_data["flag"] else ""
        items_text = "\n".join(
            f"• **{item['name']}** — {item['mups']} Mups"
            + (f" + {item['vups']} Vups" if item["vups"] else "")
            + f"\n  *{item['description']}*"
            for item in cat_data["items"]
        )
        embed.add_field(name=f"{cat_data['label']}{flag}", value=items_text or "—", inline=False)
    embed.set_footer(text="Use /buy <item name> to purchase!")
    await interaction.response.send_message(embed=embed)


@client.tree.command(name="buy", description="Buy an item from the shop")
@app_commands.describe(item="Item name to buy")
@app_commands.guild_only()
async def buy_cmd(interaction: discord.Interaction, item: str):
    guild = interaction.guild
    user = interaction.user
    cats = get_active_shop_categories()

    found = None
    for cat_data in cats.values():
        for i in cat_data["items"]:
            if i["name"].lower() == item.lower() or i["key"].lower() == item.lower():
                found = i
                break
        if found:
            break

    if not found:
        return await interaction.response.send_message(f"Item **{item}** not found in shop.", ephemeral=True)

    role = find_shop_role(guild, found["role"])
    if role and role in user.roles:
        return await interaction.response.send_message("You already own this item!", ephemeral=True)

    data = db.get(guild.id, user.id)
    if data["mups"] < found["mups"] or data["vups"] < found["vups"]:
        need = f"{found['mups']} Mups" + (f" + {found['vups']} Vups" if found["vups"] else "")
        have = f"{fmt_num(data['mups'])} Mups / {fmt_num(data['vups'])} Vups"
        return await interaction.response.send_message(f"Not enough currency! Need **{need}**, you have **{have}**.", ephemeral=True)

    if not db.spend(guild.id, user.id, found["mups"], found["vups"]):
        return await interaction.response.send_message("Purchase failed (concurrent balance change).", ephemeral=True)

    if role is None:
        role = await get_role(guild, found["role"])
    if role:
        try:
            await user.add_roles(role, reason=f"Bought from shop: {found['name']}")
        except discord.Forbidden:
            db.add_currency(guild.id, user.id, found["mups"], found["vups"])
            return await interaction.response.send_message("Couldn't give the role. Purchase refunded.", ephemeral=True)

    text = f"✅ Purchased **{found['name']}** for {found['mups']} Mups" + (f" + {found['vups']} Vups" if found["vups"] else "") + "!"
    refund = gumdrop_refund(guild.id, user.id, found["mups"], found["vups"])
    if refund:
        text += f"\n{refund}"
    await interaction.response.send_message(text)


@client.tree.command(name="daily", description="Claim your daily reward")
@app_commands.guild_only()
async def daily_cmd(interaction: discord.Interaction):
    guild = interaction.guild
    user = interaction.user
    now = int(time.time())

    streak = int(db.get_flag(guild.id, user.id, "daily_streak"))
    last = int(db.get_flag(guild.id, user.id, "daily_last"))

    treats = db.get_treats(guild.id, user.id)
    cooldown = DAILY_COOLDOWN_TRUFFLE if "witch_hat_truffle" in treats else DAILY_COOLDOWN
    window = DAILY_WINDOW_COCOA if "cauldron_cocoa" in treats else DAILY_WINDOW

    if last > 0 and now - last < cooldown:
        remaining = cooldown - (now - last)
        return await interaction.response.send_message(f"⏳ Daily cooldown! Come back in **{fmt_duration(remaining)}**.", ephemeral=True)

    if last > 0 and now - last > window:
        streak = 0

    streak += 1
    db.set_flag(guild.id, user.id, "daily_streak", float(streak))
    db.set_flag(guild.id, user.id, "daily_last", float(now))

    mups = DAILY_BASE_MUPS + (streak - 1) * DAILY_MUPS_STEP
    vups = DAILY_BASE_VUPS + (streak - 1) * DAILY_VUPS_STEP
    xp = DAILY_BASE_XP + (streak - 1) * DAILY_XP_STEP

    mult = treat_currency_mult(treats)
    mups = round(mups * mult, 2)
    vups = round(vups * mult, 2)
    xp = int(xp)

    db.add_currency(guild.id, user.id, mups, vups)
    await give_xp(interaction.guild.get_member(user.id) or user, xp)

    embed = discord.Embed(title="Daily Reward! 🎁", colour=PINK)
    embed.add_field(name="Streak", value=f"🔥 Day {streak}", inline=True)
    embed.add_field(name="Mups", value=fmt_num(mups), inline=True)
    embed.add_field(name="Vups", value=fmt_num(vups), inline=True)
    embed.add_field(name="XP", value=fmt_num(xp), inline=True)
    if mult > 1:
        embed.set_footer(text=f"Currency bonus from treats: {mult:.2f}x!")
    await interaction.response.send_message(embed=embed)


@client.tree.command(name="cookie", description="Give a cookie to someone 🍪")
@app_commands.describe(member="Who to give a cookie to")
@app_commands.guild_only()
async def cookie_cmd(interaction: discord.Interaction, member: discord.Member):
    if member.bot or member.id == interaction.user.id:
        return await interaction.response.send_message("Can't give a cookie to that person!", ephemeral=True)
    now = int(time.time())
    data = db.get(interaction.guild.id, interaction.user.id)
    if now - data["last_cookie_given"] < 3600:
        remaining = 3600 - (now - data["last_cookie_given"])
        return await interaction.response.send_message(f"🍪 Cookie on cooldown! Wait **{fmt_duration(remaining)}**.", ephemeral=True)
    db.give_cookie(interaction.guild.id, interaction.user.id, member.id, now)
    total = db.get(interaction.guild.id, member.id)["cookies_received"]
    await interaction.response.send_message(f"🍪 {interaction.user.mention} gave **{member.display_name}** a cookie! They now have **{total}** cookies!")


@client.tree.command(name="bag", description="See your treat collection 🎃")
@app_commands.describe(member="Whose bag to check (leave empty for yourself)")
@app_commands.guild_only()
async def bag_cmd(interaction: discord.Interaction, member: discord.Member | None = None):
    if not tot_is_active() and member is None:
        return await interaction.response.send_message("The Trick-or-Treat event is not active right now!", ephemeral=True)
    
    target = member or interaction.user
    treats = db.get_treats(interaction.guild.id, target.id)

    # Заранее определяем чья это сумка, чтобы не использовать сложные выражения с кавычками внутри f-строки
    if target == interaction.user:
        name_prefix = "Your"
    else:
        name_prefix = f"{target.display_name}'s"

    if not treats:
        return await interaction.response.send_message(f"{name_prefix} bag is empty! 🎃", ephemeral=True)

    lines = [f"• **{treat_name(k)}** x{v}" for k, v in sorted(treats.items())]
    missing = len(TREAT_KEYS) - len(treats)
    
    embed = discord.Embed(
        title=f"{name_prefix} Treat Bag 🎃",
        description="\n".join(lines),
        colour=discord.Colour.orange(),
    )
    embed.set_footer(text=f"{len(treats)}/{len(TREAT_KEYS)} unique treats • {missing} still to find!")
    
    await interaction.response.send_message(embed=embed, ephemeral=(target == interaction.user))


@client.tree.command(name="knock", description="Trick or treat! Knock on someone's door 🎃")
@app_commands.describe(member="Who to knock on")
@app_commands.guild_only()
async def knock_cmd(interaction: discord.Interaction, member: discord.Member):
    if not tot_is_active():
        return await interaction.response.send_message("Trick-or-Treat is not active right now!", ephemeral=True)
    if member.bot or member.id == interaction.user.id:
        return await interaction.response.send_message("You can't knock on that door!", ephemeral=True)

    knocker = interaction.user
    guild = interaction.guild
    now = time.time()

    if now - knock_last.get(knocker.id, 0) < KNOCK_COOLDOWN:
        remaining = int(KNOCK_COOLDOWN - (now - knock_last[knocker.id]))
        return await interaction.response.send_message(f"⏳ Wait **{fmt_duration(remaining)}** before knocking again!", ephemeral=True)

    pair_key = (min(knocker.id, member.id), max(knocker.id, member.id))
    if now - knock_pair_last.get(pair_key, 0) < KNOCK_PAIR_COOLDOWN:
        remaining = int(KNOCK_PAIR_COOLDOWN - (now - knock_pair_last[pair_key]))
        return await interaction.response.send_message(f"⏳ Wait **{fmt_duration(remaining)}** before knocking on this door again!", ephemeral=True)

    if member.id in pending_knocks:
        return await interaction.response.send_message("Someone else is already at their door!", ephemeral=True)

    ensure_treat_starter(guild.id, knocker.id)
    pending_knocks.add(member.id)
    knock_last[knocker.id] = now
    knock_pair_last[pair_key] = now

    view = KnockView(knocker, member, guild)
    await interaction.response.send_message(
        f"🎃 **{knocker.display_name}** knocks on **{member.mention}**'s door!\n*{member.display_name} has {KNOCK_TIMEOUT} seconds to answer...*",
        view=view,
    )


@client.tree.command(name="karma", description="See the trick-or-treat karma leaderboard")
@app_commands.guild_only()
async def karma_cmd(interaction: discord.Interaction):
    rows = db.top_karma(interaction.guild.id, 10)
    if not rows:
        return await interaction.response.send_message("No karma earned yet!", ephemeral=True)
    lines = []
    for i, (uid, karma) in enumerate(rows, 1):
        m = interaction.guild.get_member(uid)
        name = m.display_name if m else f"<@{uid}>"
        lines.append(f"**{i}.** {name} — ⭐ {karma}")
    embed = discord.Embed(title="🎃 Karma Leaderboard", description="\n".join(lines), colour=discord.Colour.orange())
    await interaction.response.send_message(embed=embed)


@client.tree.command(name="treat_stats", description="See treat collection statistics")
@app_commands.guild_only()
async def treat_stats_cmd(interaction: discord.Interaction):
    rows = db.treat_stats(interaction.guild.id, 10)
    if not rows:
        return await interaction.response.send_message("No treats collected yet!", ephemeral=True)
    lines = []
    for uid, unique, total in rows:
        m = interaction.guild.get_member(uid)
        name = m.display_name if m else f"<@{uid}>"
        lines.append(f"**{name}** — {unique} unique, {total} total")
    embed = discord.Embed(title="🍬 Treat Statistics", description="\n".join(lines), colour=discord.Colour.orange())
    await interaction.response.send_message(embed=embed)


# ======================================================================
#  Run
# ======================================================================
if not TOKEN:
    raise SystemExit("DISCORD_TOKEN environment variable is not set")

client.run(TOKEN)
