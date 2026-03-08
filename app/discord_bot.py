from __future__ import annotations

import os
from collections import defaultdict

import discord

from app.services.orchestrator import dispatch_grouped_by_channel, handle_discord_message, mark_dispatched


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

    if message.channel.name.lower() == "orchestrator" and _is_dispatch_command(message.content):
        await _dispatch_to_channels(message)
        return

    response = handle_discord_message(
        channel_name=message.channel.name,
        author_name=message.author.display_name,
        content=message.content,
    )
    if response:
        await message.channel.send(response)


async def _dispatch_to_channels(message: discord.Message) -> None:
    grouped = dispatch_grouped_by_channel()
    if not grouped:
        await message.channel.send("Není co dispatchovat. Schval nejdřív položky přes approve / web triage.")
        return

    sent_ids: list[str] = []
    sent_channels = 0
    missing_channels: list[str] = []

    for channel_name, proposals in grouped.items():
        target = discord.utils.get(message.guild.text_channels, name=channel_name)
        if target is None:
            missing_channels.append(channel_name)
            continue

        lines = ["Nové úkoly k řešení:"]
        grouped_bundles: dict[str, list] = defaultdict(list)
        for item in proposals:
            key = item.bundle_key or item.id
            grouped_bundles[key].append(item)

        bundle_items = list(grouped_bundles.values())[:20]
        for bucket in bundle_items:
            item = bucket[0]
            count = len(bucket)
            bundle = item.bundle_label or item.bundle_key or "bez-bundle"
            lines.append(
                f"- `{item.id[:8]}` | {item.role} | P{item.priority} | {item.sender} | {bundle} | zpráv: {count}"
            )
            sent_ids.extend([p.id for p in bucket])

        await target.send("\n".join(lines))
        sent_channels += 1

    if sent_ids:
        mark_dispatched(sent_ids)

    summary = [f"Dispatch hotov. Odesláno do kanálů: {sent_channels}. Položek: {len(sent_ids)}."]
    if missing_channels:
        summary.append(f"Nenalezené kanály: {', '.join(sorted(set(missing_channels)))}")
    await message.channel.send("\n".join(summary))


def _is_dispatch_command(content: str) -> bool:
    text = content.strip().lower()
    if not text:
        return False
    cmd = text.split()[0].lstrip("!/")
    return cmd == "dispatch"


def main() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not configured")
    client.run(token)


if __name__ == "__main__":
    main()
