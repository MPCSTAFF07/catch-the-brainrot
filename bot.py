import asyncio
import logging
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

import discord
from discord import app_commands
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("reaction_roles")
MESSAGE_PATH = re.compile(r"/channels/([0-9]{17,20})/([0-9]{17,20})/([0-9]{17,20})")
CUSTOM_EMOJI = re.compile(r"<a?:[A-Za-z0-9_]+:([0-9]{17,20})>")


def parse_message_link(link: str, guild_id: int) -> tuple[int, int]:
    url = urlsplit(link.strip().strip("<>"))
    match = MESSAGE_PATH.fullmatch(url.path)
    if url.scheme != "https" or url.netloc not in {
        "discord.com", "ptb.discord.com", "canary.discord.com",
    } or match is None or int(match[1]) != guild_id:
        raise ValueError("このサーバー内のDiscordメッセージリンクを指定してください。")
    return int(match[2]), int(match[3])


def emoji_key(emoji: str | discord.PartialEmoji) -> str:
    if isinstance(emoji, discord.PartialEmoji):
        if emoji.id is not None:
            return f"custom:{emoji.id}"
        emoji = emoji.name
    match = CUSTOM_EMOJI.fullmatch(emoji)
    if match:
        return f"custom:{match[1]}"
    if not emoji or len(emoji) > 40 or not any(ord(char) > 127 for char in emoji):
        raise ValueError("Discordで使える絵文字を1つ指定してください。")
    return "unicode:" + emoji.replace("\ufe0f", "")


class ReactionRoleBot(discord.Client):
    def __init__(self, guild_id: int, database: sqlite3.Connection):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_reactions = True
        super().__init__(intents=intents)
        self.guild_id = guild_id
        self.database = database
        self.tree = app_commands.CommandTree(self)
        self.locks = [asyncio.Lock() for _ in range(64)]
        database.execute("""
            CREATE TABLE IF NOT EXISTS reaction_roles (
                channel_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                emoji_key TEXT NOT NULL,
                emoji_label TEXT NOT NULL,
                role_id TEXT NOT NULL UNIQUE,
                PRIMARY KEY (channel_id, message_id, emoji_key)
            )
        """)
        database.commit()

    def message_lock(self, message_id: int) -> asyncio.Lock:
        return self.locks[message_id % len(self.locks)]

    async def setup_hook(self) -> None:
        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self) -> None:
        LOG.info("ログイン完了: %s / サーバーID %d", self.user, self.guild_id)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self.change_role(payload, add=True)

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self.change_role(payload, add=False)

    async def change_role(self, payload: discord.RawReactionActionEvent, *, add: bool) -> None:
        if payload.guild_id != self.guild_id or payload.burst:
            return
        if self.user is None or payload.user_id == self.user.id:
            return

        async with self.message_lock(payload.message_id):
            binding = self.database.execute(
                "SELECT role_id FROM reaction_roles WHERE channel_id=? AND message_id=? AND emoji_key=?",
                (str(payload.channel_id), str(payload.message_id), emoji_key(payload.emoji)),
            ).fetchone()
            if binding is None:
                return

            guild = self.get_guild(self.guild_id)
            if guild is None:
                return
            role = guild.get_role(int(binding[0]))
            if role is None or not role.is_assignable():
                LOG.warning("ロールを操作できません: role_id=%s", binding[0])
                return

            try:
                member = payload.member or await guild.fetch_member(payload.user_id)
                if member.bot:
                    return
                if add:
                    await member.add_roles(role, reason="リアクションロール")
                else:
                    await member.remove_roles(role, reason="リアクションロール")
            except discord.NotFound:
                return
            except discord.HTTPException:
                LOG.exception("ロール操作失敗: member_id=%s role_id=%s", payload.user_id, role.id)


