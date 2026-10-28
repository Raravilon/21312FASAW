import os

import discord
from discord import app_commands

# ---------- Settings ----------
# Token comes from the DISCORD_TOKEN environment variable (set it in BotHost)
TOKEN = os.getenv("DISCORD_TOKEN")

# IDs taken from your server links (can be overridden with env variables)
GUILD_ID = int(os.getenv("GUILD_ID", "1564401949019869224"))
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "1564405099004956702"))
VERIFY_CHANNEL_ID = int(os.getenv("VERIFY_CHANNEL_ID", "1564405761105461379"))

# Roles (looked up by name; created automatically if missing)
JOINED_ROLE_NAME = os.getenv("JOINED_ROLE_NAME", "Just joined!")
NEWCOMER_ROLE_NAME = os.getenv("NEWCOMER_ROLE_NAME", "Newcomer :3")

PINK = discord.Colour.from_str("#f4b6c2")
CREAM = discord.Colour.from_str("#f7e7d3")

# Needs "Server Members Intent" enabled in the Discord Developer Portal
intents = discord.Intents.default()
intents.members = True


# ---------- Helpers ----------
async def get_role(guild: discord.Guild, name: str) -> discord.Role | None:
    role = discord.utils.get(guild.roles, name=name)
    if role is None:
        try:
            role = await guild.create_role(name=name, reason="Created by Mupaw bot")
        except discord.Forbidden:
            return None
    return role


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
        joined_role = discord.utils.get(guild.roles, name=JOINED_ROLE_NAME)

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

        newcomer_role = discord.utils.get(guild.roles, name=NEWCOMER_ROLE_NAME)
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
        # Sync commands to your server (shows up instantly)
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self):
        print(f"Bot is online as {self.user}")


client = SillyBot()


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


# ---------- Commands ----------
@client.tree.command(name="ping", description="Check if the bot is awake")
async def ping(interaction: discord.Interaction):
    latency = round(client.latency * 1000)
    await interaction.response.send_message(f"Pong! {latency} ms")


@client.tree.command(name="hello", description="Say hi to the bot")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"Haii {interaction.user.mention}!")


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
