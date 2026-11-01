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
MUTED_ROLE_NAME = os.getenv("MUTED_ROLE_NAME", "Muted")

# "Night" for Moon Caramel is 22:00-06:00 in UTC plus this offset (in hours)
NIGHT_UTC_OFFSET = int(os.getenv("NIGHT_UTC_OFFSET", "0"))

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
XP_MIN = 20
XP_MAX = 35
XP_COOLDOWN_RANGE = (10, 25)    # random seconds between two XP gains (hidden from players)

# Fatigue: the more XP gains in the last hour, the less each one is worth.
# (gains in the last hour below this number, multiplier). No hard wall.
FATIGUE_STEPS = [(50, 1.0), (80, 0.7), (110, 0.4), (10**9, 0.15)]

# Message quality (by number of different words)
SINGLE_WORD_MULT = 0.5          # 1 word
SHORT_MULT = 0.8                # 2-3 words
LONG_MULT = 1.2                 # 10+ words

# Someone else must be talking too, otherwise XP is reduced
COMPANY_WINDOW = 15 * 60        # "someone else wrote here in the last 15 minutes"
SOLO_MULTIPLIER = 0.6
REPLY_BONUS = 1.15              # replying to another person

# Mechanical-looking timing detector (scripts / macros)
MECH_MESSAGES = 8               # look at the last 8 messages
MECH_TOLERANCE = 1.5            # all gaps within 1.5 s of each other ...
MECH_MIN_GAP = 3.0              # ... and every gap at least 3 s = suspicious
SUSPICIOUS_SECONDS = 30 * 60    # reduced XP for this long
SUSPICIOUS_MULTIPLIER = 0.2

# Voice also gives a little XP (only with 2+ people who can hear). 0 = no voice XP.
VOICE_XP_PER_MINUTE = 5

# Text XP (and Mups) can be limited to some channels: a channel counts if its name
# contains one of these words (decorations like "┊✦〉" don't matter), e.g.
# XP_CHANNELS = ["general", "meme-stuff"]. An empty list means XP in every channel.
XP_CHANNELS = []

# XP boosts (global 2x event and personal Trick or Treat buffs) do NOT stack:
# only the strongest one counts. Set to True to multiply them instead (x3 * 2 = x6).
XP_BOOSTS_STACK = False

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

# /daily
DAILY_COOLDOWN = 24 * 3600
DAILY_COOLDOWN_TRUFFLE = 19 * 3600     # Witch Hat Truffle
DAILY_WINDOW = 26 * 3600               # the streak survives this long after the last claim
DAILY_WINDOW_COCOA = 30 * 3600         # Cauldron Cocoa
DAILY_BASE_MUPS = 0.5
DAILY_BASE_VUPS = 0.1
DAILY_MUPS_STEP = 0.01                 # per streak day
DAILY_VUPS_STEP = 0.01                 # per 2 streak days
DAILY_BASE_XP = 1                      # XP from /daily at streak 1
DAILY_XP_STEP = 0.5                    # +0.5 XP every 2 streak days

WEEK_SECONDS = 7 * 24 * 3600


