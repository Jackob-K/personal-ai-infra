from __future__ import annotations

import os

import discord

from app.services.orchestrator import handle_discord_message


def build_client() -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.messages = True
    return discord.Client(intents=intents)


client = build_client()


@client.event
async def on_ready() -> None:
    print(f"Discord bot connected as {client.user}")


@client.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    if not message.guild:
        return

    response = handle_discord_message(
        channel_name=message.channel.name,
        author_name=message.author.display_name,
        content=message.content,
    )
    if response:
        await message.channel.send(response)


def main() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not configured")
    client.run(token)


if __name__ == "__main__":
    main()
