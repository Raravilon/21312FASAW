import os

import discord
from discord import app_commands

# Токен берётся из переменной окружения DISCORD_TOKEN (задаётся в BotHost)
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@client.event
async def on_ready():
    await tree.sync()  # регистрирует слеш-команды
    print(f"Бот запущен как {client.user}")


@tree.command(name="ping", description="Проверить, жив ли бот")
async def ping(interaction: discord.Interaction):
    latency = round(client.latency * 1000)
    await interaction.response.send_message(f"Pong! {latency} мс")


@tree.command(name="hello", description="Поздороваться с ботом")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"Привет, {interaction.user.mention}!")


if not TOKEN:
    raise SystemExit("Не задана переменная окружения DISCORD_TOKEN")

client.run(TOKEN)