# ======================================================================
#  Number formatting
# ======================================================================
def fmt_num(value) -> str:
    """1234 -> '1,234'   10.5 -> '10.5'   0.505 -> '0.51'"""
    value = round(float(value), 2)
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


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
                       "last_cookie_given", "tot_cooldown", "xp_buff_end", "karma", "tot_starter"):
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
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS treats (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                treat TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id, treat)
            )
            """
        )
        treat_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(treats)")}
        if "count" not in treat_columns:
            self.conn.execute("ALTER TABLE treats ADD COLUMN count INTEGER NOT NULL DEFAULT 1")

        # Small per-user values for treat buffs, daily streaks and cooldowns
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_flags (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, key)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mutes (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                until INTEGER NOT NULL,
                role_ids TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                moderator_id INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                due INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id, kind)
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

    # ----- flags -----
    def get_flag(self, guild_id: int, user_id: int, key: str, default: float = 0.0) -> float:
        row = self.conn.execute(
            "SELECT value FROM user_flags WHERE guild_id = ? AND user_id = ? AND key = ?",
            (guild_id, user_id, key),
        ).fetchone()
        return row[0] if row else default

    def set_flag(self, guild_id: int, user_id: int, key: str, value: float):
        self.conn.execute(
            "INSERT OR REPLACE INTO user_flags (guild_id, user_id, key, value) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, key, value),
        )
        self.conn.commit()

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
        mups_gain = round((cycles_new - cycles_old) * MUPS_REWARD * booster_mult * global_curr, 2)

        self.conn.execute(
            "UPDATE users SET messages = messages + 1, pings = pings + ?, xp = xp + ?, "
            "xp_messages = ?, mups = ROUND(mups + ?, 2) WHERE guild_id = ? AND user_id = ?",
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
        new_xp = max(0, old_xp + amount)
        self.conn.execute(
            "UPDATE users SET xp = ? WHERE guild_id = ? AND user_id = ?",
            (new_xp, guild_id, user_id),
        )
        self.conn.commit()
        return old_xp, new_xp

    def add_voice(self, guild_id: int, user_id: int, seconds: int, booster_mult: float = 1.0):
        self._ensure(guild_id, user_id)
        old_seconds = self.conn.execute(
            "SELECT voice_seconds FROM users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()[0]

        new_seconds = old_seconds + seconds
        global_curr = float(self.get_setting("global_curr_mult", "1.0"))

        vups_gain = round((new_seconds // VOICE_SECONDS_PER_VUPS - old_seconds // VOICE_SECONDS_PER_VUPS) * VUPS_REWARD * booster_mult * global_curr, 2)

        self.conn.execute(
            "UPDATE users SET voice_seconds = ?, vups = ROUND(vups + ?, 2) "
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

    def top_cookies(self, guild_id: int, limit: int):
        return self.conn.execute(
            "SELECT user_id, cookies_received FROM users WHERE guild_id = ? AND cookies_received > 0 ORDER BY cookies_received DESC LIMIT ?",
            (guild_id, limit),
        ).fetchall()

    def spend(self, guild_id: int, user_id: int, mups: float, vups: float) -> bool:
        self._ensure(guild_id, user_id)
        cursor = self.conn.execute(
            "UPDATE users SET mups = ROUND(mups - ?, 2), vups = ROUND(vups - ?, 2) WHERE guild_id = ? AND user_id = ? AND mups >= ? AND vups >= ?",
            (mups, vups, guild_id, user_id, mups, vups),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    def refund(self, guild_id: int, user_id: int, mups: float, vups: float):
        self.conn.execute("UPDATE users SET mups = ROUND(mups + ?, 2), vups = ROUND(vups + ?, 2) WHERE guild_id = ? AND user_id = ?", (mups, vups, guild_id, user_id))
        self.conn.commit()

    def add_currency(self, guild_id: int, user_id: int, mups: float, vups: float):
        self._ensure(guild_id, user_id)
        self.conn.execute("UPDATE users SET mups = ROUND(mups + ?, 2), vups = ROUND(vups + ?, 2) WHERE guild_id = ? AND user_id = ?", (mups, vups, guild_id, user_id))
        self.conn.commit()

    def take_currency(self, guild_id: int, user_id: int, mups: float, vups: float):
        """Takes up to the given amounts (never goes below zero). Returns (mups_taken, vups_taken)."""
        self._ensure(guild_id, user_id)
        have_m, have_v = self.conn.execute(
            "SELECT mups, vups FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        ).fetchone()
        take_m = round(max(0, min(mups, have_m)), 2)
        take_v = round(max(0, min(vups, have_v)), 2)
        self.conn.execute(
            "UPDATE users SET mups = ROUND(mups - ?, 2), vups = ROUND(vups - ?, 2) WHERE guild_id = ? AND user_id = ?",
            (take_m, take_v, guild_id, user_id),
        )
        self.conn.commit()
        return take_m, take_v

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

    # ----- Trick or Treat: treats, karma, stealing -----
    def get_treats(self, guild_id: int, user_id: int) -> dict:
        """{treat_key: how many copies}"""
        rows = self.conn.execute(
            "SELECT treat, count FROM treats WHERE guild_id = ? AND user_id = ? AND count > 0", (guild_id, user_id)
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def add_treat(self, guild_id: int, user_id: int, treat: str) -> bool:
        """Adds one copy. Returns True if it was a brand new treat."""
        row = self.conn.execute(
            "SELECT count FROM treats WHERE guild_id = ? AND user_id = ? AND treat = ?", (guild_id, user_id, treat)
        ).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO treats (guild_id, user_id, treat, count) VALUES (?, ?, ?, 1)", (guild_id, user_id, treat)
            )
            self.conn.commit()
            return True
        self.conn.execute(
            "UPDATE treats SET count = count + 1 WHERE guild_id = ? AND user_id = ? AND treat = ?",
            (guild_id, user_id, treat),
        )
        self.conn.commit()
        return row[0] <= 0

    def remove_treat(self, guild_id: int, user_id: int, treat: str):
        """Removes one copy."""
        self.conn.execute(
            "UPDATE treats SET count = count - 1 WHERE guild_id = ? AND user_id = ? AND treat = ?",
            (guild_id, user_id, treat),
        )
        self.conn.execute(
            "DELETE FROM treats WHERE guild_id = ? AND user_id = ? AND treat = ? AND count <= 0",
            (guild_id, user_id, treat),
        )
        self.conn.commit()

    def treat_totals(self, guild_id: int):
        """Returns [(user_id, how many different treats)]."""
        return self.conn.execute(
            "SELECT user_id, COUNT(*) FROM treats WHERE guild_id = ? AND count > 0 GROUP BY user_id", (guild_id,)
        ).fetchall()

    def treat_owners(self, guild_id: int, treat: str) -> list[int]:
        rows = self.conn.execute(
            "SELECT user_id FROM treats WHERE guild_id = ? AND treat = ? AND count > 0", (guild_id, treat)
        ).fetchall()
        return [row[0] for row in rows]

    def treat_holders(self, guild_id: int, treat: str):
        """[(user_id, copies)] for everyone who owns the treat, most copies first."""
        return self.conn.execute(
            "SELECT user_id, count FROM treats WHERE guild_id = ? AND treat = ? AND count > 0 ORDER BY count DESC",
            (guild_id, treat),
        ).fetchall()

    def treat_stats(self, guild_id: int, limit: int):
        """[(user_id, different treats, total copies)] best collectors first."""
        return self.conn.execute(
            "SELECT user_id, COUNT(*), SUM(count) FROM treats WHERE guild_id = ? AND count > 0 "
            "GROUP BY user_id ORDER BY COUNT(*) DESC, SUM(count) DESC LIMIT ?",
            (guild_id, limit),
        ).fetchall()

    def tot_has_starter(self, guild_id: int, user_id: int) -> bool:
        self._ensure(guild_id, user_id)
        row = self.conn.execute(
            "SELECT tot_starter FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        ).fetchone()
        return bool(row[0])

    def tot_give_starter(self, guild_id: int, user_id: int, treat: str):
        self._ensure(guild_id, user_id)
        self.conn.commit()
        self.add_treat(guild_id, user_id, treat)
        self.conn.execute(
            "UPDATE users SET tot_starter = 1 WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        self.conn.commit()

    def reset_karma(self, guild_id: int):
        """A new event starts with zero karma. Treats are kept forever."""
        self.conn.execute("UPDATE users SET karma = 0 WHERE guild_id = ?", (guild_id,))
        self.conn.commit()

    def add_karma(self, guild_id: int, user_id: int, amount: int):
        self._ensure(guild_id, user_id)
        self.conn.execute(
            "UPDATE users SET karma = karma + ? WHERE guild_id = ? AND user_id = ?",
            (amount, guild_id, user_id),
        )
        self.conn.commit()

    def get_karma(self, guild_id: int, user_id: int) -> int:
        row = self.conn.execute(
            "SELECT karma FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        ).fetchone()
        return row[0] if row else 0

    def top_karma(self, guild_id: int, limit: int):
        return self.conn.execute(
            "SELECT user_id, karma FROM users WHERE guild_id = ? AND karma > 0 ORDER BY karma DESC LIMIT ?",
            (guild_id, limit),
        ).fetchall()

    def steal_currency(self, guild_id: int, victim_id: int, thief_id: int, amount: float, column: str) -> float:
        """Moves up to `amount` Mups or Vups from the victim to the thief. Returns how much moved."""
        if column not in ("mups", "vups"):
            raise ValueError("bad column")
        self._ensure(guild_id, victim_id)
        self._ensure(guild_id, thief_id)
        have = self.conn.execute(
            f"SELECT {column} FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, victim_id)
        ).fetchone()[0]
        taken = round(max(0, min(amount, have)), 2)
        if taken:
            self.conn.execute(
                f"UPDATE users SET {column} = ROUND({column} - ?, 2) WHERE guild_id = ? AND user_id = ?",
                (taken, guild_id, victim_id),
            )
            self.conn.execute(
                f"UPDATE users SET {column} = ROUND({column} + ?, 2) WHERE guild_id = ? AND user_id = ?",
                (taken, guild_id, thief_id),
            )
            self.conn.commit()
        return taken

    # ----- mutes -----
    def add_mute(self, guild_id: int, user_id: int, until: int, role_ids: str, reason: str, moderator_id: int):
        self.conn.execute(
            "INSERT OR REPLACE INTO mutes (guild_id, user_id, until, role_ids, reason, moderator_id) VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, until, role_ids, reason, moderator_id),
        )
        self.conn.commit()

    def get_mute(self, guild_id: int, user_id: int):
        return self.conn.execute(
            "SELECT until, role_ids, reason, moderator_id FROM mutes WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()

    def list_mutes(self, guild_id: int):
        return self.conn.execute("SELECT user_id, until FROM mutes WHERE guild_id = ?", (guild_id,)).fetchall()

    def remove_mute(self, guild_id: int, user_id: int):
        self.conn.execute("DELETE FROM mutes WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        self.conn.commit()

    # ----- cooldown reminders -----
    def set_reminder(self, guild_id: int, user_id: int, kind: str, due: int):
        self.conn.execute(
            "INSERT OR REPLACE INTO reminders (guild_id, user_id, kind, due) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, kind, due),
        )
        self.conn.commit()

    def due_reminders(self, guild_id: int, now: int):
        return self.conn.execute(
            "SELECT user_id, kind FROM reminders WHERE guild_id = ? AND due <= ?", (guild_id, now)
        ).fetchall()

    def remove_reminder(self, guild_id: int, user_id: int, kind: str):
        self.conn.execute(
            "DELETE FROM reminders WHERE guild_id = ? AND user_id = ? AND kind = ?", (guild_id, user_id, kind)
        )
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

async def give_xp(member: discord.Member, amount: int):
    """Adds (or removes) XP outside of messages and hands out level roles if needed."""
    old_xp, new_xp = db.add_xp(member.guild.id, member.id, amount)
    new_level = level_from_xp(new_xp)[0]
    if new_level > level_from_xp(old_xp)[0]:
        await sync_level_roles(member, new_level)

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
        default_used = False
        for label, role in group.items():
            selected = (role in held) if role else (not has_any)
            # A single-choice menu may only have ONE default value, otherwise Discord rejects the whole menu
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
        all_items.extend(EVENT_NAME_EMOJIS)   # event-only name emojis (not sold in the shop)

        seen = set()
        default_used = False
        for item in all_items:
            role = find_shop_role(guild, item["role"])
            if role is None or role.id in seen: continue
            seen.add(role.id)
            self.all_emoji_roles.append(role)
            if role in member.roles:
                # Only ONE default value is allowed in a single-choice menu
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
        text = f"Your name has been changed to **{self.new_name.value.strip()}** for 100 Mups!"
        back = gumdrop_refund(guild.id, member.id, 100, 0)
        if back: text += f"\n{back}"
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
        try:
            view = EditProfilePanel(interaction.user)
            embed = discord.Embed(title="Edit Server Profile", description="Change your roles, emojis, and name here!", colour=CREAM)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception as error:
            print(f"Edit profile failed: {error!r}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Something went wrong while opening your profile editor. Please tell a mod.", ephemeral=True)


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
        self.add_view(InventoryView())
        voice_tracker.start()
        voice_cleanup.start()
        mute_checker.start()
        reminder_loop.start()
        treat_weekly.start()
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

def combine_xp_multipliers(global_mult: float, personal_mult: float) -> float:
    """Global event and personal buff: the strongest boost wins, they don't multiply.
    A curse (below 1.0) still applies when there is no boost."""
    if XP_BOOSTS_STACK:
        return global_mult * personal_mult
    boosts = [m for m in (global_mult, personal_mult) if m > 1.0]
    if boosts:
        return max(boosts)
    return min(global_mult, personal_mult)
# --- end xp engine helpers ---


# ======================================================================
#  Treats: list, descriptions and buffs
# ======================================================================
# The 20 treats. They stay in the inventory after the event and their buffs keep working.
TREATS = [
    ("candy_bear", "Candy Bear"),
    ("black_bombob", "Black Bombob"),
    ("pumpkin_cookie", "Pumpkin Cookie"),
    ("ghost_marshmallow", "Ghost Marshmallow"),
    ("spooky_lollipop", "Spooky Lollipop"),
    ("witch_hat_truffle", "Witch Hat Truffle"),
    ("bat_wing_gummy", "Bat Wing Gummy"),
    ("cat_paw_fudge", "Cat Paw Fudge"),
    ("moon_caramel", "Moon Caramel"),
    ("cursed_candy_corn", "Cursed Candy Corn"),
    ("gloomy_gumdrop", "Gloomy Gumdrop"),
    ("skeleton_biscuit", "Skeleton Biscuit"),
    ("cobweb_cotton_candy", "Cobweb Cotton Candy"),
    ("cauldron_cocoa", "Cauldron Cocoa"),
    ("mummy_muffin", "Mummy Muffin"),
    ("sugar_skull", "Sugar Skull"),
    ("toffee_toad", "Toffee Toad"),
    ("maple_taffy", "Maple Taffy"),
    ("goober_jelly", "Goober Jelly"),
    ("chocolate_pawprint", "Chocolate Pawprint"),
]
TREAT_KEYS = [key for key, _ in TREATS]
TREAT_NAMES = dict(TREATS)

TREAT_INFO = {
    "candy_bear": "+2% XP gain.",
    "black_bombob": "Every 5th XP message: 50% chance to double the XP, 50% chance to lose half of it.",
    "pumpkin_cookie": "/award_cookie gives 2 cookies instead of 1.",
    "ghost_marshmallow": "Tricks against you succeed 5% less often.",
    "spooky_lollipop": "You get +1 XP every time someone pats you.",
    "witch_hat_truffle": "Your /daily cooldown is 19h instead of 24h.",
    "bat_wing_gummy": "If you get muted, your mute is 1% shorter.",
    "cat_paw_fudge": "10% chance to double the Mups and Vups you gain.",
    "moon_caramel": "At night (real life) you gain +5% more XP.",
    "cursed_candy_corn": "+3% Mups and Vups gain, but -2% XP gain.",
    "gloomy_gumdrop": "When you spend Mups or Vups, 10% chance to get half of them back.",
    "skeleton_biscuit": "Takes 2 Mups and 1 Vups every week. With more than 100 Mups, 10% chance to give you 10 Mups.",
    "cobweb_cotton_candy": "Talk too much and you get muted for 60 seconds, then +5% XP for an hour. (5h cooldown)",
    "cauldron_cocoa": "Your daily streak lasts longer: 26h becomes 30h.",
    "mummy_muffin": "Voice chats give you 10% more XP and voice time.",
    "sugar_skull": "Your sent message can crumble away with 10% chance. (5h cooldown)",
    "toffee_toad": "If your daily streak breaks, revive it once a month with /revive_streak.",
    "maple_taffy": "Gives you a random shop item every week. Repeated items can happen.",
    "goober_jelly": "The more you talk, the more XP you get per XP message (+0.001 XP for every message sent).",
    "chocolate_pawprint": "When you pat someone, they get 1 XP.",
}

LOVELY_HORNET_ROLE = "Lovely Hornet ^^"   # end of the event: most karma
HORNET_HALLOW_ROLE = "Hornet Hallow"      # collected all 20 treats
LMAO_HORNET_ROLE = "Lmao Hornet"          # 1% on Treat, can drop only once ever
GHOSTLY_ROLE = "Ghostly"                  # 1% on a successful Trick, for the scared person
EVENT_NAME_EMOJIS = [
    {"name": "Lovely Hornet", "role": LOVELY_HORNET_ROLE},
    {"name": "Hornet Hallow", "role": HORNET_HALLOW_ROLE},
    {"name": "Lmao Hornet", "role": LMAO_HORNET_ROLE},
    {"name": "Ghostly", "role": GHOSTLY_ROLE},
]

TRICK_SUCCESS_CHANCE = 0.5      # a Trick works 50% of the time
GHOST_MARSHMALLOW_REDUCTION = 0.05
TRICK_XP_PERCENT = 20           # a Trick can steal this % of the XP progress inside the current level
TRICK_CURRENCY_RANGE = (10, 20)
TREAT_REPEAT_CHANCE = 0.70      # a Treat is a repeat (the receiver already owns it) 70% of the time, when the giver has one
LMAO_CHANCE = 0.01
GHOST_CHANCE = 0.01
TREAT_END_REWARD_MUPS = 5       # Mups per collected treat when the event ends

KARMA_TREAT = 2                 # you gave a treat
KARMA_TRICK_FAIL = -1           # your Trick failed

KNOCK_TIMEOUT = 90              # seconds to answer the door
KNOCK_COOLDOWN = 600            # seconds between two knocks by the same person (10 minutes)
TRICK_COOLDOWN = 300            # seconds between two Tricks by the same person (5 minutes)
KNOCK_PAIR_COOLDOWN = 3600      # the same person can't knock on the same door again for this long

pending_knocks: set[int] = set()
knock_last: dict[int, float] = {}
knock_pair_last: dict[tuple[int, int], float] = {}
trick_last: dict[int, float] = {}

def treat_name(key: str) -> str:
    return TREAT_NAMES.get(key, key)

def tot_is_active() -> bool:
    return db.get_setting("tot_active", "0") == "1"

def ensure_treat_starter(guild_id: int, user_id: int):
    """Late joiners and quiet members get their starting treat the first time they take part."""
    if not tot_is_active() or db.tot_has_starter(guild_id, user_id):
        return
    db.tot_give_starter(guild_id, user_id, random.choice(TREAT_KEYS))

def is_night() -> bool:
    hour = (datetime.datetime.now(datetime.timezone.utc).hour + NIGHT_UTC_OFFSET) % 24
    return hour >= 22 or hour < 6

def treat_currency_mult(treats: dict) -> float:
    """Cursed Candy Corn (+3%) and Cat Paw Fudge (10% chance to double)."""
    mult = 1.0
    if "cursed_candy_corn" in treats: mult *= 1.03
    if "cat_paw_fudge" in treats and random.random() < 0.10: mult *= 2
    return mult

def gumdrop_refund(guild_id: int, user_id: int, mups: float, vups: float) -> str | None:
    """Gloomy Gumdrop: 10% chance to get half of the spent Mups/Vups back. Returns a text or None."""
    if "gloomy_gumdrop" not in db.get_treats(guild_id, user_id): return None
    if random.random() >= 0.10: return None
    back_m, back_v = round(mups / 2, 2), round(vups / 2, 2)
    if not back_m and not back_v: return None
    db.add_currency(guild_id, user_id, back_m, back_v)
    parts = []
    if back_m: parts.append(f"{fmt_num(back_m)} Mups")
    if back_v: parts.append(f"{fmt_num(back_v)} Vups")
    return f"Your Gloomy Gumdrop gave back {' and '.join(parts)}!"

# --- game rules (no Discord needed) ---
def resolve_treat(guild_id: int, knocker_id: int, target_id: int) -> dict:
    """The target (giver) gives the knocker a copy of one of the GIVER's OWN treats.
    70% of the time it is one the knocker already owns (a repeat), if the giver has such a treat."""
    mine = db.get_treats(guild_id, target_id)
    if not mine:
        return {"treat": None, "is_new": False, "count": 0, "lmao": False}
    theirs = db.get_treats(guild_id, knocker_id)
    repeat_pool = [key for key in mine if key in theirs]
    new_pool = [key for key in mine if key not in theirs]
    if repeat_pool and (not new_pool or random.random() < TREAT_REPEAT_CHANCE):
        key = random.choice(repeat_pool)
    else:
        key = random.choice(new_pool)
    is_new = db.add_treat(guild_id, knocker_id, key)
    count = db.get_treats(guild_id, knocker_id).get(key, 1)
    db.add_karma(guild_id, target_id, KARMA_TREAT)
    lmao = db.get_setting("lmao_hornet_given", "0") != "1" and random.random() < LMAO_CHANCE
    return {"treat": key, "is_new": is_new, "count": count, "lmao": lmao}

def resolve_trick(guild_id: int, knocker_id: int, target_id: int, roll: float) -> dict:
    """The target (thief) tricks the knocker (victim). `roll` is a random number from 0 to 1."""
    chance = TRICK_SUCCESS_CHANCE
    if "ghost_marshmallow" in db.get_treats(guild_id, knocker_id):
        chance -= GHOST_MARSHMALLOW_REDUCTION
    if roll >= chance:
        db.add_karma(guild_id, target_id, KARMA_TRICK_FAIL)
        return {"success": False, "karma": KARMA_TRICK_FAIL}

    result = {"success": True, "kind": None, "amount": 0, "currency": None, "treat": None, "karma": 0, "ghost": False}

    # steal one copy of a random treat from the victim
    victim_treats = db.get_treats(guild_id, knocker_id)
    if victim_treats:
        treat = random.choice(list(victim_treats))
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

    if result["treat"]: loss += 1
    if loss == 0: loss = 1
    result["karma"] = -loss
    db.add_karma(guild_id, target_id, -loss)
    result["ghost"] = random.random() < GHOST_CHANCE
    return result

async def check_hornet_hallow(guild: discord.Guild, member: discord.Member) -> bool:
    """Collecting every treat gives the limited Hornet Hallow role. True if it was just given."""
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
#  Mute helpers
# ======================================================================
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

def is_moderator(member: discord.Member) -> bool:
    perms = member.guild_permissions
    return member.id == member.guild.owner_id or perms.administrator or perms.moderate_members

async def release_mute(guild: discord.Guild, user_id: int, notify: bool = True) -> bool:
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
    except (discord.Forbidden, discord.HTTPException):
        pass

    if notify:
        try:
            await member.send(embed=discord.Embed(
                title="Mute is over",
                description="Hi! Your mute is over, welcome back. Your roles were returned :3",
                colour=PINK,
            ))
        except (discord.Forbidden, discord.HTTPException):
            pass
    return True


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
            treats = db.get_treats(guild.id, member.id)
            has_booster = any("CERTIFIED PAWBOOSTER" in r.name.upper() for r in member.roles)
            booster_mult = 1.5 if has_booster else 1.0
            muffin = 1.1 if "mummy_muffin" in treats else 1.0
            db.add_voice(guild.id, member.id, int(60 * booster_mult * muffin), treat_currency_mult(treats))

            member_voice_xp = int(voice_xp * muffin)
            if member_voice_xp > 0:
                old_xp, new_xp = db.add_xp(guild.id, member.id, member_voice_xp)
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

REMINDER_TEXT = {
    "daily": "Your /daily is ready again! Come and collect it to keep your streak going.",
    "knock": "Your knock-knock cooldown is over. You can knock on a door again!",
    "cookie": "You can give another cookie with /award_cookie now.",
}

def schedule_reminder(guild_id: int, user_id: int, kind: str, seconds: float):
    """Remember to send a private message when a cooldown ends. Saved in the database, so restarts don't lose it."""
    db.set_reminder(guild_id, user_id, kind, int(time.time() + seconds))

