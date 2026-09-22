#!/usr/bin/env python3
"""Outbound Discord Gateway adapter for one administrator-bound Canvas account."""
import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import discord
from discord import app_commands

import brand
from cc_account import AccountService, COMMANDS, _SwitchFailure
from cc_telegram import _neutralize, _split_utf16_units


class DiscordBot(discord.Client):
    """Accept only the bound user's native commands and components in this bot's DM."""

    def __init__(self, service):
        if service.secrets.discord_user_id is None:
            raise ValueError("Discord identity is not configured")
        super().__init__(intents=discord.Intents.none(), allowed_mentions=discord.AllowedMentions.none())
        self.service = service
        self.identity = service.secrets.discord_user_id
        self.tree = app_commands.CommandTree(self)
        self._schedule_lock = asyncio.Lock()
        group = app_commands.Group(
            name="canvas", description="Canvas 账号日报与提醒管理",
            allowed_contexts=app_commands.AppCommandContext(guild=False, dm_channel=True, private_channel=False),
            allowed_installs=app_commands.AppInstallationType(guild=True, user=False),
        )

        def handler(name):
            async def command(interaction: discord.Interaction):
                await self.handle_command(interaction, name, [])
            return command

        for name in ("help", "status", "report", "refresh", "on", "off", "courses"):
            group.command(name=name, description=COMMANDS[name][:100])(handler(name))

        async def tasks(interaction: discord.Interaction, stopped: bool = False):
            await self.handle_command(interaction, "tasks", ["stopped"] if stopped else [])

        async def schedule(interaction: discord.Interaction, time: str, timezone: str):
            await self.handle_command(interaction, "schedule", [time, timezone])

        group.command(name="tasks", description=COMMANDS["tasks"][:100])(tasks)
        group.command(name="schedule", description=COMMANDS["schedule"][:100])(schedule)
        self.tree.add_command(group)
        self._schedule_task = None
        self._delivery_lock = asyncio.Lock()
        self._dm_unreachable = False

    async def setup_hook(self):
        # Global commands are restricted to the bot DM context; guild commands cannot run in DMs.
        await self.tree.sync()
        self._schedule_task = asyncio.create_task(self._schedule_loop())

    async def on_ready(self):
        await asyncio.to_thread(self.service.channel_status, "discord", not self._dm_unreachable)

    async def on_disconnect(self):
        await asyncio.to_thread(self.service.channel_status, "discord", False)

    def _authorized(self, interaction):
        return (interaction.guild_id is None
                and interaction.context.dm_channel
                and not interaction.context.guild
                and not interaction.context.private_channel
                and interaction.user.id == self.identity
                and not interaction.user.bot
                and interaction.application_id == self.application_id
                and interaction.channel is not None
                and interaction.channel.type == discord.ChannelType.private)

    @staticmethod
    def _view(actions):
        if not actions:
            return None
        view = discord.ui.View(timeout=None)
        buttons = [button for row in actions for button in row]
        for index, button in enumerate(buttons[:25]):
            view.add_item(discord.ui.Button(label=_neutralize(button["text"])[:80],
                                            custom_id=button["data"], row=index // 5))
        return view

    async def _reply(self, interaction, result, generation=None):
        if result.get("stale"):
            return False
        text = (result["text"].replace("/canvas tasks stopped", "/canvas tasks stopped:true")
                .replace("/canvas schedule HH:MM Area/City", "/canvas schedule time:HH:MM timezone:Area/City"))
        # Discord content is Markdown; escape before splitting to keep every segment inert.
        escaped = discord.utils.escape_markdown(_neutralize(text))
        segments = _split_utf16_units(escaped, 1800)
        if not segments:
            return False
        for index, segment in enumerate(segments):
            try:
                await asyncio.to_thread(self.service._authorized, self.identity, "discord")
            except ValueError:
                return False
            if generation is not None and not self.service.permitted(generation):
                return False
            try:
                await interaction.followup.send(
                    segment, view=self._view(result.get("actions")) if index == len(segments) - 1 else None,
                    allowed_mentions=discord.AllowedMentions.none(), suppress_embeds=True)
            except Exception:
                self._dm_unreachable = True
                await asyncio.to_thread(self.service.channel_status, "discord", False)
                raise
        self._dm_unreachable = False
        await asyncio.to_thread(self.service.channel_status, "discord", True)
        self.service.acknowledge_delivery(result)
        return True

    async def handle_command(self, interaction, command, params):
        if not self._authorized(interaction):
            await interaction.response.send_message("仅限授权私聊使用。", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        generation = self.service.generation()
        try:
            options = {"expected_generation": generation} if command in ("on", "refresh") else {}
            result = await asyncio.to_thread(self.service.execute, self.identity, command, params,
                                             "discord", str(interaction.id), **options)
            if command in ("refresh", "report") and self.service.permitted(generation):
                async with self._delivery_lock:
                    result = await asyncio.to_thread(self.service.execute, self.identity, "report", [],
                                                     "discord", str(interaction.id))
                    sent = await self._reply(interaction, result, generation if command == "refresh" else None)
            else:
                if command == "on":
                    generation = result.get("generation", generation)
                sent = await self._reply(interaction, result, generation if command == "on" else None)
            if command == "on" and result.get("resume_delivery") and sent:
                await self._scheduled_once()
        except _SwitchFailure as failure:
            await self._failure(interaction, failure.generation)
        except Exception:
            await self._failure(interaction, generation)

    async def _failure(self, interaction, generation):
        if self.service.permitted(generation):
            try:
                await asyncio.to_thread(self.service._authorized, self.identity, "discord")
                await interaction.followup.send("操作失败，请稍后重试。", allowed_mentions=discord.AllowedMentions.none())
            except Exception:
                pass

    async def on_interaction(self, interaction):
        if interaction.type != discord.InteractionType.component:
            return
        if not self._authorized(interaction):
            await interaction.response.send_message("仅限授权私聊使用。", ephemeral=True)
            return
        if not interaction.message or interaction.message.author.id != self.user.id:
            return
        data = interaction.data.get("custom_id") if isinstance(interaction.data, dict) else None
        if not isinstance(data, str):
            return
        await interaction.response.defer(thinking=True)
        try:
            if data == "schedule:help":
                result = await asyncio.to_thread(self.service.execute, self.identity, "schedule", [],
                                                 "discord", str(interaction.id))
            elif data.startswith("course:"):
                result = await asyncio.to_thread(self.service.course_action, self.identity, data, "discord")
            else:
                result = await asyncio.to_thread(self.service.task_action, self.identity, data, "discord")
            await self._reply(interaction, result, result.get("generation"))
        except Exception:
            await self._failure(interaction, self.service.generation())

    async def _scheduled_once(self):
        if not self.is_ready():
            return
        async with self._schedule_lock:
            try:
                await asyncio.to_thread(self.service.channel_status, "discord", not self._dm_unreachable)
                due = await asyncio.to_thread(self.service.scheduled_tick, "discord")
                if due is None:
                    return
                await asyncio.to_thread(self.service._authorized, self.identity, "discord")
                user = await self.fetch_user(self.identity)
                channel = await user.create_dm()
                if not isinstance(channel, discord.DMChannel) or channel.recipient.id != self.identity:
                    raise ValueError("Discord DM not available")
                text = discord.utils.escape_markdown(_neutralize(due["text"]))
                for segment in _split_utf16_units(text, 1800):
                    await asyncio.to_thread(self.service._authorized, self.identity, "discord")
                    if not self.service.permitted(due["generation"]):
                        return
                    await channel.send(segment, allowed_mentions=discord.AllowedMentions.none(), suppress_embeds=True)
                self._dm_unreachable = False
                await asyncio.to_thread(self.service.channel_status, "discord", True)
                self.service.scheduled_delivery(due["day"], True, due["generation"], "discord")
            except Exception:
                if "due" in locals() and due:
                    self.service.scheduled_delivery(due["day"], False, due["generation"], "discord")
                self._dm_unreachable = True
                await asyncio.to_thread(self.service.channel_status, "discord", False)

    async def _schedule_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            await self._scheduled_once()
            await asyncio.sleep(10)


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=f"{brand.NAME} Discord Adapter")
    parser.add_argument("--home", required=True)
    parser.add_argument("--secrets", required=True)
    args = parser.parse_args(argv)
    if not os.path.isabs(args.home) or not os.path.isabs(args.secrets):
        parser.error("参数路径必须为绝对路径")
    try:
        service = AccountService(args.home, args.secrets)
        if not service.secrets.discord_bot_token:
            raise ValueError("Discord Bot token 未配置")
        bot = DiscordBot(service)
        bot.run(service.secrets.discord_bot_token, log_handler=None)
    except KeyboardInterrupt:
        return
    except Exception:
        sys.stderr.write("Discord 服务启动失败；检查应用安装、私信权限和配置。\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