@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
class ReactionRoleCommands(app_commands.Group, name="reaction-role", description="リアクションロールを管理"):
    def __init__(self, bot: ReactionRoleBot):
        super().__init__()
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        allowed = (
            interaction.guild_id == self.bot.guild_id and interaction.permissions.administrator
        )
        if not allowed:
            await interaction.response.send_message("管理者のみ操作できます。", ephemeral=True)
        return allowed

    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        LOG.error("コマンド実行失敗", exc_info=error)
        message = "処理に失敗しました。Botのログを確認してください。"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="add", description="既存メッセージにリアクションとロールを登録")
    @app_commands.describe(message_link="対象のメッセージリンク", emoji="通常絵文字またはカスタム絵文字", role="付与するロール")
    async def add(
        self, interaction: discord.Interaction, message_link: str, emoji: str, role: discord.Role
    ) -> None:
        try:
            channel_id, message_id = parse_message_link(message_link, self.bot.guild_id)
            emoji = emoji.strip()
            key = emoji_key(emoji)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        if not role.is_assignable():
            await interaction.response.send_message(
                "Botに「ロールの管理」権限を付け、Botのロールを対象ロールより上に置いてください。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.message_lock(message_id):
            values = (str(channel_id), str(message_id), key, emoji, str(role.id))
            try:
                with self.bot.database:
                    self.bot.database.execute("INSERT INTO reaction_roles VALUES (?, ?, ?, ?, ?)", values)
            except sqlite3.IntegrityError:
                await interaction.followup.send(
                    "このメッセージの絵文字、またはこのロールは登録済みです。", ephemeral=True
                )
                return

            seeded = False
            try:
                channel = self.bot.get_partial_messageable(channel_id, guild_id=self.bot.guild_id)
                message = await channel.fetch_message(message_id)
                await message.add_reaction(emoji)
                seeded = True
            except discord.NotFound:
                await interaction.followup.send(
                    "メッセージが見つからないか、Botがその絵文字を使えません。", ephemeral=True
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    "Botに対象チャンネルの閲覧・履歴閲覧・リアクション追加権限を付けてください。",
                    ephemeral=True,
                )
            except discord.HTTPException:
                await interaction.followup.send(
                    "絵文字を付けられませんでした。Discordで使える絵文字を1つ入力してください。",
                    ephemeral=True,
                )
            finally:
                if not seeded:
                    with self.bot.database:
                        self.bot.database.execute(
                            "DELETE FROM reaction_roles WHERE channel_id=? AND message_id=? AND emoji_key=?",
                            values[:3],
                        )
        if seeded:
            await interaction.followup.send(f"{emoji} → {role.mention} を登録しました。", ephemeral=True)

    @app_commands.command(name="remove", description="登録を解除する（付与済みのロールは維持）")
    @app_commands.describe(role="登録を解除するロール")
    async def remove(self, interaction: discord.Interaction, role: discord.Role) -> None:
        role_id = str(role.id)
        binding = self.bot.database.execute(
            "SELECT message_id FROM reaction_roles WHERE role_id=?", (role_id,)
        ).fetchone()
        if binding is None:
            await interaction.response.send_message("そのロールは登録されていません。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.message_lock(int(binding[0])):
            with self.bot.database:
                result = self.bot.database.execute(
                    "DELETE FROM reaction_roles WHERE role_id=? AND message_id=?", (role_id, binding[0])
                )
        message = "登録を解除しました。付与済みのロールはそのままです。"
        if not result.rowcount:
            message = "登録内容はすでに変更されています。"
        await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(name="list", description="登録中のリアクションロールを表示")
    async def list_rules(self, interaction: discord.Interaction) -> None:
        rows = self.bot.database.execute(
            "SELECT channel_id, message_id, emoji_label, role_id FROM reaction_roles ORDER BY channel_id, message_id"
        ).fetchall()
        if not rows:
            await interaction.response.send_message("登録はまだありません。", ephemeral=True)
            return

        pages: list[str] = []
        page = ""
        for channel_id, message_id, emoji, role_id in rows:
            url = f"https://discord.com/channels/{self.bot.guild_id}/{channel_id}/{message_id}"
            line = f"{emoji} → <@&{role_id}> · [メッセージ]({url})\n"
            if len(page) + len(line) > 1800:
                pages.append(page)
                page = ""
            page += line
        pages.append(page)

        await interaction.response.send_message(pages[0], ephemeral=True)
        for page in pages[1:]:
            await interaction.followup.send(page, ephemeral=True)


def main() -> None:
    load_dotenv(ROOT / ".env")
    token = os.getenv("DISCORD_TOKEN", "").strip()
    guild_id = os.getenv("DISCORD_GUILD_ID", "").strip()
    if not token or not re.fullmatch(r"[0-9]{17,20}", guild_id):
        raise SystemExit(".envに DISCORD_TOKEN と DISCORD_GUILD_ID を設定してください。")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    database = sqlite3.connect(ROOT / "reaction_roles.sqlite3")
    try:
        bot = ReactionRoleBot(int(guild_id), database)
        bot.tree.add_command(ReactionRoleCommands(bot))
        bot.run(token, log_handler=None)
    finally:
        database.close()


if __name__ == "__main__":
    main()