@tasks.loop(seconds=30)
async def reminder_loop():
    guild = client.get_guild(GUILD_ID)
    if guild is None: return
    for user_id, kind in db.due_reminders(guild.id, int(time.time())):
        db.remove_reminder(guild.id, user_id, kind)
        text = REMINDER_TEXT.get(kind)
        if text is None or db.get_flag(guild.id, user_id, "reminders_off"): continue
        if kind == "knock" and not tot_is_active(): continue
        member = guild.get_member(user_id)
        if member is None: continue
        try:
            await member.send(embed=discord.Embed(title="Cooldown is over", description=text, colour=PINK))
        except (discord.Forbidden, discord.HTTPException):
            pass   # their DMs are closed

@reminder_loop.before_loop
async def before_reminder_loop():
    await client.wait_until_ready()

@tasks.loop(seconds=30)
async def mute_checker():
    guild = client.get_guild(GUILD_ID)
    if guild is None: return
    now = int(time.time())
    for user_id, until in db.list_mutes(guild.id):
        if now >= until:
            await release_mute(guild, user_id)

@mute_checker.before_loop
async def before_mute_checker():
    await client.wait_until_ready()

@tasks.loop(hours=1)
async def treat_weekly():
    """Skeleton Biscuit and Maple Taffy do their thing once a week. The timer starts the first time we see the treat."""
    guild = client.get_guild(GUILD_ID)
    if guild is None: return
    now = time.time()

    for user_id in db.treat_owners(guild.id, "skeleton_biscuit"):
        last = db.get_flag(guild.id, user_id, "skeleton_last")
        if last == 0:
            db.set_flag(guild.id, user_id, "skeleton_last", now)
            continue
        if now - last < WEEK_SECONDS: continue
        db.set_flag(guild.id, user_id, "skeleton_last", now)
        db.take_currency(guild.id, user_id, 2, 1)
        if db.get(guild.id, user_id)["mups"] > 100 and random.random() < 0.10:
            db.add_currency(guild.id, user_id, 10, 0)

    all_items = [item for category in get_active_shop_categories().values() for item in category["items"]]
    for user_id in db.treat_owners(guild.id, "maple_taffy"):
        last = db.get_flag(guild.id, user_id, "taffy_last")
        if last == 0:
            db.set_flag(guild.id, user_id, "taffy_last", now)
            continue
        if now - last < WEEK_SECONDS: continue
        db.set_flag(guild.id, user_id, "taffy_last", now)
        member = guild.get_member(user_id)
        if member is None or not all_items: continue
        item = random.choice(all_items)
        role = find_shop_role(guild, item["role"])
        if role is None: continue
        already = role in member.roles
        if not already:
            try: await member.add_roles(role, reason="Maple Taffy weekly gift")
            except (discord.Forbidden, discord.HTTPException): continue
        text = f"Your Maple Taffy brought you **{item['name']}**!"
        if already: text += " You already had it, so nothing changes this week."
        try: await member.send(embed=discord.Embed(title="Maple Taffy", description=text, colour=PINK))
        except (discord.Forbidden, discord.HTTPException): pass

