from __future__ import annotations

import os
from collections import defaultdict

import discord
from discord.ext import tasks

from app.services.orchestrator import dispatch_grouped_by_channel, handle_discord_message, mark_dispatched
from app.services.agent_registry import load_agent_registry
from app.services.proposal_store import list_pending_discord_notifications, mark_discord_notified


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
    if _auto_dispatch_enabled() and not auto_dispatch_loop.is_running():
        auto_dispatch_loop.change_interval(seconds=_auto_dispatch_interval_seconds())
        auto_dispatch_loop.start()


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


@tasks.loop(seconds=30)
async def auto_dispatch_loop() -> None:
    grouped = _pending_notifications_by_channel()
    if not grouped:
        return

    guild = _resolve_target_guild()
    if guild is None:
        return

    sent_ids: list[str] = []
    for channel_name, proposals in grouped.items():
        target = discord.utils.get(guild.text_channels, name=channel_name)
        if target is None:
            continue
        await target.send(_format_auto_dispatch_message(proposals))
        sent_ids.extend([item.id for item in proposals])

    if sent_ids:
        mark_discord_notified(sent_ids)


@auto_dispatch_loop.before_loop
async def before_auto_dispatch_loop() -> None:
    await client.wait_until_ready()


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


def _pending_notifications_by_channel() -> dict[str, list]:
    grouped: dict[str, list] = defaultdict(list)
    for item in list_pending_discord_notifications():
        registry_channel = _channel_for_role(item.role)
        if not registry_channel:
            continue
        grouped[registry_channel].append(item)
    return grouped


def _format_auto_dispatch_message(proposals: list) -> str:
    lines = ["Nové emailové návrhy:"]
    grouped_bundles: dict[str, list] = defaultdict(list)
    for item in proposals:
        key = item.bundle_key or item.id
        grouped_bundles[key].append(item)

    for bucket in list(grouped_bundles.values())[:20]:
        item = bucket[0]
        count = len(bucket)
        preview = (item.subject or item.summary or "").replace("\n", " ").strip()[:120]
        lines.append(
            f"- `{item.id[:8]}` | {item.role} | P{item.priority} | {item.sender} | zpráv: {count} | {preview}"
        )
    return "\n".join(lines)


def _resolve_target_guild() -> discord.Guild | None:
    configured_name = str(load_agent_registry().get("guild_name", "")).strip()
    if configured_name:
        match = discord.utils.get(client.guilds, name=configured_name)
        if match is not None:
            return match
    return client.guilds[0] if client.guilds else None


def _channel_for_role(role: str) -> str | None:
    registry = load_agent_registry()
    for channel in registry.get("channels", []):
        if str(channel.get("role", "")).upper().strip() == role.upper().strip():
            return str(channel.get("channel_name", "")).strip() or None
    return None


def _auto_dispatch_enabled() -> bool:
    return os.getenv("DISCORD_AUTO_DISPATCH_ENABLED", "true").strip().lower() not in {"0", "false", "no"}


def _auto_dispatch_interval_seconds() -> int:
    raw = os.getenv("DISCORD_AUTO_DISPATCH_INTERVAL_SECONDS", "30").strip()
    try:
        return max(10, min(600, int(raw)))
    except ValueError:
        return 30


if __name__ == "__main__":
    main()
