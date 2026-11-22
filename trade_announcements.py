from contextlib import closing
from io import BytesIO
import sqlite3

import discord

SITE_ORIGIN = "https://catch-the-brainrot.eggkingdom.com"


def init_announcements(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS trade_announcement_destination (
            id INTEGER PRIMARY KEY CHECK(id=1),
            guild_id TEXT NOT NULL,
            channel_id TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS trade_announcement_queue (
            offer_id INTEGER PRIMARY KEY REFERENCES trade_offers(id) ON DELETE CASCADE,
            guild_id TEXT NOT NULL
        )
    """)


def queue_announcement(db, offer_id):
    db.execute(
        """INSERT INTO trade_announcement_queue(offer_id, guild_id)
           SELECT ?, guild_id FROM trade_announcement_destination WHERE id=1""",
        (offer_id,),
    )


class TradeAnnouncements:
    def __init__(self, path):
        self.path = path

    def destination(self):
        with closing(sqlite3.connect(self.path, timeout=8)) as db:
            return db.execute("SELECT guild_id, channel_id FROM trade_announcement_destination WHERE id=1").fetchone()

    def configure(self, guild_id, channel_id):
        with closing(sqlite3.connect(self.path, timeout=8)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if channel_id:
                db.execute("DELETE FROM trade_announcement_queue WHERE guild_id!=?", (guild_id,))
                db.execute(
                    """INSERT INTO trade_announcement_destination VALUES (1, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET guild_id=excluded.guild_id, channel_id=excluded.channel_id""",
                    (guild_id, channel_id),
                )
            else:
                db.execute("DELETE FROM trade_announcement_destination")
                db.execute("DELETE FROM trade_announcement_queue")

    def next_offer(self, guild_id, channel_id):
        with closing(sqlite3.connect(self.path, timeout=8)) as db:
            db.row_factory = sqlite3.Row
            return db.execute(
                """SELECT o.id, o.offered, o.wanted, o.note, o.owner_name, o.closed_at, p.photo
                   FROM trade_announcement_queue q
                   JOIN trade_offers o ON o.id=q.offer_id
                   JOIN trade_announcement_destination d ON d.guild_id=q.guild_id
                   LEFT JOIN trade_offer_photos p ON p.offer_id=o.id
                   WHERE d.guild_id=? AND d.channel_id=? ORDER BY q.offer_id LIMIT 1""",
                (guild_id, channel_id),
            ).fetchone()

    def delivered(self, offer_id):
        with closing(sqlite3.connect(self.path, timeout=8)) as db, db:
            db.execute("DELETE FROM trade_announcement_queue WHERE offer_id=?", (offer_id,))


def can_publish(channel, member):
    if not isinstance(channel, discord.TextChannel) or member is None:
        return False
    permissions = channel.permissions_for(member)
    return all((permissions.view_channel, permissions.send_messages, permissions.embed_links, permissions.attach_files))


async def publish_offer(channel, offer):
    def plain(value, limit):
        return discord.utils.escape_markdown(discord.utils.escape_mentions(value), ignore_links=False)[:limit]

    url = f"{SITE_ORIGIN}/trade/{offer['id']}"
    embed = discord.Embed(title="新しい交換募集", url=url, colour=0xBEA2E1)
    embed.add_field(name="出すもの", value=plain(offer["offered"], 1024), inline=True)
    embed.add_field(name="欲しいもの", value=plain(offer["wanted"], 1024), inline=True)
    embed.add_field(name="投稿者", value=plain(offer["owner_name"], 1024), inline=False)
    if offer["note"]:
        embed.add_field(name="備考", value=plain(offer["note"], 1024), inline=False)
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="募集の詳細を見る", url=url))
    arguments = {
        "embed": embed, "view": view,
        "allowed_mentions": discord.AllowedMentions.none(),
        "nonce": f"ctb-{offer['id']}",
    }
    if offer["photo"]:
        embed.set_image(url="attachment://offer.webp")
        with BytesIO(offer["photo"]) as image:
            photo = discord.File(image, filename="offer.webp")
            try:
                await channel.send(**arguments, file=photo)
            finally:
                photo.close()
    else:
        await channel.send(**arguments)
