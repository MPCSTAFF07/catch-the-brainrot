from contextlib import closing, contextmanager
import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from trade_profiles import register_profiles, save_profile

PAGE_SIZE = 24
CATEGORIES = {"brainrot": "ブレインロット", "item": "アイテム", "bundle": "セット", "other": "その他"}
DEAL_STATUS = {
    "pending": "承認待ち", "accepted": "交換中", "completed": "交換完了",
    "declined": "見送り", "cancelled": "キャンセル",
}


def create_trade_board(db_path):
    board = Blueprint("trade", __name__)

    @contextmanager
    def database(*, write=False):
        with closing(sqlite3.connect(db_path, timeout=8)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db

    with database(write=True) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS trade_offers (
                id INTEGER PRIMARY KEY, owner_id TEXT NOT NULL, owner_name TEXT NOT NULL,
                offered TEXT NOT NULL, wanted TEXT NOT NULL, note TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'other',
                created_at TEXT NOT NULL, closed_at TEXT
            )
        """)
        if not any(row["name"] == "category" for row in db.execute("PRAGMA table_info(trade_offers)")):
            db.execute("ALTER TABLE trade_offers ADD COLUMN category TEXT NOT NULL DEFAULT 'other'")
        db.execute("""
            CREATE TABLE IF NOT EXISTS trade_deals (
                id INTEGER PRIMARY KEY,
                offer_id INTEGER NOT NULL REFERENCES trade_offers(id),
                buyer_id TEXT NOT NULL, buyer_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                owner_done INTEGER NOT NULL DEFAULT 0,
                buyer_done INTEGER NOT NULL DEFAULT 0
            )
        """)
        db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS trade_one_active_request
            ON trade_deals(offer_id, buyer_id) WHERE status IN ('pending', 'accepted')
        """)
        db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS trade_one_accepted
            ON trade_deals(offer_id) WHERE status = 'accepted'
        """)
        db.execute("CREATE INDEX IF NOT EXISTS trade_deals_by_buyer ON trade_deals(buyer_id, id)")
        db.execute("CREATE INDEX IF NOT EXISTS trade_deals_by_offer ON trade_deals(offer_id, id)")
        db.execute("""
            CREATE TABLE IF NOT EXISTS trade_messages (
                id INTEGER PRIMARY KEY,
                deal_id INTEGER NOT NULL REFERENCES trade_deals(id),
                sender_id TEXT NOT NULL, body TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS trade_messages_by_deal ON trade_messages(deal_id, id)")

    def text_arg(data, key, limit):
        return data.get(key, "").strip()[:limit]

    def offer_by_id(db, offer_id):
        row = db.execute(
            """SELECT o.*, EXISTS(SELECT 1 FROM trade_deals d
               WHERE d.offer_id=o.id AND d.status='accepted') AS in_progress
               FROM trade_offers o WHERE o.id=?""", (offer_id,),
        ).fetchone() if offer_id <= 2**63 - 1 else None
        if row is None:
            abort(404)
        return row

    def visible_deal(db, deal_id):
        row = db.execute(
            """SELECT d.*, o.offered, o.wanted, o.owner_id, o.owner_name
               FROM trade_deals d JOIN trade_offers o ON o.id=d.offer_id
               WHERE d.id=? AND (d.buyer_id=? OR o.owner_id=?)""",
            (deal_id, session["discord_id"], session["discord_id"]),
        ).fetchone() if deal_id <= 2**63 - 1 else None
        if row is None:
            abort(404)
        return row

    @board.get("/")
    def index():
        query = text_arg(request.args, "q", 100)
        category = request.args.get("category", "")
        category = category if category in CATEGORIES else ""
        mine = request.args.get("mine") == "1"
        sort = "old" if request.args.get("sort") == "old" else "new"
        page = min(100000, max(1, request.args.get("page", 1, type=int)))
        criteria, values = [], []
        if mine:
            criteria.append("o.owner_id = ?")
            values.append(session["discord_id"])
        else:
            criteria.extend(["o.closed_at IS NULL", """NOT EXISTS (
                SELECT 1 FROM trade_deals d WHERE d.offer_id=o.id AND d.status='accepted'
            )"""])
        if category:
            criteria.append("o.category = ?")
            values.append(category)
        if query:
            criteria.append("instr(lower(o.wanted), lower(?)) > 0")
            values.append(query)
        order = "ASC" if sort == "old" else "DESC"
        with database() as db:
            rows = db.execute(
                f"""SELECT o.id, o.owner_name, o.offered, o.wanted, o.category, o.closed_at,
                    EXISTS(SELECT 1 FROM trade_deals d WHERE d.offer_id=o.id AND d.status='accepted') AS in_progress,
                    (SELECT COUNT(*) FROM trade_likes l WHERE l.offer_id=o.id) AS likes
                    FROM trade_offers o WHERE {' AND '.join(criteria)}
                    ORDER BY o.id {order} LIMIT ? OFFSET ?""",
                (*values, PAGE_SIZE + 1, (page - 1) * PAGE_SIZE),
            ).fetchall()
        return render_template(
            "trade.html", offers=rows[:PAGE_SIZE], query=query, mine=mine,
            page=page, has_next=len(rows) > PAGE_SIZE, sort=sort,
            categories=CATEGORIES, category=category, active="trade",
            mode="post" if request.args.get("mode") == "post" else "search",
        )

    @board.get("/trade/new")
    def new():
        return redirect(url_for("trade.index", mode="post"))

    @board.post("/trade/post")
    def post():
        offered = text_arg(request.form, "offered", 100)
        wanted = text_arg(request.form, "wanted", 100)
        note = text_arg(request.form, "note", 600)
        category = request.form.get("category", "other")
        if not offered or not wanted or category not in CATEGORIES:
            flash("出すもの、欲しいもの、カテゴリを確認してください。", "error")
            return redirect(url_for("trade.new"), code=303)
        with database(write=True) as db:
            save_profile(db, session["discord_id"], session["discord_name"])
            result = db.execute(
                """INSERT INTO trade_offers
                   (owner_id, owner_name, offered, wanted, note, category, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (session["discord_id"], session["discord_name"], offered, wanted, note, category),
            )
        flash("交換募集を投稿しました。", "success")
        return redirect(url_for("trade.offer", offer_id=result.lastrowid), code=303)

    @board.get("/trade/<int:offer_id>")
    def offer(offer_id):
        with database() as db:
            item = offer_by_id(db, offer_id)
            own = item["owner_id"] == session["discord_id"]
            likes = db.execute(
                "SELECT COUNT(*) AS total, COUNT(CASE WHEN user_id=? THEN 1 END) AS mine FROM trade_likes WHERE offer_id=?",
                (session["discord_id"], offer_id),
            ).fetchone()
            if own:
                deals = db.execute("SELECT * FROM trade_deals WHERE offer_id=? ORDER BY id DESC", (offer_id,)).fetchall()
            else:
                deals = db.execute(
                    "SELECT * FROM trade_deals WHERE offer_id=? AND buyer_id=? ORDER BY id DESC",
                    (offer_id, session["discord_id"]),
                ).fetchall()
        can_apply = not (own or item["closed_at"] or item["in_progress"] or
                         any(deal["status"] == "pending" for deal in deals))
        return render_template(
            "trade_offer.html", item=item, own=own, deals=deals, can_apply=can_apply,
            categories=CATEGORIES, statuses=DEAL_STATUS, likes=likes, active="trade",
        )

    @board.post("/trade/<int:offer_id>/close")
    def close(offer_id):
        with database(write=True) as db:
            item = offer_by_id(db, offer_id)
            if item["owner_id"] != session["discord_id"]:
                abort(404)
            if item["in_progress"] or item["closed_at"]:
                abort(409)
            db.execute("UPDATE trade_offers SET closed_at=CURRENT_TIMESTAMP WHERE id=?", (offer_id,))
            db.execute("UPDATE trade_deals SET status='declined' WHERE offer_id=? AND status='pending'", (offer_id,))
        flash("交換募集を締め切りました。", "success")
        return redirect(url_for("trade.index", mine=1), code=303)

    @board.post("/trade/<int:offer_id>/apply")
    def apply(offer_id):
        user_id = session["discord_id"]
        message = text_arg(request.form, "message", 1000)
        if not message:
            flash("申込みのメッセージを入力してください。", "error")
            return redirect(url_for("trade.offer", offer_id=offer_id), code=303)
        with database(write=True) as db:
            item = offer_by_id(db, offer_id)
            if item["owner_id"] == user_id or item["closed_at"] or item["in_progress"]:
                abort(409)
            previous = db.execute(
                "SELECT id FROM trade_deals WHERE offer_id=? AND buyer_id=? AND status='pending'",
                (offer_id, user_id),
            ).fetchone()
            if previous:
                return redirect(url_for("trade.deal", deal_id=previous["id"]), code=303)
            save_profile(db, user_id, session["discord_name"])
            deal_id = db.execute(
                "INSERT INTO trade_deals(offer_id, buyer_id, buyer_name) VALUES (?, ?, ?)",
                (offer_id, user_id, session["discord_name"]),
            ).lastrowid
            db.execute("INSERT INTO trade_messages(deal_id, sender_id, body) VALUES (?, ?, ?)", (deal_id, user_id, message))
        flash("交換を申し込みました。", "success")
        return redirect(url_for("trade.deal", deal_id=deal_id), code=303)

    @board.get("/trade/deals")
    def inbox():
        user_id = session["discord_id"]
        incoming = request.args.get("box") == "incoming"
        page = min(100000, max(1, request.args.get("page", 1, type=int)))
        field = "o.owner_id" if incoming else "d.buyer_id"
        with database() as db:
            deals = db.execute(
                f"""SELECT d.*, o.offered, o.wanted, o.owner_name
                    FROM trade_deals d JOIN trade_offers o ON o.id=d.offer_id
                    WHERE {field}=? ORDER BY d.id DESC LIMIT ? OFFSET ?""",
                (user_id, PAGE_SIZE + 1, (page - 1) * PAGE_SIZE),
            ).fetchall()
        return render_template(
            "trade_inbox.html", deals=deals[:PAGE_SIZE], incoming=incoming, statuses=DEAL_STATUS,
            page=page, has_next=len(deals) > PAGE_SIZE, active="trade_inbox",
        )

    @board.get("/trade/deals/<int:deal_id>")
    def deal(deal_id):
        with database() as db:
            item = visible_deal(db, deal_id)
            messages = db.execute(
                """SELECT sender_id, body, created_at FROM (
                      SELECT id, sender_id, body, created_at FROM trade_messages
                      WHERE deal_id=? ORDER BY id DESC LIMIT 100
                   ) ORDER BY id""",
                (deal_id,),
            ).fetchall()
        return render_template(
            "trade_deal.html", item=item, messages=messages, statuses=DEAL_STATUS, active="trade_inbox",
        )

    @board.post("/trade/deals/<int:deal_id>/message")
    def send_message(deal_id):
        message = text_arg(request.form, "message", 1000)
        with database(write=True) as db:
            item = visible_deal(db, deal_id)
            if item["status"] not in {"accepted", "pending"}:
                abort(409)
            if message:
                db.execute("INSERT INTO trade_messages(deal_id, sender_id, body) VALUES (?, ?, ?)",
                           (deal_id, session["discord_id"], message))
        return redirect(url_for("trade.deal", deal_id=deal_id, _anchor="messages"), code=303)

    @board.post("/trade/deals/<int:deal_id>/action")
    def deal_action(deal_id):
        action = request.form.get("action")
        with database(write=True) as db:
            item = visible_deal(db, deal_id)
            owner = item["owner_id"] == session["discord_id"]
            status = item["status"]
            if action == "accept" and owner and status == "pending":
                listing = offer_by_id(db, item["offer_id"])
                if listing["closed_at"] or listing["in_progress"]:
                    abort(409)
                db.execute("UPDATE trade_deals SET status='accepted' WHERE id=?", (deal_id,))
                db.execute(
                    "UPDATE trade_deals SET status='declined' WHERE offer_id=? AND id!=? AND status='pending'",
                    (item["offer_id"], deal_id),
                )
            elif action == "decline" and owner and status == "pending":
                db.execute("UPDATE trade_deals SET status='declined' WHERE id=?", (deal_id,))
            elif action == "cancel" and (
                (status == "accepted" and not item["owner_done"] and not item["buyer_done"]) or
                (status == "pending" and not owner)
            ):
                db.execute("UPDATE trade_deals SET status='cancelled' WHERE id=?", (deal_id,))
            elif action == "complete" and status == "accepted":
                column = "owner_done" if owner else "buyer_done"
                db.execute(f"UPDATE trade_deals SET {column}=1 WHERE id=?", (deal_id,))
                done = db.execute(
                    "SELECT owner_done AND buyer_done FROM trade_deals WHERE id=?", (deal_id,),
                ).fetchone()[0]
                if done:
                    db.execute("UPDATE trade_deals SET status='completed' WHERE id=?", (deal_id,))
                    db.execute("UPDATE trade_offers SET closed_at=CURRENT_TIMESTAMP WHERE id=?", (item["offer_id"],))
            else:
                abort(409)
        return redirect(url_for("trade.deal", deal_id=deal_id), code=303)

    register_profiles(board, database)
    return board