@treat_weekly.before_loop
async def before_treat_weekly():
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
    gid = message.guild.id
    pings = len({m.id for m in message.mentions if not m.bot and m.id != member.id})
    key = (gid, member.id)
    now_f = time.time()
    now = int(now_f)
    treats = db.get_treats(gid, member.id)

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
    cobweb_triggered = False
    if counted and channel_gives_xp(message.channel) and now_f >= next_xp_time.get(key, 0):
        history = xp_timestamps.setdefault(key, deque())
        while history and now - history[0] > 3600:
            history.popleft()
        gains_last_hour = len(history)

        data = db.get(gid, member.id)
        current_level = level_from_xp(data["xp"])[0]

        quality = quality_multiplier(unique_word_count(message.content))
        fatigue = fatigue_multiplier(gains_last_hour)
        company_mult = 1.0 if company else SOLO_MULTIPLIER
        suspicious_mult = SUSPICIOUS_MULTIPLIER if now_f < suspicious_until.get(key, 0) else 1.0
        reply_mult = REPLY_BONUS if replied_to_human else 1.0

        base_gain = random.randint(XP_MIN, XP_MAX)
        level_bonus = int(current_level * 0.5)
        if "goober_jelly" in treats:
            level_bonus += data["messages"] * 0.001

        # Global / TrickOrTreat Multipliers
        global_xp = float(db.get_setting("global_xp_mult", "1.0"))
        tot_xp_buff = 1.0
        if now < data.get("xp_buff_end", 0):
            tot_xp_buff = data.get("xp_buff_value", 1.0)
        event_mult = combine_xp_multipliers(global_xp, tot_xp_buff)

        # Treat buffs
        treat_xp = 1.0
        if "candy_bear" in treats: treat_xp *= 1.02
        if "moon_caramel" in treats and is_night(): treat_xp *= 1.05
        if "cursed_candy_corn" in treats: treat_xp *= 0.98
        if now_f < db.get_flag(gid, member.id, "cobweb_boost_end"): treat_xp *= 1.05

        gain = int((base_gain + level_bonus) * quality * fatigue * company_mult * suspicious_mult * reply_mult * event_mult * treat_xp)
        gain = max(1, gain)

        if "black_bombob" in treats:
            counter = int(db.get_flag(gid, member.id, "bombob_n")) + 1
            if counter >= 5:
                counter = 0
                gain = gain * 2 if random.random() < 0.5 else max(1, gain // 2)
            db.set_flag(gid, member.id, "bombob_n", counter)

        # Mups only for real conversation, and only for the first gains of each hour
        count_for_mups = (
            quality >= SHORT_MULT and company and suspicious_mult == 1.0
            and gains_last_hour < MUPS_ELIGIBLE_PER_HOUR
        )

        next_xp_time[key] = now_f + random.uniform(*XP_COOLDOWN_RANGE)
        history.append(now)

        # Cobweb Cotton Candy: talking a lot (a full hour of normal XP gains) mutes you for a minute
        if ("cobweb_cotton_candy" in treats and len(history) >= FATIGUE_STEPS[0][0]
                and now_f >= db.get_flag(gid, member.id, "cobweb_cd")):
            db.set_flag(gid, member.id, "cobweb_cd", now_f + 5 * 3600)
            db.set_flag(gid, member.id, "cobweb_boost_end", now_f + 60 + 3600)
            cobweb_triggered = True

    has_booster = any("CERTIFIED PAWBOOSTER" in r.name.upper() for r in member.roles)
    booster_mult = (1.5 if has_booster else 1.0) * treat_currency_mult(treats)

    old_xp, new_xp, _mups = db.add_message(gid, member.id, pings, gain, counted, booster_mult, count_for_mups)
    old_level = level_from_xp(old_xp)[0]
    new_level = level_from_xp(new_xp)[0]

    if new_level > old_level:
        granted = await sync_level_roles(member, new_level)
        text = f"{member.mention} just reached **level {new_level}**!"
        if granted:
            names = ", ".join(f"**{role.name}**" for role in granted)
            text += f" New role unlocked: {names}"
        await message.channel.send(text)

    if cobweb_triggered:
        try:
            await member.timeout(datetime.timedelta(seconds=60), reason="Cobweb Cotton Candy: talked too much")
            await message.channel.send(embed=discord.Embed(
                title="Cobweb Cotton Candy",
                description=f"{member.mention} talked so much that the cobwebs wrapped around them. "
                            f"Quiet for 60 seconds, then +5% XP for an hour.",
                colour=PINK))
        except (discord.Forbidden, discord.HTTPException):
            pass

    # Sugar Skull: sometimes your message crumbles away
    if ("sugar_skull" in treats and now_f >= db.get_flag(gid, member.id, "sugar_cd")
            and random.random() < 0.10):
        db.set_flag(gid, member.id, "sugar_cd", now_f + 5 * 3600)
        try:
            await message.delete()
            await message.channel.send(embed=discord.Embed(
                description=f"The Sugar Skull of {member.mention} crumbled and took their message with it.",
                colour=PINK), delete_after=10)
        except (discord.Forbidden, discord.HTTPException):
            pass

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
    karma = db.get_karma(interaction.guild.id, target_member.id)

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

    embed.add_field(name="Mups", value=fmt_num(data['mups']), inline=True)
    embed.add_field(name="Vups", value=fmt_num(data['vups']), inline=True)
    embed.add_field(name="Cookies", value=f"🍪 {data['cookies_received']:,}", inline=True)

    embed.add_field(name="Messages sent", value=f"{data['messages']:,}", inline=True)
    embed.add_field(name="Pings sent", value=f"{data['pings']:,}", inline=True)
    embed.add_field(name="Time in voice", value=format_duration(data["voice_seconds"]), inline=True)

    embed.add_field(name="Karma", value=f"{karma:,}", inline=True)
    embed.add_field(name="Next reward", value=next_reward, inline=True)
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
        lines.append(f"**#{len(lines) + 1}** {member.display_name} | {fmt_num(mups)} Mups")
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

    amount = 2 if "pumpkin_cookie" in db.get_treats(guild_id, giver.id) else 1
    db.give_cookie(guild_id, giver.id, user.id, now, amount)
    schedule_reminder(guild_id, giver.id, "cookie", cooldown)
    if amount == 1:
        text = f"🍪 {giver.mention} gave a delicious cookie to {user.mention}! So sweet :3"
    else:
        text = f"🍪 {giver.mention} gave **2** delicious cookies to {user.mention} thanks to their Pumpkin Cookie! So sweet :3"
    await interaction.response.send_message(text)


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

@client.tree.command(name="cookieboard", description="Shows the people with the most cookies")
@app_commands.guild_only()
async def cookieboard(interaction: discord.Interaction):
    guild = interaction.guild
    lines = []
    for user_id, cookies in db.top_cookies(guild.id, 50):
        member = guild.get_member(user_id)
        if member is None: continue
        lines.append(f"**#{len(lines) + 1}** {member.display_name} | {cookies:,} cookies")
        if len(lines) == 10: break
    embed = discord.Embed(
        title="Cookie Leaderboard",
        description="\n".join(lines) if lines else "Nobody has cookies yet. Share some with /award_cookie!",
        colour=CREAM,
    )
    await interaction.response.send_message(embed=embed)


# ======================================================================
#  Daily reward
# ======================================================================
def daily_reward(streak: int) -> tuple[float, float]:
    """Streak 1: 0.5 Mups, 0.1 Vups. Mups grow by 0.01 every day, Vups by 0.01 every 2 days."""
    mups = DAILY_BASE_MUPS + DAILY_MUPS_STEP * (streak - 1)
    vups = DAILY_BASE_VUPS + DAILY_VUPS_STEP * ((streak - 1) // 2)
    return mups, vups

def month_key(now: float) -> int:
    moment = datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    return moment.year * 100 + moment.month

def daily_timing(treats: dict) -> tuple[int, int]:
    cooldown = DAILY_COOLDOWN_TRUFFLE if "witch_hat_truffle" in treats else DAILY_COOLDOWN
    window = DAILY_WINDOW_COCOA if "cauldron_cocoa" in treats else DAILY_WINDOW
    return cooldown, window

@client.tree.command(name="daily", description="Collect your daily Mups and Vups")
@app_commands.guild_only()
async def daily(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    member = interaction.user
    now = int(time.time())
    treats = db.get_treats(guild_id, member.id)
    cooldown, window = daily_timing(treats)

    last = int(db.get_flag(guild_id, member.id, "daily_last"))
    streak = int(db.get_flag(guild_id, member.id, "daily_streak"))

    if last and now - last < cooldown:
        remaining = cooldown - (now - last)
        hours, rest = divmod(remaining, 3600)
        embed = discord.Embed(title="Daily already collected", description=f"Come back in {hours}h {rest // 60}m.", colour=CREAM)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    broken = bool(last) and now - last > window
    can_revive = (broken and streak > 0 and "toffee_toad" in treats
                  and int(db.get_flag(guild_id, member.id, "revive_month")) != month_key(now))
    if can_revive and int(db.get_flag(guild_id, member.id, "revive_warned")) != last:
        db.set_flag(guild_id, member.id, "revive_warned", last)
        embed = discord.Embed(
            title="Your streak broke",
            description=f"Your streak of **{streak}** broke, but your Toffee Toad can bring it back.\n"
                        f"Use **/revive_streak** to revive it, or use **/daily** again to start over.",
            colour=CREAM,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    new_streak = streak + 1 if (last and not broken) else 1
    mups, vups = daily_reward(new_streak)

    has_booster = any("CERTIFIED PAWBOOSTER" in r.name.upper() for r in member.roles)
    mult = (1.5 if has_booster else 1.0) * float(db.get_setting("global_curr_mult", "1.0")) * treat_currency_mult(treats)
    mups, vups = round(mups * mult, 2), round(vups * mult, 2)

    # XP: 1 at first, +0.5 every 2 streak days. Halves are kept and paid out as whole XP.
    xp_exact = DAILY_BASE_XP + DAILY_XP_STEP * ((new_streak - 1) // 2)
    total_xp = xp_exact + db.get_flag(guild_id, member.id, "xp_frac")
    whole_xp = int(total_xp)
    db.set_flag(guild_id, member.id, "xp_frac", round(total_xp - whole_xp, 2))

    db.add_currency(guild_id, member.id, mups, vups)
    db.set_flag(guild_id, member.id, "daily_last", now)
    db.set_flag(guild_id, member.id, "daily_streak", new_streak)
    schedule_reminder(guild_id, member.id, "daily", cooldown)
    if whole_xp:
        await give_xp(member, whole_xp)

    embed = discord.Embed(
        title="Daily reward collected!",
        description=f"Streak x{new_streak}\nYou gain {fmt_num(mups)} of Mups and {fmt_num(vups)} of Vups!\nAlso +{fmt_num(xp_exact)} XP.",
        colour=PINK,
    )
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@client.tree.command(name="revive_streak", description="Bring back a broken daily streak (needs a Toffee Toad, once a month)")
@app_commands.guild_only()
async def revive_streak(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    member = interaction.user
    now = int(time.time())
    treats = db.get_treats(guild_id, member.id)

    if "toffee_toad" not in treats:
        await interaction.response.send_message(embed=discord.Embed(
            description="You need a Toffee Toad treat to revive a streak.", colour=CREAM), ephemeral=True)
        return

    cooldown, window = daily_timing(treats)
    last = int(db.get_flag(guild_id, member.id, "daily_last"))
    streak = int(db.get_flag(guild_id, member.id, "daily_streak"))
    if not last or streak <= 0 or now - last <= window:
        await interaction.response.send_message(embed=discord.Embed(
            description="Your streak isn't broken, there is nothing to revive.", colour=CREAM), ephemeral=True)
        return
    if int(db.get_flag(guild_id, member.id, "revive_month")) == month_key(now):
        await interaction.response.send_message(embed=discord.Embed(
            description="You already revived a streak this month. Try again next month!", colour=CREAM), ephemeral=True)
        return

    db.set_flag(guild_id, member.id, "revive_month", month_key(now))
    # Pretend the last claim was one cooldown ago: /daily is ready and the streak continues
    db.set_flag(guild_id, member.id, "daily_last", now - cooldown)
    schedule_reminder(guild_id, member.id, "daily", 0)   # /daily is ready right now
    await interaction.response.send_message(embed=discord.Embed(
        title="Streak revived!",
        description=f"Your streak of **{streak}** is back. Use **/daily** to keep it going.",
        colour=PINK))


# ======================================================================
#  Trick or Treat: knock-knock (members trick or treat each other)
# ======================================================================
class KnockView(discord.ui.View):
    def __init__(self, knocker: discord.Member, target: discord.Member):
        super().__init__(timeout=KNOCK_TIMEOUT)
        self.knocker = knocker
        self.target = target
        self.message: discord.Message | None = None
        self.finished = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.target.id: return True
        await interaction.response.send_message("This door isn't yours to open!", ephemeral=True)
        return False

    def finish(self):
        self.finished = True
        pending_knocks.discard(self.target.id)
        self.stop()

    async def show_result(self, text: str):
        embed = discord.Embed(title="Knock knock!", description=text, colour=PINK)
        try:
            await self.message.edit(content=None, embed=embed, view=None)
        except (discord.NotFound, discord.HTTPException, AttributeError):
            pass

    @discord.ui.button(label="Treat!", style=discord.ButtonStyle.success)
    async def treat(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished:
            await interaction.response.send_message("Too late, the door is closed.", ephemeral=True)
            return
        self.finish()
        await interaction.response.defer()

        guild = interaction.guild
        result = resolve_treat(guild.id, self.knocker.id, self.target.id)
        knocker, target = self.knocker.mention, self.target.mention
        if result["treat"] is None:
            await self.show_result(f"{target} chose **Treat**, but has no treats to give yet. The thought counts!")
            return
        name = treat_name(result["treat"])

        if result["is_new"]:
            text = f"{target} chose **Treat** and gave {knocker} a brand new treat: **{name}**! (+{KARMA_TREAT} karma)"
        else:
            text = f"{target} chose **Treat** and gave {knocker} another **{name}**! Now they have {result['count']}. (+{KARMA_TREAT} karma)"

        if result["lmao"] and await grant_role_by_name(self.knocker, LMAO_HORNET_ROLE, "Lucky Treat drop"):
            db.set_setting("lmao_hornet_given", "1")
            text += f"\nA tiny hornet flew out of the bag... {knocker} got the limited **{LMAO_HORNET_ROLE}** role! Nobody else can get it now."
        if await check_hornet_hallow(guild, self.knocker):
            text += f"\n{knocker} collected every treat and became a **{HORNET_HALLOW_ROLE}**!"
        await self.show_result(text)

    @discord.ui.button(label="Trick!", style=discord.ButtonStyle.danger)
    async def trick(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished:
            await interaction.response.send_message("Too late, the door is closed.", ephemeral=True)
            return
        now = time.time()
        left = TRICK_COOLDOWN - (now - trick_last.get(self.target.id, 0))
        if left > 0:
            await interaction.response.send_message(f"Your tricks need a rest! You can trick again in {int(left // 60)}m {int(left % 60)}s. You can still choose Treat.", ephemeral=True)
            return
        trick_last[self.target.id] = now
        self.finish()
        await interaction.response.defer()

        guild = interaction.guild
        result = resolve_trick(guild.id, self.knocker.id, self.target.id, random.random())
        knocker, target = self.knocker.mention, self.target.mention

        if not result["success"]:
            await self.show_result(f"{target} chose **Trick**... but {knocker} wasn't scared at all! The trick failed ~w~ ({KARMA_TRICK_FAIL} karma)")
            return

        treat_text = f"**{treat_name(result['treat'])}**" if result["treat"] else None
        karma_text = f"({result['karma']} karma)"
        if result["kind"] == "xp" and result["amount"]:
            text = f"{target} just stole {TRICK_XP_PERCENT}% of {knocker}'s XP"
            text += f" and their {treat_text} treat!! How evil!! {karma_text}" if treat_text else "!! How evil!! " + karma_text
        elif result["kind"] == "currency" and result["amount"]:
            text = f"{target} stole {fmt_num(result['amount'])} {result['currency']} from {knocker}"
            text += f" with their {treat_text} treat!! {karma_text}" if treat_text else "!! " + karma_text
        elif treat_text:
            text = f"{target} chose **Trick** and scared {knocker}! They only found their {treat_text} treat, and took it! {karma_text}"
        else:
            text = f"{target} chose **Trick** and scared {knocker}, but there was nothing to steal! {karma_text}"

        if await check_hornet_hallow(guild, self.target):
            text += f"\n{target} collected every treat and became a **{HORNET_HALLOW_ROLE}**!"
        await self.show_result(text)

        # The scared person can rarely turn into a ghost
        if result["ghost"] and await grant_role_by_name(self.knocker, GHOSTLY_ROLE, "Scared by a cat ghost"):
            try:
                await interaction.followup.send(
                    content=knocker,
                    embed=discord.Embed(
                        description=f"WOAHH.. A GHOST!! You were scared by a cat ghost!... You got.. a special role. (/profile)",
                        colour=CREAM),
                    allowed_mentions=discord.AllowedMentions(users=[self.knocker]),
                )
            except discord.HTTPException:
                pass

    async def on_timeout(self):
        if self.finished: return
        self.finish()
        try:
            await self.message.edit(content=None, embed=discord.Embed(
                title="Knock knock!",
                description=f"Nobody opened the door... {self.target.mention} didn't answer {self.knocker.mention}.",
                colour=PINK), view=None)
        except (discord.NotFound, discord.HTTPException, AttributeError):
            pass

@client.tree.command(name="knock-knock", description="Knock on someone's door: will they give you a Treat or play a Trick?")
@app_commands.describe(user="Whose door do you want to knock on?")
@app_commands.guild_only()
async def knock_knock(interaction: discord.Interaction, user: discord.Member):
    knocker = interaction.user
    guild_id = interaction.guild.id

    if not tot_is_active():
        await interaction.response.send_message("The Trick or Treat event isn't running right now.", ephemeral=True)
        return
    if user.id == knocker.id:
        await interaction.response.send_message("You can't knock on your own door!", ephemeral=True)
        return
    if user.bot:
        await interaction.response.send_message("Bots don't open doors, pick a person!", ephemeral=True)
        return

    now = time.time()
    left = KNOCK_COOLDOWN - (now - knock_last.get(knocker.id, 0))
    if left > 0:
        await interaction.response.send_message(f"Your knuckles need a rest! Try again in {int(left // 60)}m {int(left % 60) + 1}s.", ephemeral=True)
        return
    left = KNOCK_PAIR_COOLDOWN - (now - knock_pair_last.get((knocker.id, user.id), 0))
    if left > 0:
        await interaction.response.send_message(f"You already knocked on that door. Try again in {int(left // 60) + 1} minutes.", ephemeral=True)
        return
    if user.id in pending_knocks:
        await interaction.response.send_message("That door already has a visitor, wait a moment!", ephemeral=True)
        return

    ensure_treat_starter(guild_id, knocker.id)
    ensure_treat_starter(guild_id, user.id)

    knock_last[knocker.id] = now
    knock_pair_last[(knocker.id, user.id)] = now
    pending_knocks.add(user.id)
    schedule_reminder(guild_id, knocker.id, "knock", KNOCK_COOLDOWN)

    view = KnockView(knocker, user)
    embed = discord.Embed(
        title="Knock knock!",
        description=f"{knocker.mention} knocks on {user.mention}'s door...\n\n**Trick or treat?**",
        colour=PINK,
    )
    await interaction.response.send_message(
        content=user.mention, embed=embed, view=view,
        allowed_mentions=discord.AllowedMentions(users=[user]),
    )
    view.message = await interaction.original_response()

def treat_grid(names: list[str]) -> str:
    """Treat names in rows of three."""
    if not names: return "Nothing here"
    rows = [", ".join(names[i:i + 3]) for i in range(0, len(names), 3)]
    return ",\n".join(rows)

def build_buffs_embed() -> discord.Embed:
    lines = [f"**{name}**\n*{TREAT_INFO[key]}*" for key, name in TREATS]
    return discord.Embed(title="Treat Buffs", description="\n\n".join(lines)[:4000], colour=CREAM)

class InventoryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Treats Buffs", style=discord.ButtonStyle.primary, custom_id="inventory_buffs_button")
    async def buffs(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=build_buffs_embed(), ephemeral=True)

@client.tree.command(name="inventory", description="See your treat collection, or someone else's")
@app_commands.describe(user="Whose inventory to show (leave empty for your own)")
@app_commands.guild_only()
async def inventory(interaction: discord.Interaction, user: discord.Member | None = None):
    target = user or interaction.user
    if target.bot:
        await interaction.response.send_message("Bots don't collect treats.", ephemeral=True)
        return
    guild_id = interaction.guild.id
    ensure_treat_starter(guild_id, target.id)
    owned = db.get_treats(guild_id, target.id)

    owned_names = [f"{name} (x{owned[key]})" if owned[key] > 1 else name for key, name in TREATS if key in owned]
    missing_names = [name for key, name in TREATS if key not in owned]

    embed = discord.Embed(
        title=f"{target.display_name}'s Inventory",
        description=(
            f"**Treats Owned -**\n{treat_grid(owned_names)}\n\n"
            f"**Treats Missing -**\n{treat_grid(missing_names)}\n\n"
            f"**{len(owned)}/{len(TREAT_KEYS)}**\n"
            f"Karma {db.get_karma(guild_id, target.id)}"
        ),
        colour=CREAM,
    )
    await interaction.response.send_message(embed=embed, view=InventoryView())

search_group = app_commands.Group(name="search", description="Search the server", guild_only=True)

@search_group.command(name="treat", description="See who owns a treat")
@app_commands.describe(treat="Which treat to look for")
@app_commands.choices(treat=[app_commands.Choice(name=name, value=key) for key, name in TREATS])
async def search_treat(interaction: discord.Interaction, treat: str):
    guild = interaction.guild
    lines = []
    total = 0
    for user_id, copies in db.treat_holders(guild.id, treat):
        member = guild.get_member(user_id)
        if member is None: continue
        total += 1
        if len(lines) < 30:
            lines.append(f"**#{len(lines) + 1}** {member.display_name} | x{copies}")
    if total > len(lines):
        lines.append(f"...and {total - len(lines)} more")
    embed = discord.Embed(
        title=f"Who has {treat_name(treat)}?",
        description="\n".join(lines) if lines else "Nobody has this treat yet.",
        colour=PINK,
    )
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
    embed = discord.Embed(
        title="Treat Leaderboard",
        description="\n".join(lines) if lines else "Nobody has treats yet. Use /knock-knock!",
        colour=PINK,
    )
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
    embed = discord.Embed(
        title="Karma",
        description=f"Your karma: **{mine}**\n\n**Kindest critters:**\n{board}\n\n"
                    f"The kindest one gets the **{LOVELY_HORNET_ROLE}** role when the event ends.",
        colour=PINK,
    )
    await interaction.response.send_message(embed=embed)

@client.tree.command(name="tot_start", description="Start the Trick or Treat event (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def tot_start(interaction: discord.Interaction):
    if not is_server_admin(interaction):
        await interaction.response.send_message("Only the server creator or admins can do this.", ephemeral=True)
        return
    if tot_is_active():
        await interaction.response.send_message("The event is already running. Use /tot_end first.", ephemeral=True)
        return

    await interaction.response.defer()
    guild = interaction.guild
    db.reset_karma(guild.id)   # karma starts from zero, treats are kept
    for member in guild.members:
        if member.bot or db.tot_has_starter(guild.id, member.id): continue
        db.tot_give_starter(guild.id, member.id, random.choice(TREAT_KEYS))
    db.set_setting("tot_active", "1")
    pending_knocks.clear(); knock_last.clear(); knock_pair_last.clear(); trick_last.clear()

    embed = discord.Embed(
        title="Trick or Treat has started!",
        description=(
            f"Everyone who has no treat yet just got **one random treat** out of {len(TREAT_KEYS)}.\n\n"
            "Use **/knock-knock @user** to knock on someone's door. They choose **Treat** (you get a treat, "
            "sometimes one you already own) or **Trick** (50% chance to scare you and steal XP or Mups or Vups, and one of your treats).\n\n"
            "Check your collection and what every treat does with **/inventory**.\n"
            "Kind critters earn karma, and the kindest one gets a special role.\n"
            f"Collect all {len(TREAT_KEYS)} treats to become a **{HORNET_HALLOW_ROLE}**!"
        ),
        colour=PINK,
    )
    await interaction.followup.send(embed=embed)

@client.tree.command(name="tot_end", description="End the Trick or Treat event and hand out rewards (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def tot_end(interaction: discord.Interaction):
    if not is_server_admin(interaction):
        await interaction.response.send_message("Only the server creator or admins can do this.", ephemeral=True)
        return
    if not tot_is_active():
        await interaction.response.send_message("The event isn't running.", ephemeral=True)
        return

    await interaction.response.defer()
    guild = interaction.guild
    db.set_setting("tot_active", "0")
    pending_knocks.clear()

    # Everyone gets a few Mups for every different treat in their collection
    rewarded = 0
    completed = 0
    for user_id, count in db.treat_totals(guild.id):
        db.add_currency(guild.id, user_id, count * TREAT_END_REWARD_MUPS, 0)
        rewarded += 1
        if count >= len(TREAT_KEYS): completed += 1

    # The kindest critter gets the karma role
    winner_text = "Nobody earned karma this time."
    top = db.top_karma(guild.id, 1)
    if top:
        winner_id, winner_karma = top[0]
        winner = guild.get_member(winner_id)
        role = await get_role(guild, LOVELY_HORNET_ROLE)
        if winner is not None and role is not None:
            try:
                for holder in list(role.members):
                    if holder.id != winner.id:
                        await holder.remove_roles(role, reason="New kindest critter")
                await winner.add_roles(role, reason="Most karma in the Trick or Treat event")
                winner_text = f"The kindest critter is {winner.mention} with **{winner_karma} karma**! They got the **{role.name}** role."
            except (discord.Forbidden, discord.HTTPException):
                winner_text = f"The kindest critter is {winner.mention} with {winner_karma} karma, but I couldn't give the role. Please tell a mod."

    embed = discord.Embed(
        title="Trick or Treat is over!",
        description=(
            f"Thank you for playing! Everyone received **{TREAT_END_REWARD_MUPS} Mups per different treat** they collected "
            f"({rewarded} players).\n"
            f"Players who found all {len(TREAT_KEYS)} treats: **{completed}**.\n\n{winner_text}\n\n"
            "Your treats stay in /inventory and their buffs keep working."
        ),
        colour=PINK,
    )
    await interaction.followup.send(embed=embed)


# ======================================================================
#  Moderation: mute
# ======================================================================
@client.tree.command(name="reminders", description="Turn private cooldown reminders on or off")
@app_commands.describe(enabled="True: the bot sends you a private message when a cooldown ends. False: no messages.")
@app_commands.guild_only()
async def reminders(interaction: discord.Interaction, enabled: bool):
    db.set_flag(interaction.guild.id, interaction.user.id, "reminders_off", 0 if enabled else 1)
    text = ("Reminders are on. I will send you a private message when /daily, /knock-knock or /award_cookie is ready again."
            if enabled else "Reminders are off. I won't message you about cooldowns.")
    await interaction.response.send_message(embed=discord.Embed(description=text, colour=CREAM), ephemeral=True)

@client.tree.command(name="mute", description="Mute someone: removes their roles and gives Muted (mods only)")
@app_commands.describe(user="Who to mute", duration="10d = 10 days, 10h = 10 hours, 10m = 10 minutes", reason="Why they are muted")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
async def mute(interaction: discord.Interaction, user: discord.Member, duration: str, reason: str = "No reason given"):
    guild = interaction.guild
    mod = interaction.user
    if not is_moderator(mod):
        await interaction.response.send_message("Only moderators can do this.", ephemeral=True)
        return
    if user.bot or user.id == mod.id or user.id == guild.owner_id:
        await interaction.response.send_message("I can't mute that person.", ephemeral=True)
        return
    if user.top_role >= guild.me.top_role:
        await interaction.response.send_message("That person's role is higher than mine. Please move my role higher.", ephemeral=True)
        return
    muted_role = find_role(guild, MUTED_ROLE_NAME)
    if muted_role is None:
        await interaction.response.send_message(f"I can't find a role called {MUTED_ROLE_NAME}.", ephemeral=True)
        return
    seconds = parse_duration(duration)
    if seconds is None:
        await interaction.response.send_message("Use a duration like 10d, 10h or 10m (up to 365d).", ephemeral=True)
        return

    if "bat_wing_gummy" in db.get_treats(guild.id, user.id):
        seconds = max(60, int(seconds * 0.99))   # Bat Wing Gummy: 1% shorter

    await interaction.response.defer()

    existing = db.get_mute(guild.id, user.id)
    if existing:
        role_ids = existing[1]   # already stripped before, keep the original list
    else:
        removable = [r for r in user.roles if r != guild.default_role and r != muted_role
                     and not r.managed and r < guild.me.top_role]
        role_ids = ",".join(str(r.id) for r in removable)
        try:
            if removable: await user.remove_roles(*removable, reason=f"Muted by {mod}: {reason}")
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send("I couldn't remove their roles. Please check my permissions.", ephemeral=True)
            return
    try:
        await user.add_roles(muted_role, reason=f"Muted by {mod}: {reason}")
    except (discord.Forbidden, discord.HTTPException):
        await interaction.followup.send("I couldn't give the Muted role. Please check my permissions.", ephemeral=True)
        return

    until = int(time.time()) + seconds
    db.add_mute(guild.id, user.id, until, role_ids, reason, mod.id)
    length = describe_duration(seconds)

    try:
        await user.send(embed=discord.Embed(
            title="You were muted",
            description=f"Hi! Uh.. you were muted!\n\n**Duration is** - {length}\n{mod.mention} has muted you with reason:\n{reason}",
            colour=PINK,
        ))
    except (discord.Forbidden, discord.HTTPException):
        pass

    await interaction.followup.send(embed=discord.Embed(
        title="Muted",
        description=f"{user.mention} was muted for **{length}** by {mod.mention}.\nReason: {reason}",
        colour=PINK,
    ))

@client.tree.command(name="unmute", description="End a mute early and give the roles back (mods only)")
@app_commands.describe(user="Who to unmute")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
async def unmute(interaction: discord.Interaction, user: discord.Member):
    if not is_moderator(interaction.user):
        await interaction.response.send_message("Only moderators can do this.", ephemeral=True)
        return
    await interaction.response.defer()
    if await release_mute(interaction.guild, user.id):
        await interaction.followup.send(embed=discord.Embed(
            title="Unmuted", description=f"{user.mention} was unmuted and got their roles back.", colour=PINK))
    else:
        await interaction.followup.send("That person isn't muted by me.", ephemeral=True)


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
            if missing_mups: parts.append(f"{fmt_num(missing_mups)} more Mups")
            if missing_vups: parts.append(f"{fmt_num(missing_vups)} more Vups")
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
        back = gumdrop_refund(guild.id, member.id, item["mups"], item["vups"])
        if back: shop.notice += f"\n{back}"
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

        embed.add_field(name="Your Mups", value=fmt_num(data['mups']), inline=True)
        embed.add_field(name="Your Vups", value=fmt_num(data['vups']), inline=True)
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

# /give is now a group: /give mups, /give vups, /give lvl
give_group = app_commands.Group(
    name="give",
    description="Give Mups, Vups or levels (admins only)",
    default_permissions=discord.Permissions(administrator=True),
    guild_only=True,
)

def is_server_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.id == interaction.guild.owner_id or interaction.user.guild_permissions.administrator

@give_group.command(name="mups", description="Give Mups to a user")
@app_commands.describe(user="Who gets the Mups", amount="How many Mups")
async def give_mups(interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 1, 1000000]):
    if not is_server_admin(interaction):
        await interaction.response.send_message("Only the server creator or admins can do this.", ephemeral=True)
        return
    db.add_currency(interaction.guild.id, user.id, amount, 0)
    await interaction.response.send_message(f"Successfully gave {amount} Mups to {user.mention}!", ephemeral=True)

@give_group.command(name="vups", description="Give Vups to a user")
@app_commands.describe(user="Who gets the Vups", amount="How many Vups")
async def give_vups(interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 1, 1000000]):
    if not is_server_admin(interaction):
        await interaction.response.send_message("Only the server creator or admins can do this.", ephemeral=True)
        return
    db.add_currency(interaction.guild.id, user.id, 0, amount)
    await interaction.response.send_message(f"Successfully gave {amount} Vups to {user.mention}!", ephemeral=True)

@give_group.command(name="lvl", description="Give levels to a user")
@app_commands.describe(user="Who gets the levels", amount="How many levels (default 1)")
async def give_lvl(interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 1, 200] = 1):
    if not is_server_admin(interaction):
        await interaction.response.send_message("Only the server creator or admins can do this.", ephemeral=True)
        return
    if user.bot:
        await interaction.response.send_message("Bots don't have levels.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)   # giving roles can take a moment

    guild_id = interaction.guild.id
    old_level = level_from_xp(db.get(guild_id, user.id)["xp"])[0]
    # Add exactly the XP that `amount` levels cost, so the progress inside the level stays the same
    db.add_xp(guild_id, user.id, xp_for_level(old_level + amount) - xp_for_level(old_level))
    new_level = old_level + amount

    granted = await sync_level_roles(user, new_level)
    text = f"Gave {amount} level{'s' if amount != 1 else ''} to {user.mention}: level {old_level} -> level {new_level}."
    if granted:
        text += " Roles unlocked: " + ", ".join(f"**{role.name}**" for role in granted)
    await interaction.followup.send(text, ephemeral=True)

client.tree.add_command(give_group)


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

    # Treat buffs for pats: Spooky Lollipop (the patted person) and Chocolate Pawprint (the one who pats)
    if name == "pat":
        guild_id = interaction.guild.id
        bonus = 0
        if "spooky_lollipop" in db.get_treats(guild_id, target.id): bonus += 1
        if "chocolate_pawprint" in db.get_treats(guild_id, giver.id): bonus += 1
        if bonus:
            await give_xp(target, bonus)

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
