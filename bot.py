import asyncio
import logging
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

import discord
from discord import app_commands
from discord.ext import tasks
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


def emoji_key(emoji: str | discord.Emoji | discord.PartialEmoji) -> str:
    if isinstance(emoji, (discord.Emoji, discord.PartialEmoji)):
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
        intents.emojis_and_stickers = True
        super().__init__(intents=intents)
        self.guild_id = guild_id
        self.database = database
        self.tree = app_commands.CommandTree(self)
        self.locks = [asyncio.Lock() for _ in range(64)]
        self.member_role_locks = [asyncio.Lock() for _ in range(64)]
        self.counter_lock = asyncio.Lock()
        self.counter_error = ""
        database.execute("""
            CREATE TABLE IF NOT EXISTS reaction_roles (
                channel_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                emoji_key TEXT NOT NULL,
                emoji_label TEXT NOT NULL,
                role_id TEXT NOT NULL UNIQUE,
                remove_role_id TEXT,
                PRIMARY KEY (channel_id, message_id, emoji_key)
            )
        """)
        columns = {column[1] for column in database.execute("PRAGMA table_info(reaction_roles)")}
        if "remove_role_id" not in columns:
            database.execute("ALTER TABLE reaction_roles ADD COLUMN remove_role_id TEXT")
        database.execute("""
            CREATE TABLE IF NOT EXISTS member_counter (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                channel_id TEXT NOT NULL,
                last_count INTEGER
            )
        """)
        database.execute("""
            CREATE TABLE IF NOT EXISTS trade_categories (
                id INTEGER PRIMARY KEY CHECK (id=1),
                active_id TEXT NOT NULL,
                completed_id TEXT NOT NULL
            )
        """)
        database.commit()

    def trade_categories(self):
        row = self.database.execute("SELECT active_id, completed_id FROM trade_categories WHERE id=1").fetchone()
        return (row[0], row[1]) if row else ("", "")

    def save_trade_categories(self, active_id, completed_id):
        with self.database:
            self.database.execute(
                """INSERT INTO trade_categories VALUES (1, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET active_id=excluded.active_id, completed_id=excluded.completed_id""",
                (active_id, completed_id),
            )

    def counter_state(self, guild):
        saved = self.database.execute(
            "SELECT channel_id, last_count FROM member_counter WHERE id=1"
        ).fetchone()
        channels = []
        if guild is not None and guild.me is not None:
            for channel in guild.voice_channels:
                permissions = channel.permissions_for(guild.me)
                channels.append({
                    "id": str(channel.id),
                    "name": channel.name,
                    "available": permissions.manage_channels,
                })
        return {
            "channel_id": saved[0] if saved else "",
            "last_count": saved[1] if saved else None,
            "channels": channels,
            "error": self.counter_error,
        }

    async def configure_counter(self, guild, channel_id):
        async with self.counter_lock:
            if not channel_id:
                with self.database:
                    self.database.execute("DELETE FROM member_counter WHERE id=1")
                self.counter_error = ""
                return
            channel = guild.get_channel(int(channel_id)) if guild and re.fullmatch(r"[0-9]{17,20}", channel_id) else None
            if not isinstance(channel, discord.VoiceChannel) or guild.me is None:
                raise ValueError("このサーバーのボイスチャンネルを選んでください。")
            if not channel.permissions_for(guild.me).manage_channels:
                raise ValueError("Botに対象チャンネルの「チャンネルの管理」権限を付けてください。")
            with self.database:
                self.database.execute(
                    """INSERT INTO member_counter(id, channel_id) VALUES (1, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         last_count = CASE WHEN channel_id = excluded.channel_id THEN last_count END,
                         channel_id = excluded.channel_id""",
                    (channel_id,),
                )
            await self.refresh_member_counter(guild, channel, channel_id)

    async def refresh_member_counter(self, guild, channel, channel_id):
        try:
            snapshot = await self.fetch_guild(guild.id, with_counts=True)
            count = snapshot.approximate_member_count
            if count is None:
                self.counter_error = "Discordから人数を取得できませんでした。次回の更新で再試行します。"
                return
            desired_name = f"👥 MEMBERS: {count}"
            if channel.name != desired_name:
                await channel.edit(name=desired_name, reason="メンバー数の自動表示")
            with self.database:
                self.database.execute("UPDATE member_counter SET last_count=? WHERE id=1 AND channel_id=?", (count, channel_id))
            self.counter_error = ""
        except discord.Forbidden:
            self.counter_error = "Botに対象チャンネルの「チャンネルの管理」権限を付けてください。"
        except discord.HTTPException as error:
            self.counter_error = "Discordとの通信に失敗しました。次回の更新で再試行します。"
            LOG.warning("メンバー数の更新に失敗: status=%s", error.status)

    @tasks.loop(minutes=5)
    async def member_counter_loop(self):
        if not self.is_ready():
            return
        async with self.counter_lock:
            saved = self.database.execute("SELECT channel_id FROM member_counter WHERE id=1").fetchone()
            if not saved:
                return
            channel_id = saved[0]
            guild = self.get_guild(self.guild_id)
            if guild is None or guild.unavailable or guild.me is None:
                return
            channel = guild.get_channel(int(channel_id))
            if not isinstance(channel, discord.VoiceChannel):
                self.counter_error = "対象のボイスチャンネルが見つかりません。表示先を選び直してください。"
                return
            if not channel.permissions_for(guild.me).manage_channels:
                self.counter_error = "Botに対象チャンネルの「チャンネルの管理」権限を付けてください。"
                return
            await self.refresh_member_counter(guild, channel, channel_id)

    async def close(self):
        pending = []
        for loop in (self.member_counter_loop, self.remove_existing_unregistered_reactions):
            task = loop.get_task()
            if task is not None:
                loop.cancel()
                pending.append(task)
        await asyncio.gather(*pending, return_exceptions=True)
        await super().close()

    def message_lock(self, message_id: int) -> asyncio.Lock:
        return self.locks[message_id % len(self.locks)]

    async def setup_hook(self) -> None:
        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self) -> None:
        LOG.info("ログイン完了: %s / サーバーID %d", self.user, self.guild_id)
        if not self.member_counter_loop.is_running():
            self.member_counter_loop.start()
        if not self.remove_existing_unregistered_reactions.is_running():
            self.remove_existing_unregistered_reactions.start()

    @tasks.loop(count=1)
    async def remove_existing_unregistered_reactions(self) -> None:
        panels = self.database.execute(
            "SELECT DISTINCT channel_id, message_id FROM reaction_roles"
        ).fetchall()
        for channel_id, message_id in panels:
            async with self.message_lock(int(message_id)):
                allowed = {
                    key for (key,) in self.database.execute(
                        "SELECT emoji_key FROM reaction_roles WHERE channel_id=? AND message_id=?",
                        (channel_id, message_id),
                    )
                }
                if not allowed:
                    continue

                channel = self.get_partial_messageable(int(channel_id), guild_id=self.guild_id)
                try:
                    message = await channel.fetch_message(int(message_id))
                except discord.NotFound:
                    continue
                except discord.HTTPException as error:
                    LOG.warning("リアクションロールのメッセージを取得できませんでした: message_id=%s status=%s", message_id, error.status)
                    continue

                for reaction in message.reactions:
                    if emoji_key(reaction.emoji) not in allowed:
                        await self.clear_unregistered_reaction(message, reaction.emoji)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self.change_role(payload, add=True)

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self.change_role(payload, add=False)

    async def change_role(self, payload: discord.RawReactionActionEvent, *, add: bool) -> None:
        if payload.guild_id != self.guild_id:
            return
        if self.user is None or payload.user_id == self.user.id:
            return

        async with self.message_lock(payload.message_id):
            message_key = (str(payload.channel_id), str(payload.message_id))
            binding = self.database.execute(
                "SELECT role_id, remove_role_id FROM reaction_roles WHERE channel_id=? AND message_id=? AND emoji_key=?",
                (*message_key, emoji_key(payload.emoji)),
            ).fetchone()
            if binding is None:
                if add and self.database.execute(
                    "SELECT 1 FROM reaction_roles WHERE channel_id=? AND message_id=? LIMIT 1",
                    message_key,
                ).fetchone():
                    channel = self.get_partial_messageable(payload.channel_id, guild_id=self.guild_id)
                    message = channel.get_partial_message(payload.message_id)
                    await self.clear_unregistered_reaction(message, payload.emoji)
                return
            if payload.burst:
                return

            guild = self.get_guild(self.guild_id)
            if guild is None:
                return
            role = guild.get_role(int(binding[0]))
            if role is None or not role.is_assignable():
                LOG.warning("ロールを操作できません: role_id=%s", binding[0])
                return

            try:
                async with self.member_role_locks[payload.user_id % len(self.member_role_locks)]:
                    member = payload.member or await guild.fetch_member(payload.user_id)
                    if member.bot:
                        return
                    if add:
                        await member.add_roles(role, reason="リアクションロール")
                        if binding[1] is not None:
                            await self.remove_role_after_grant(member, guild, binding[1])
                    else:
                        await member.remove_roles(role, reason="リアクションロール")
            except discord.NotFound:
                return
            except discord.HTTPException:
                LOG.exception("ロール操作失敗: member_id=%s role_id=%s", payload.user_id, role.id)

    async def remove_role_after_grant(self, member: discord.Member, guild: discord.Guild, role_id: str) -> None:
        role = guild.get_role(int(role_id))
        if role is None or not role.is_assignable():
            LOG.warning("付与後に外すロールを操作できません: role_id=%s", role_id)
            return
        try:
            await member.remove_roles(role, reason="リアクションロール付与後の解除")
        except discord.NotFound:
            return
        except discord.HTTPException:
            LOG.exception("付与後のロール解除に失敗: member_id=%s role_id=%s", member.id, role_id)

    async def clear_unregistered_reaction(
        self, message: discord.PartialMessage, emoji: str | discord.Emoji | discord.PartialEmoji,
    ) -> None:
        try:
            await message.clear_reaction(emoji)
        except discord.NotFound:
            return
        except discord.Forbidden:
            LOG.warning("指定外リアクションの削除には「メッセージの管理」権限が必要です: channel_id=%s", message.channel.id)
        except discord.HTTPException as error:
            LOG.warning("指定外リアクションの削除に失敗しました: message_id=%s status=%s", message.id, error.status)

    def removal_role_id(self, grant_role_id: str, remove_role: discord.Role | None) -> str | None:
        if remove_role is None:
            return None
        if str(remove_role.id) == grant_role_id:
            raise ValueError("付与するロールと、外すロールには別のロールを選んでください。")
        if (
            remove_role.guild.id != self.guild_id
            or not remove_role.is_assignable()
            or not remove_role.guild.me.guild_permissions.manage_roles
        ):
            raise ValueError("Botにロールの管理権限を付け、外すロールをBotのロールより下に置いてください。")
        return str(remove_role.id)

    async def add_binding(
        self, message_link: str, emoji: str, role: discord.Role, remove_role: discord.Role | None = None,
    ) -> None:
        channel_id, message_id = parse_message_link(message_link, self.guild_id)
        emoji = emoji.strip()
        key = emoji_key(emoji)
        if not role.is_assignable() or not role.guild.me.guild_permissions.manage_roles:
            raise ValueError("Botにロールの管理権限を付け、Botのロールを対象ロールより上に置いてください。")
        remove_role_id = self.removal_role_id(str(role.id), remove_role)

        async with self.message_lock(message_id):
            values = (str(channel_id), str(message_id), key, emoji, str(role.id))
            try:
                with self.database:
                    self.database.execute(
                        "INSERT INTO reaction_roles (channel_id, message_id, emoji_key, emoji_label, role_id, remove_role_id) "
                        "VALUES (?, ?, ?, ?, ?, ?)", (*values, remove_role_id),
                    )
            except sqlite3.IntegrityError as error:
                raise ValueError("このメッセージの絵文字、またはこのロールは登録済みです。") from error

            seeded = False
            try:
                channel = self.get_partial_messageable(channel_id, guild_id=self.guild_id)
                message = await channel.fetch_message(message_id)
                await message.add_reaction(emoji)
                seeded = True
            except discord.NotFound as error:
                raise ValueError("メッセージが見つからないか、Botがその絵文字を使えません。") from error
            except discord.Forbidden as error:
                raise ValueError("対象チャンネルでBotの閲覧・履歴閲覧・リアクション追加権限を確認してください。") from error
            except discord.HTTPException as error:
                raise ValueError("絵文字を付けられませんでした。Discordで使える絵文字を1つ入力してください。") from error
            finally:
                if not seeded:
                    with self.database:
                        self.database.execute(
                            "DELETE FROM reaction_roles WHERE channel_id=? AND message_id=? AND emoji_key=?",
                            values[:3],
                        )

    def bindings(self) -> list[tuple[str, str, str, str, str | None]]:
        return self.database.execute(
            "SELECT channel_id, message_id, emoji_label, role_id, remove_role_id "
            "FROM reaction_roles ORDER BY channel_id, message_id"
        ).fetchall()

    async def set_removal_role(self, role_id: str, remove_role: discord.Role | None) -> None:
        remove_role_id = self.removal_role_id(role_id, remove_role)
        binding = self.database.execute(
            "SELECT message_id FROM reaction_roles WHERE role_id=?", (role_id,),
        ).fetchone()
        if binding is None:
            raise ValueError("登録が見つかりません。")
        async with self.message_lock(int(binding[0])):
            with self.database:
                result = self.database.execute(
                    "UPDATE reaction_roles SET remove_role_id=? WHERE role_id=? AND message_id=?",
                    (remove_role_id, role_id, binding[0]),
                )
            if not result.rowcount:
                raise ValueError("登録が見つかりません。")

    async def remove_binding(self, role_id: str) -> bool:
        binding = self.database.execute(
            "SELECT message_id, emoji_key FROM reaction_roles WHERE role_id=?", (role_id,)
        ).fetchone()
        if binding is None:
            return False
        async with self.message_lock(int(binding[0])):
            with self.database:
                result = self.database.execute(
                    "DELETE FROM reaction_roles WHERE role_id=? AND message_id=? AND emoji_key=?",
                    (role_id, *binding),
                )
        return bool(result.rowcount)


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
    @app_commands.describe(
        message_link="対象のメッセージリンク", emoji="通常絵文字またはカスタム絵文字",
        role="付与するロール", remove_role="付与後に外すロール（任意）",
    )
    async def add(
        self, interaction: discord.Interaction, message_link: str, emoji: str, role: discord.Role,
        remove_role: discord.Role | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.bot.add_binding(message_link, emoji, role, remove_role)
        except ValueError as error:
            await interaction.followup.send(str(error), ephemeral=True)
        else:
            removal = f" 付与後に {remove_role.mention} を外します。" if remove_role else ""
            await interaction.followup.send(f"{emoji} → {role.mention} を登録しました。{removal}", ephemeral=True)

    @app_commands.command(name="set-remove-role", description="付与後に外すロールを設定または解除する")
    @app_commands.describe(role="登録済みの付与ロール", remove_role="付与後に外すロール（省略すると設定解除）")
    async def set_remove_role(
        self, interaction: discord.Interaction, role: discord.Role, remove_role: discord.Role | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.bot.set_removal_role(str(role.id), remove_role)
        except ValueError as error:
            message = str(error)
        else:
            message = f"{role.mention} の付与後に {remove_role.mention} を外します。" if remove_role else "設定を解除しました。"
        await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(name="remove", description="登録を解除する（付与済みのロールは維持）")
    @app_commands.describe(role="登録を解除するロール")
    async def remove(self, interaction: discord.Interaction, role: discord.Role) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if await self.bot.remove_binding(str(role.id)):
            message = "登録を解除しました。付与済みのロールはそのままです。"
        else:
            message = "そのロールは登録されていません。"
        await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(name="list", description="登録中のリアクションロールを表示")
    async def list_rules(self, interaction: discord.Interaction) -> None:
        rows = self.bot.bindings()
        if not rows:
            await interaction.response.send_message("登録はまだありません。", ephemeral=True)
            return

        pages: list[str] = []
        page = ""
        for channel_id, message_id, emoji, role_id, remove_role_id in rows:
            url = f"https://discord.com/channels/{self.bot.guild_id}/{channel_id}/{message_id}"
            removal = f" · 外す <@&{remove_role_id}>" if remove_role_id else ""
            line = f"{emoji} → <@&{role_id}>{removal} · [メッセージ]({url})\n"
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
