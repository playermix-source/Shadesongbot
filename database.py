import sqlite3
import datetime

DB_PATH = "beatnova.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, name TEXT, joined TEXT,
        downloads INTEGER DEFAULT 0, streak INTEGER DEFAULT 0,
        last_active TEXT, subscribed INTEGER DEFAULT 0,
        xp INTEGER DEFAULT 0, level INTEGER DEFAULT 1,
        invite_points INTEGER DEFAULT 0
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, song TEXT, downloaded_at TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, song TEXT, UNIQUE(user_id, song)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS wishlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, song TEXT, UNIQUE(user_id, song)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, song TEXT, note TEXT, UNIQUE(user_id, song)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS ratings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, song TEXT, rating INTEGER, UNIQUE(user_id, song)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS song_stats (
        song TEXT PRIMARY KEY,
        downloads INTEGER DEFAULT 0, favorites INTEGER DEFAULT 0
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS song_reactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, song TEXT, reaction TEXT,
        UNIQUE(user_id, song)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS bot_stats (
        key TEXT PRIMARY KEY, value TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS last_downloaded (
        user_id INTEGER PRIMARY KEY,
        title TEXT, duration TEXT, by_name TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS group_stats (
        group_id INTEGER, user_id INTEGER, user_name TEXT,
        downloads INTEGER DEFAULT 0,
        PRIMARY KEY(group_id, user_id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS group_settings (
        group_id INTEGER PRIMARY KEY,
        daily_song INTEGER DEFAULT 0,
        party_mode INTEGER DEFAULT 0,
        party_host INTEGER DEFAULT 0
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS party_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER, user_id INTEGER,
        user_name TEXT, song TEXT, added_at TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS daily_rewards (
        user_id INTEGER PRIMARY KEY, last_claim TEXT
    )""")

    conn.commit()
    conn.close()
    print("✅ Database initialized!")

# ========== USER ==========

def get_user(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def ensure_user(user_id, name):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.datetime.now().strftime("%d %b %Y")
    today = datetime.date.today().isoformat()
    c.execute("""INSERT INTO users (user_id, name, joined, last_active)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET name = excluded.name
    """, (user_id, name, now, today))
    conn.commit()
    conn.close()

def update_streak(user_id):
    conn = get_conn()
    c = conn.cursor()
    today = datetime.date.today().isoformat()
    c.execute("SELECT last_active, streak FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if row:
        last = row["last_active"]
        streak = row["streak"] or 0
        if last:
            try:
                diff = (datetime.date.today() - datetime.date.fromisoformat(last)).days
                if diff == 1: streak += 1
                elif diff > 1: streak = 1
            except: streak = 1
        else:
            streak = 1
        c.execute("UPDATE users SET streak=?, last_active=? WHERE user_id=?", (streak, today, user_id))
    conn.commit()
    conn.close()

def increment_downloads(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET downloads=downloads+1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def add_xp(user_id, xp_amount):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET xp=xp+? WHERE user_id=?", (xp_amount, user_id))
    c.execute("SELECT xp FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    total_xp = row["xp"] if row else 0
    new_level = max(1, total_xp // 100 + 1)
    c.execute("UPDATE users SET level=? WHERE user_id=?", (new_level, user_id))
    conn.commit()
    conn.close()
    return total_xp, new_level

def get_all_users():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM users ORDER BY downloads DESC")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_subscribers():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE subscribed=1")
    rows = c.fetchall()
    conn.close()
    return [r["user_id"] for r in rows]

def set_subscribed(user_id, value):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET subscribed=? WHERE user_id=?", (1 if value else 0, user_id))
    conn.commit()
    conn.close()

def is_subscribed(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT subscribed FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return bool(row and row["subscribed"])

def get_total_users():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as cnt FROM users")
    row = c.fetchone()
    conn.close()
    return row["cnt"] if row else 0

# ========== HISTORY ==========

def add_history(user_id, song):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.datetime.now().isoformat()
    c.execute("INSERT INTO history (user_id, song, downloaded_at) VALUES (?,?,?)", (user_id, song, now))
    conn.commit()
    conn.close()

def get_history(user_id, limit=10):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT song FROM history WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return [r["song"] for r in rows]

# ========== FAVORITES ==========

def add_favorite(user_id, song):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO favorites (user_id, song) VALUES (?,?)", (user_id, song))
        conn.commit()
        result = True
    except sqlite3.IntegrityError:
        result = False
    conn.close()
    return result

def remove_favorite(user_id, song):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM favorites WHERE user_id=? AND song=?", (user_id, song))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed

def get_favorites(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT song FROM favorites WHERE user_id=?", (user_id,))
    rows = c.fetchall()
    conn.close()
    return [r["song"] for r in rows]

def count_favorites(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as cnt FROM favorites WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row["cnt"] if row else 0

def is_favorite(user_id, song):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT 1 FROM favorites WHERE user_id=? AND song=?", (user_id, song))
    result = c.fetchone() is not None
    conn.close()
    return result

# ========== WISHLIST ==========

def add_wishlist(user_id, song):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO wishlist (user_id, song) VALUES (?,?)", (user_id, song))
        conn.commit()
        result = True
    except sqlite3.IntegrityError:
        result = False
    conn.close()
    return result

def get_wishlist(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT song FROM wishlist WHERE user_id=?", (user_id,))
    rows = c.fetchall()
    conn.close()
    return [r["song"] for r in rows]

# ========== NOTES ==========

def save_note(user_id, song, note):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO notes (user_id, song, note) VALUES (?,?,?)
        ON CONFLICT(user_id, song) DO UPDATE SET note=excluded.note
    """, (user_id, song, note))
    conn.commit()
    conn.close()

# ========== RATINGS ==========

def save_rating(user_id, song, rating):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO ratings (user_id, song, rating) VALUES (?,?,?)
        ON CONFLICT(user_id, song) DO UPDATE SET rating=excluded.rating
    """, (user_id, song, rating))
    conn.commit()
    conn.close()

def get_avg_rating(song):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT AVG(rating) as avg_r, COUNT(*) as cnt FROM ratings WHERE song=?", (song,))
    row = c.fetchone()
    conn.close()
    if row and row["cnt"]: return round(row["avg_r"], 1), row["cnt"]
    return 0, 0

def get_top_rated_songs(limit=10):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""SELECT song, AVG(rating) as avg_r, COUNT(*) as cnt
        FROM ratings GROUP BY song ORDER BY avg_r DESC LIMIT ?""", (limit,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def user_rated_count(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as cnt FROM ratings WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row["cnt"] if row else 0

# ========== SONG REACTIONS ==========

def save_reaction(user_id, song, reaction):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO song_reactions (user_id, song, reaction) VALUES (?,?,?)
        ON CONFLICT(user_id, song) DO UPDATE SET reaction=excluded.reaction
    """, (user_id, song, reaction))
    conn.commit()
    conn.close()

def get_song_reactions(song):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT reaction, COUNT(*) as cnt FROM song_reactions WHERE song=? GROUP BY reaction", (song,))
    rows = c.fetchall()
    conn.close()
    return {r["reaction"]: r["cnt"] for r in rows}

# ========== SONG GLOBAL STATS ==========

def increment_song_downloads(song):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO song_stats (song, downloads, favorites) VALUES (?,1,0)
        ON CONFLICT(song) DO UPDATE SET downloads=downloads+1""", (song,))
    conn.commit()
    conn.close()

def increment_song_favorites(song):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO song_stats (song, downloads, favorites) VALUES (?,0,1)
        ON CONFLICT(song) DO UPDATE SET favorites=favorites+1""", (song,))
    conn.commit()
    conn.close()

def get_song_global_stats(song):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM song_stats WHERE song=?", (song,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else {"song": song, "downloads": 0, "favorites": 0}

# ========== BOT STATS ==========

def get_bot_stat(key, default="0"):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM bot_stats WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row["value"] if row else default

def increment_bot_stat(key):
    conn = get_conn()
    c = conn.cursor()
    current = int(get_bot_stat(key, "0"))
    new_val = current + 1
    c.execute("""INSERT INTO bot_stats (key, value) VALUES (?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (key, str(new_val)))
    conn.commit()
    conn.close()
    return new_val

def get_total_downloads():
    return int(get_bot_stat("total_downloads", "0"))

# ========== LAST DOWNLOADED ==========

def save_last_downloaded(user_id, title, duration, by_name):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO last_downloaded (user_id, title, duration, by_name) VALUES (?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET title=excluded.title,
        duration=excluded.duration, by_name=excluded.by_name
    """, (user_id, title, duration, by_name))
    conn.commit()
    conn.close()

def get_last_downloaded(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM last_downloaded WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

# ========== GROUP STATS ==========

def update_group_stats(group_id, user_id, user_name):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO group_stats (group_id, user_id, user_name, downloads)
        VALUES (?,?,?,1)
        ON CONFLICT(group_id, user_id) DO UPDATE SET
        downloads=downloads+1, user_name=excluded.user_name
    """, (group_id, user_id, user_name))
    conn.commit()
    conn.close()

def get_group_leaderboard(group_id, limit=10):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""SELECT user_name, downloads FROM group_stats
        WHERE group_id=? ORDER BY downloads DESC LIMIT ?""", (group_id, limit))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_group_total_downloads(group_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT SUM(downloads) as total FROM group_stats WHERE group_id=?", (group_id,))
    row = c.fetchone()
    conn.close()
    return row["total"] or 0

def get_group_members_count(group_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as cnt FROM group_stats WHERE group_id=?", (group_id,))
    row = c.fetchone()
    conn.close()
    return row["cnt"] or 0

# ========== GROUP SETTINGS ==========

def get_group_setting(group_id, key):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"SELECT {key} FROM group_settings WHERE group_id=?", (group_id,))
    row = c.fetchone()
    conn.close()
    return row[key] if row else 0

def set_group_setting(group_id, key, value):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO group_settings (group_id) VALUES (?)
        ON CONFLICT(group_id) DO NOTHING""", (group_id,))
    c.execute(f"UPDATE group_settings SET {key}=? WHERE group_id=?", (value, group_id))
    conn.commit()
    conn.close()

# ========== PARTY MODE ==========

def add_to_party_queue(group_id, user_id, user_name, song):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.datetime.now().isoformat()
    c.execute("""INSERT INTO party_queue (group_id, user_id, user_name, song, added_at)
        VALUES (?,?,?,?,?)""", (group_id, user_id, user_name, song, now))
    conn.commit()
    conn.close()

def get_party_queue(group_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM party_queue WHERE group_id=? ORDER BY id", (group_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def pop_party_queue(group_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM party_queue WHERE group_id=? ORDER BY id LIMIT 1", (group_id,))
    row = c.fetchone()
    if row:
        c.execute("DELETE FROM party_queue WHERE id=?", (row["id"],))
        conn.commit()
    conn.close()
    return dict(row) if row else None

def clear_party_queue(group_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM party_queue WHERE group_id=?", (group_id,))
    conn.commit()
    conn.close()

# ========== DAILY REWARD ==========

def can_claim_reward(user_id):
    conn = get_conn()
    c = conn.cursor()
    today = datetime.date.today().isoformat()
    c.execute("SELECT last_claim FROM daily_rewards WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row: return True
    return row["last_claim"] != today

def claim_reward(user_id):
    conn = get_conn()
    c = conn.cursor()
    today = datetime.date.today().isoformat()
    c.execute("""INSERT INTO daily_rewards (user_id, last_claim) VALUES (?,?)
        ON CONFLICT(user_id) DO UPDATE SET last_claim=excluded.last_claim
    """, (user_id, today))
    conn.commit()
    conn.close()
