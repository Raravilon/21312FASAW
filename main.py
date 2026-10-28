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

# Role that members get after verification (created automatically if missing)
VERIFIED_ROLE_NAME = os.getenv("VERIFIED_ROLE_NAME", "Silly Critter")

PINK = discord.Colour.from_str("#f4b6c2")
CREAM = discord.Colour.from_str("#f7e7d3")

# Needs "Server Members Intent" enabled in the Discord Developer Portal
intents = discord.Intents.default()
intents.members = True


# ---------- Verification button ----------
class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # persistent: works after bot restarts

    @discord.ui.button(
        label="I'm a silly critter! 🐾",
        style=discord.ButtonStyle.success,
        custom_id="silly_verify_button",
    )
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user

        role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME)
        if role is None:
            try:
                role = await guild.create_role(
                    name=VERIFIED_ROLE_NAME,
                    colour=PINK,
                    reason="Verification role for Silly Critters",
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "Oopsie! I can't make the role... please tell a mod! 🙀",
                    ephemeral=True,
                )
                return

        if role in member.roles:
            await interaction.response.send_message(
                "You're already a verified silly critter! Go play! 🐱",
                ephemeral=True,
            )
            return

        try:
            await member.add_roles(role, reason="Passed verification")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Oopsie! I can't give you the role... tell a mod to move my role higher! 🙀",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Yay, you're in! 🎉 Welcome to the silly critters club! "
            "Go say hi and have fun! 🐾✨",
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
        title="🐾 A new silly critter appeared!",
        description=(
            f"Haii {member.mention}!! Welcome to **{member.guild.name}**! 💖\n\n"
            f"Hop over to {verify_text} and press the button to get your "
            f"critter badge ✨\n"
            f"Then grab a snack, say hi, and be silly with us! 🍪🐱"
        ),
        colour=PINK,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"You are critter #{member.guild.member_count} 🐾")
    return embed


@client.event
async def on_member_join(member: discord.Member):
    if member.guild.id != GUILD_ID or member.bot:
        return
    channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if channel is None:
        return
    await channel.send(content=member.mention, embed=build_welcome_embed(member))


# ---------- Commands ----------
@client.tree.command(name="ping", description="Check if the critter is awake")
async def ping(interaction: discord.Interaction):
    latency = round(client.latency * 1000)
    await interaction.response.send_message(f"Pong! 🏓 {latency} ms, still awake~")


@client.tree.command(name="hello", description="Say hi to the bot")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"Haii {interaction.user.mention}! 🐾💖")


@client.tree.command(name="setup_verify", description="Post the verification message (admins only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_verify(interaction: discord.Interaction):
    channel = interaction.guild.get_channel(VERIFY_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message("I can't find the verify channel! 🙀", ephemeral=True)
        return

    embed = discord.Embed(
        title="✨ Welcome, little critter! ✨",
        description=(
            "Before you can play with everyone, please read the rules "
            "and promise to be kind and silly. 💖\n\n"
            "Ready? Press the button below to get your critter badge! 🐾"
        ),
        colour=CREAM,
    )
    await channel.send(embed=embed, view=VerifyView())
    await interaction.response.send_message(f"Verification posted in {channel.mention}! 🎉", ephemeral=True)


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
