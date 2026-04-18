import asyncio
import os
import requests
import random
import datetime
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import API_ID, API_HASH, BOT_TOKEN
import database as db
import apis

app = Client("beatnova_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Cleanup old temp files on startup
import glob as _glob, shutil as _shutil
_tmp_dir = "/tmp/beatnova_dl"
if os.path.exists(_tmp_dir):
    old_files = _glob.glob(f"{_tmp_dir}/*")
    if old_files:
        _shutil.rmtree(_tmp_dir, ignore_errors=True)
        print(f"[startup] Cleaned {len(old_files)} old temp files")

BOT_NAME = "BeatNova"
BOT_USERNAME = "@SHADE_SONG_BOT"
DEVELOPER = "@ZeroShader"
START_TIME = datetime.datetime.now()

active_quiz = {}
group_votes = {}
active_bomb = {}
active_duel = {}
active_guess = {}
active_wordle = {}
_pending_downloads = {}  # {user_id:query_key: {results, is_group, user_id, first_name}}
today_downloads = {"count": 0, "date": datetime.date.today()}
chat_histories = {}  # {user_id: [{"role": "user/assistant", "content": "..."}]}
chat_daily_count = {}  # {user_id: {"count": int, "date": date}}
CHAT_DAILY_LIMIT = 15
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


# ── Safe helpers ──────────────────────────────────────────────────────────────

def get_user_id(m) -> int | None:
    """Safely get user id — returns None for anonymous/channel messages"""
    return m.from_user.id if m.from_user else None

def get_user_name(m, fallback: str = "User") -> str:
    """Safely get first name"""
    return (m.from_user.first_name or fallback) if m.from_user else fallback

def safe_handler(func):
    """
    Decorator for all message handlers:
    1. Skips anonymous / channel messages (m.from_user is None)
    2. Catches SlowmodeWait silently — no crash, no log spam
    3. Catches all other exceptions and logs them without crashing the bot
    """
    import functools
    @functools.wraps(func)
    async def wrapper(client, m: Message):
        # Skip anonymous messages (channels, linked posts, etc.)
        if not m.from_user:
            return
        try:
            await func(client, m)
        except Exception as e:
            err = str(e)
            if "SLOWMODE_WAIT" in err or "SlowmodeWait" in err:
                # Group slowmode — silently ignore, download handler has its own DM logic
                return
            print(f"[{func.__name__}] Error: {type(e).__name__}: {err[:120]}")
            try:
                await m.reply("⚠️ Something went wrong. Please try again!")
            except Exception:
                pass
    return wrapper

# ─────────────────────────────────────────────────────────────────────────────

BEATNOVA_SYSTEM_PROMPT = """Tu BeatNova hai — ek music-loving AI dost jo Hinglish mein baat karta hai.

Rules:
- Hinglish mein baat kar (Hindi + English mix)
- Real dost jaisa natural baat kar — formal mat ban
- Choti replies de (2-3 lines max) jab tak kuch explain nahi karna
- Music ke baare mein passionate rehna
- Funny aur friendly rehna
- Agar koi song ya artist ke baare mein pooche to suggest karo
- Bot commands bhi suggest karo jaise: "try kar /download [song]"
- Kabhi mat bol ki tu AI hai — bas dost jaisa baat kar
- Agar koi personal problem ho to empathy dikhao
- "yaar", "bhai", "arre", "sach mein", "matlab" jaisi words use karo

Tu music bot BeatNova ka AI dost hai — gana sunna, suggest karna, baat karna sab karta hai!"""


PLACEHOLDERS = ["[song]", "[song name]", "[name]", "[artist]", "[line]", "[mood]", "[type]", "[a-z]"]

def is_valid_quiz_line(line):
    """Check if lyrics line is in Hindi/English only — no Punjabi/Gujarati/Tamil etc script"""
    # Allow only Latin (English) and Devanagari (Hindi) characters
    import unicodedata
    clean = line.strip()
    if not clean or len(clean) < 15:
        return False
    for char in clean:
        if char.isspace() or char in ',.!?"()-[]':
            continue
        name = unicodedata.name(char, '')
        # Allow Latin (English), Devanagari (Hindi/Urdu romanized), digits
        if not (char.isascii() or 'DEVANAGARI' in name or char.isdigit()):
            return False
    return True

# Large quiz song pools for variety
QUIZ_QUERIES = [
    "hindi popular songs hits",
    "bollywood romantic songs",
    "punjabi hits popular",
    "arijit singh songs",
    "atif aslam songs",
    "jubin nautiyal songs",
    "neha kakkar songs",
    "armaan malik songs",
    "shreya ghoshal songs",
    "sonu nigam songs",
    "kumar sanu songs",
    "udit narayan songs",
    "lata mangeshkar songs",
    "kishore kumar songs",
    "mohd rafi songs",
    "90s hindi songs",
    "2000s bollywood songs",
    "2010s hindi hits",
    "sad hindi songs",
    "party hindi songs",
    "romantic hindi songs",
    "new hindi songs 2024",
    "ap dhillon songs",
    "diljit dosanjh songs",
    "badshah songs",
    "yo yo honey singh",
    "anuv jain songs",
    "vishal mishra songs",
    "darshan raval songs",
    "b praak songs",
    "english pop hits",
    "ed sheeran songs",
    "taylor swift songs",
    "the weeknd songs",
    "coldplay songs",
    "imagine dragons songs",
]

# Hindi-only queries for lyrics-based games (avoid Punjabi/regional scripts)
HINDI_QUIZ_QUERIES = [
    "hindi romantic songs hits",
    "bollywood sad songs hindi",
    "arijit singh hindi songs",
    "atif aslam hindi songs",
    "jubin nautiyal songs hindi",
    "shreya ghoshal bollywood",
    "armaan malik hindi songs",
    "vishal mishra songs",
    "darshan raval hindi songs",
    "b praak hindi songs",
    "mohit chauhan songs",
    "sonu nigam bollywood songs",
    "kumar sanu hindi songs",
    "udit narayan hindi songs",
    "90s hindi romantic songs",
    "2000s bollywood hindi songs",
    "new hindi songs 2024",
    "hindi party songs bollywood",
]

MUSIC_FACTS = [
    "🎵 The longest officially released song is over 13 hours long!",
    "🎵 'Happy Birthday to You' was the first song played in space!",
    "🎵 A person's heartbeat syncs to the music they listen to!",
    "🎵 Music can boost workout performance by up to 15%!",
    "🎵 The guitar is the most played instrument in the world!",
    "🎵 Mozart could memorize and write out an entire piece after hearing it once!",
    "🎵 Listening to music releases dopamine — same as chocolate!",
    "🎵 'Bohemian Rhapsody' took 3 weeks to record in 1975!",
    "🎵 India has the world's largest film music industry!",
    "🎵 Arijit Singh has sung over 300 Bollywood songs!",
]

EASTER_EGGS = [
    "🥚 You found an easter egg! Here's a secret: The bot's name BeatNova comes from 'Beat' (music) + 'Nova' (star) ⭐",
    "🎩 Secret unlocked! Did you know @ZeroShader built this bot from scratch? Legends do exist! 👑",
    "🔮 Hidden message: The music never stops if you never stop listening! 🎵",
    "🤫 Psst! Try /party in a group for a surprise! 🎉",
    "🥚 Easter Egg #2: BeatNova processes thousands of songs... and hasn't complained once! 😄",
]



XP_REWARDS = {
    "download": 10,
    "first_download": 50,
    "daily_reward": 25,
    "rate_song": 5,
    "streak_3": 20,
    "streak_7": 50,
    "quiz_win": 30,
}

LOADING_MESSAGES = [
    ["🔍 Searching best quality...", "🎧 Fetching audio...", "⚡ Almost ready..."],
    ["🎵 Finding the perfect version...", "📡 Connecting to servers...", "✅ Got it!"],
    ["🕵️ Hunting down the track...", "🎙 Locking in audio quality...", "🚀 Sending your way!"],
    ["🎼 Reading the sheet music... just kidding", "⚙️ Processing audio...", "📦 Packing it up!"],
    ["🌐 Reaching across the internet...", "🎧 Buffering the beats...", "🔥 Here it comes!"],
]

MICRO_TIPS = [
    "🎧 **BeatNova Tip:** Use `/srec` for personalized recommendations!",
    "🎧 **BeatNova Tip:** Save your faves with `/save` while downloading!",
    "🎧 **BeatNova Tip:** Try `/rlc Tum Hi Ho lofi` for chill versions!",
    "🎧 **BeatNova Tip:** `/lang korean` for K-pop hits!",
    "🎧 **BeatNova Tip:** Play `/wordle` daily to build your streak!",
    "🎧 **BeatNova Tip:** Use `/quality` to pick your audio bitrate!",
    "🎧 **BeatNova Tip:** `/dwordle` — same word for everyone, compete with friends!",
    "🎧 **BeatNova Tip:** `/artist Arijit Singh` shows bio + top songs!",
    "🎧 **BeatNova Tip:** Rate songs with `/rate` to earn XP!",
    "🎧 **BeatNova Tip:** Check your badges with `/badges`!",
]

HOOK_SONGS = [
    "Tum Hi Ho", "Kesariya", "Raataan Lambiyan", "Tera Ban Jaunga",
    "Shayad", "Hawayein", "Channa Mereya", "Ae Dil Hai Mushkil",
    "Shape of You", "Blinding Lights", "Stay", "As It Was",
    "Husn", "Kahani Suno", "Ik Vaari Aa", "Phir Bhi Tumko Chahunga",
]

# ========== HELPERS ==========

def update_today_stats():
    today = datetime.date.today()
    if today_downloads["date"] != today:
        today_downloads["count"] = 0
        today_downloads["date"] = today

def get_xp_bar(xp):
    xp_in_level = xp % 100
    filled = xp_in_level // 10
    bar = "█" * filled + "░" * (10 - filled)
    return f"{bar} {xp_in_level}/100 XP"

def get_level_title(level):
    titles = {1: "🌱 Newbie", 2: "🎵 Listener", 3: "🎧 Music Fan",
              4: "🎸 Music Lover", 5: "🏆 Music Expert",
              6: "💎 Music Master", 7: "👑 Music Legend", 8: "🌟 BeatNova Star"}
    return titles.get(level, f"🔥 Level {level} Pro")

def get_badges(user_id):
    user = db.get_user(user_id) or {}
    downloads = user.get("downloads", 0)
    streak = user.get("streak", 0)
    favs = db.count_favorites(user_id)
    rated = db.user_rated_count(user_id)
    badges = []
    if downloads >= 1: badges.append("🎵 First Download")
    if downloads >= 10: badges.append("🎧 Music Fan")
    if downloads >= 50: badges.append("🎸 Music Lover")
    if downloads >= 100: badges.append("🥇 Music Master")
    if downloads >= 200: badges.append("💎 Legend")
    if downloads >= 500: badges.append("👑 BeatNova Star")
    if streak >= 3: badges.append("🔥 3-Day Streak")
    if streak >= 7: badges.append("⚡ 7-Day Streak")
    if streak >= 30: badges.append("👑 30-Day Streak")
    if favs >= 10: badges.append("⭐ Collector")
    if rated >= 5: badges.append("📊 Critic")
    return badges if badges else ["🌱 Just Starting!"]

def get_level(downloads):
    if downloads < 10: return "🥉 Beginner"
    elif downloads < 50: return "🥈 Music Lover"
    elif downloads < 100: return "🥇 Music Master"
    else: return "💎 Legend"

def get_user_genre_from_history(user_id):
    songs = db.get_history(user_id, 50)
    if not songs: return "Unknown"
    hindi = sum(1 for s in songs if any(w in s.lower() for w in ["hindi","tum","dil","pyar","ishq","tera","mera"]))
    english = sum(1 for s in songs if any(w in s.lower() for w in ["love","baby","night","light","heart"]))
    punjabi = sum(1 for s in songs if any(w in s.lower() for w in ["punjabi","jatt","kudi","yaar"]))
    counts = {"Hindi 🇮🇳": hindi, "English 🌍": english, "Punjabi 🎵": punjabi}
    return max(counts, key=counts.get)

def _normalize_song(s):
    """Normalize song dict to consistent format"""
    if not s: return None
    return {
        "name": s.get("name", "Unknown"),
        "primaryArtists": s.get("artist", s.get("primaryArtists", "Unknown")),
        "artist": s.get("artist", s.get("primaryArtists", "Unknown")),
        "album": s.get("album", "Unknown"),  # always string now
        "year": s.get("year", "Unknown"),
        "duration": s.get("duration", 0),
        "language": s.get("language", "Unknown"),
        "download_url": s.get("download_url", ""),
        "id": s.get("id", ""),
        "source": s.get("source", ""),
        "quality": s.get("quality", "320kbps"),
    }

def search_jiosaavn(query):
    """Legacy wrapper - uses apis.py"""
    results = apis.search_songs(query, 10)
    if not results: return None, None, None, None
    s = _normalize_song(results[0])
    title = f"{s['name']} - {s['primaryArtists']}"
    return s.get("download_url"), title, s.get("duration", 0), s

def search_jiosaavn_quality(query, quality="320"):
    """Legacy wrapper - uses apis.py"""
    s = apis.search_song_download(query, quality)
    if not s: return None, None, None, None
    s = _normalize_song(s)
    title = f"{s['name']} - {s['primaryArtists']}"
    return s.get("download_url"), title, s.get("duration", 0), s

def search_jiosaavn_multiple(query, limit=8):
    """Legacy wrapper - uses apis.py"""
    results = apis.search_songs(query, limit)
    # Convert to old format for backward compat
    out = []
    for s in results:
        out.append({
            "name": s["name"],
            "primaryArtists": s["artist"],
            "album": {"name": s.get("album","Unknown")},
            "year": s.get("year","Unknown"),
            "duration": s.get("duration", 0),
            "language": s.get("language","Unknown"),
            "downloadUrl": [{"link": s.get("download_url",""), "url": s.get("download_url","")}],
            "id": s.get("id",""),
        })
    return out[:limit]

def get_lyrics(query):
    try:
        parts = query.split("-")
        title = parts[0].strip()
        artist = parts[-1].strip() if len(parts) >= 2 else ""
        r = requests.get(f"https://lrclib.net/api/search?track_name={title}&artist_name={artist}",
                         headers={"User-Agent": "MusicBot/1.0"}, timeout=15)
        data = r.json()
        if not data: return None, None
        return data[0].get("plainLyrics"), f"{data[0].get('trackName', title)} - {data[0].get('artistName', artist)}"
    except Exception as e:
        print(f"Lyrics error: {e}")
        return None, None

def fetch_quote():
    try:
        r = requests.get("https://api.quotable.io/random?tags=music", timeout=10)
        data = r.json()
        return f'💬 "{data["content"]}"\n\n— {data["author"]}'
    except:
        return random.choice([
            '💬 "Without music, life would be a mistake." — Nietzsche',
            '💬 "Where words fail, music speaks." — H.C. Andersen',
            '💬 "One good thing about music, when it hits you, you feel no pain." — Bob Marley',
        ])

def download_song_file(url, title):
    dl_dir = "/tmp/beatnova_dl"
    os.makedirs(dl_dir, exist_ok=True)
    safe = "".join(c for c in title if c.isalnum() or c in " -_")[:50]
    path = f"{dl_dir}/{safe}.mp3"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "audio/mpeg, audio/*, */*",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }
    # Try up to 3 times
    for attempt in range(3):
        try:
            r = requests.get(url, stream=True, timeout=90,
                           headers=headers, allow_redirects=True)
            if r.status_code not in (200, 206):
                raise Exception(f"HTTP {r.status_code}")
            size = 0
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=131072):
                    if chunk:
                        f.write(chunk)
                        size += len(chunk)
            if size < 10000:  # Less than 10KB = failed
                raise Exception(f"File too small: {size} bytes")
            return path
        except Exception as e:
            print(f"[download] Attempt {attempt+1} failed: {e}")
            if attempt == 2:
                raise Exception(f"Download failed after 3 tries: {e}")
    return path

async def send_song(m, query, msg, quality="320", _user_id=None, _first_name=None):
    raw = await asyncio.to_thread(apis.search_song_download, query, quality)
    if not raw:
        await msg.edit("❌ Song not found! Try a different name.")
        return

    # Smart validation: if user specified artist in query, result must match that artist
    from collections import Counter
    query_lower = query.lower().strip()
    query_words = query_lower.split()
    result_name = raw.get("name", "").lower().strip()
    result_artist = raw.get("primaryArtists", raw.get("artist", "")).lower()

    # Detect if query contains artist words (words NOT in result name but in query)
    result_name_words = set(result_name.split())
    # Artist words = query words that don't appear in the result name
    # e.g. "pal pal talwiinder" → "pal","pal" in result → "talwiinder" is artist word
    potential_artist_words = [w for w in query_words if w not in result_name_words and len(w) > 2]

    wrong_song = False
    if potential_artist_words:
        # User typed artist name — check if result artist matches (or alias)
        artist_matched = False
        for aw in potential_artist_words:
            # Check direct match
            if aw in result_artist:
                artist_matched = True
                break
            # Check aliases
            aliases = apis.ARTIST_ALIASES.get(aw, [])
            for alias in aliases:
                if any(a in result_artist for a in alias.split()):
                    artist_matched = True
                    break
            if artist_matched:
                break
        if not artist_matched:
            wrong_song = True
            print(f"[send_song] ❌ Artist mismatch: wanted artist from '{potential_artist_words}' got '{result_artist}'")

    if wrong_song:
        print(f"[send_song] Trying yt-dlp for: {query}")
        yt_raw2 = await asyncio.to_thread(apis._ytdlp_download, query)
        if yt_raw2 and int(yt_raw2.get("duration", 0)) >= 90:
            raw = yt_raw2
            print(f"[send_song] ✅ yt-dlp: {raw.get('name')} ({raw.get('duration')}s)")
        else:
            print(f"[send_song] yt-dlp failed for: {query}")
            title_words = " ".join(query.split()[:2])
            await msg.edit(
                f"⚠️ **'{query}'** — sahi artist ka song nahi mila.\n\n"
                f"🔍 Try karo: `/download {title_words}`"
            )
            return

    # If result is still a short clip (<90s), force yt-dlp
    if int(raw.get("duration", 0)) > 0 and int(raw.get("duration", 0)) < 90:
        print(f"[send_song] Short clip ({raw.get('duration')}s) — forcing yt-dlp: {query}")
        yt_raw = await asyncio.to_thread(apis._ytdlp_download, query)
        if yt_raw and int(yt_raw.get("duration", 0)) >= 90:
            raw = yt_raw
        else:
            await msg.edit(f"❌ Full song nahi mila `{query}` ke liye. Try: `/download {' '.join(query.split()[:2])}`")
            return

    # Preserve _local_path BEFORE normalize strips it (yt-dlp sets this)
    local_path = raw.get("_local_path")

    raw = _normalize_song(raw)
    dl_url = raw.get("download_url")
    title = f"{raw['name']} - {raw['primaryArtists']}"
    duration = raw.get("duration", 0)
    song_data = raw

    if not dl_url:
        await msg.edit("❌ Song not found! Try a different name.")
        return

    mins, secs = duration // 60, duration % 60

    user_id = _user_id or (m.from_user.id if m.from_user else None)
    first_name = _first_name or (get_user_name(m) if m.from_user else "User")
    if not user_id:
        await msg.edit("❌ Could not identify user. Please try again.")
        return

    is_first = db.get_user(user_id) is None or db.get_user(user_id)["downloads"] == 0

    loading_seq = random.choice(LOADING_MESSAGES)
    try:
        await msg.edit(f"**{loading_seq[0]}**")
    except: pass

    # If yt-dlp already downloaded to local file, use it directly
    if local_path and os.path.exists(local_path):
        path = local_path
    else:
        try:
            path = await asyncio.wait_for(
                asyncio.to_thread(download_song_file, dl_url, title),
                timeout=120
            )
        except asyncio.TimeoutError:
            await msg.edit(f"❌ **Timeout!** Server slow hai.\n🔄 Try: `/download {query}`")
            return
        except Exception as e:
            err = str(e)
            try:
                await msg.edit(f"**{loading_seq[1]}** (switching source...)")
                song_alt = await asyncio.to_thread(apis.search_song_download, query, quality)
                local_alt = song_alt.get("_local_path") if song_alt else None
                if song_alt and song_alt.get("download_url") and song_alt["download_url"] != dl_url:
                    if local_alt and os.path.exists(local_alt):
                        path = local_alt
                    else:
                        path = await asyncio.wait_for(
                            asyncio.to_thread(download_song_file, song_alt["download_url"], title),
                            timeout=120
                        )
                else:
                    raise Exception(err)
            except Exception as e2:
                await msg.edit(f"❌ **Download failed!**\n`{str(e2)[:80]}`\n\n🔄 Try: `/download {query}`")
                return

    # Step 3: Update stats AFTER successful download
    update_today_stats()
    today_downloads["count"] += 1
    db.increment_bot_stat("total_downloads")
    db.ensure_user(user_id, first_name)
    db.update_streak(user_id)
    db.increment_downloads(user_id)
    db.add_history(user_id, title)
    db.save_last_downloaded(user_id, title, f"{mins}:{secs:02d}", first_name)
    db.increment_song_downloads(title)

    # XP system
    xp_earned = XP_REWARDS["download"]
    if is_first: xp_earned += XP_REWARDS["first_download"]
    total_xp, new_level = db.add_xp(user_id, xp_earned)

    # Group stats — only when m.chat is a real group message (not a bot-sent DM)
    try:
        if m.chat and m.chat.type.name in ("GROUP", "SUPERGROUP"):
            db.update_group_stats(m.chat.id, user_id, first_name)
    except: pass

    if song_data:
        album_raw = song_data.get("album", "Unknown")
        album = album_raw.get("name", "Unknown") if isinstance(album_raw, dict) else (str(album_raw) if album_raw else "Unknown")
        year = str(song_data.get("year", "Unknown") or "Unknown")
    else:
        album = "Unknown"
        year = "Unknown"

    reaction_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Lyrics", callback_data=f"lyr_{title[:35]}"),
         InlineKeyboardButton("🎵 Similar", callback_data=f"sim_{title[:40]}")],
        [InlineKeyboardButton("⭐ Save", callback_data=f"save_{title[:40]}"),
         InlineKeyboardButton("👍 Like", callback_data=f"react_like_{title[:25]}"),
         InlineKeyboardButton("🔥 Fire", callback_data=f"react_fire_{title[:25]}"),
         InlineKeyboardButton("💔 Sad", callback_data=f"react_sad_{title[:25]}")],
    ])

    try:
        await msg.edit(f"**{loading_seq[2]}**")
    except: pass

    is_group = False
    try:
        is_group = m.chat.type.name in ("GROUP", "SUPERGROUP")
    except: pass

    # Split title into song name and artist for Telegram display
    song_name = song_data.get("name", title) if song_data else title
    artist_name = song_data.get("primaryArtists", song_data.get("artist", "")) if song_data else ""
    if not artist_name and " - " in title:
        parts_t = title.split(" - ", 1)
        song_name = parts_t[0].strip()
        artist_name = parts_t[1].strip()

    try:
        await app.send_audio(
            m.chat.id, path,
            caption=(f"🎵 **{title}**\n"
                     f"💿 {album} | 📅 {year}\n"
                     f"⏱ {mins}:{secs:02d} | 🎧 {quality}kbps\n"
                     f"👤 {first_name}\n"
                     f"━━━━━━━━━━━━━━━\n"
                     f"🎧 Powered by BeatNova"),
            title=song_name,
            performer=artist_name,
            duration=duration,
            reply_markup=reaction_keyboard
        )
    except Exception as e:
        err_str = str(e)
        # Any restriction → send to PM instead
        should_dm = any(x in err_str for x in [
            "SLOWMODE_WAIT", "CHAT_SEND_AUDIO", "CHAT_WRITE_FORBIDDEN",
            "403", "Forbidden", "USER_BANNED", "CHAT_RESTRICTED",
            "RIGHT", "permission", "flood", "slow"
        ])
        if should_dm or is_group:
            DM_CAPTIONS = [
                "🎵 Slid into your DMs with the goods 😎",
                "📩 Group was being difficult, so here you go! 🎧",
                "🚀 Delivered straight to your DMs — no drama!",
                "🎶 The group couldn't handle it, but your DMs can 😄",
                "💌 Special delivery — just for you!",
                "🤫 Between us — here's your song!",
            ]
            GROUP_MSGS = [
                f"✅ **{title}** — sent to your PM! 📩",
                f"🎵 **{title}** — check your DMs! 😎",
                f"📩 Dropped it in your DMs, {first_name}!",
                f"🚀 **{title}** is in your inbox! Check DMs 👆",
            ]
            try:
                await app.send_audio(
                    user_id, path,
                    caption=(f"🎵 **{title}**\n"
                             f"💿 {album} | 📅 {year}\n"
                             f"⏱ {mins}:{secs:02d} | 🎧 {quality}kbps\n\n"
                             f"{random.choice(DM_CAPTIONS)}\n"
                             f"━━━━━━━━━━━━━━━\n"
                             f"🎧 {BOT_NAME} | {BOT_USERNAME}"),
                    title=song_name,
                    performer=artist_name,
                    duration=duration,
                    reply_markup=reaction_keyboard
                )
                try:
                    await msg.edit(random.choice(GROUP_MSGS))
                except: pass
            except Exception as e2:
                await msg.edit(
                    f"⚠️ **Couldn't send here or in DM!**\n\n"
                    f"Please start a PM with me first: {BOT_USERNAME}\n"
                    f"Then try again 🎵"
                )
        else:
            await msg.edit(f"❌ Error: `{err_str[:80]}`")
        try: os.remove(path)
        except: pass
        return

    # Delete "Sending" message
    try:
        await msg.delete()
    except:
        try: await msg.edit("✅")
        except: pass

    # XP notification — sirf private chat mein, group mein spam nahi
    user = db.get_user(user_id)
    streak_bonus = ""
    if user and user["streak"] == 3:
        db.add_xp(user_id, XP_REWARDS["streak_3"])
        streak_bonus = " 🔥+20 streak bonus!"
    elif user and user["streak"] == 7:
        db.add_xp(user_id, XP_REWARDS["streak_7"])
        streak_bonus = " ⚡+50 streak bonus!"

    if is_first:
        xp_msg = (f"🎉 **First Download!** +{xp_earned} XP 🌟\n"
                  f"🏅 Badge: **Music Explorer**{streak_bonus}")
        try: await m.reply(xp_msg)
        except: pass
    elif not is_group:
        try: await m.reply(f"✨ +{xp_earned} XP{streak_bonus} | {get_xp_bar(total_xp)} Lv.{new_level}")
        except: pass

    # Auto-suggest similar songs
    try:
        if song_data:
            artist_s = song_data.get("primaryArtists", song_data.get("artist", "")).split(",")[0].strip()
            song_s = song_data.get("name", query)
            similar = await asyncio.to_thread(apis.get_similar_tracks, artist_s, song_s)
            if similar and len(similar) >= 2:
                suggest_text = "💡 **You may also like:**\n"
                for t in similar[:3]:
                    suggest_text += f"• {t['name']} — {t['artist']}\n"
                suggest_text += f"\n`/download [song name]`"
                await m.reply(suggest_text)
    except:
        pass

    try: os.remove(path)
    except: pass

# ========== CALLBACKS ==========

@app.on_callback_query(filters.regex(r"^dl_"))
async def dl_callback(_, cb):
    song = cb.data[3:]
    await cb.answer("Downloading...")
    msg = await cb.message.reply(f"⬇️ Searching `{song}`...")
    await send_song(cb.message, song, msg)

@app.on_callback_query(filters.regex(r"^save_"))
async def save_callback(_, cb):
    song_title = cb.data[5:]
    user_id = cb.from_user.id
    db.ensure_user(user_id, cb.from_user.first_name)
    if db.is_favorite(user_id, song_title):
        await cb.answer("⭐ Already in favorites!", show_alert=False)
        return
    db.add_favorite(user_id, song_title)
    db.increment_song_favorites(song_title)
    await cb.answer("⭐ Saved to favorites!", show_alert=True)

@app.on_callback_query(filters.regex(r"^sim_"))
async def similar_callback(_, cb):
    song_title = cb.data[4:]
    msg = await cb.message.reply("🔍 Finding similar songs...")
    try:
        similar_tracks = await asyncio.to_thread(apis.get_similar_tracks, "", song_title)
        if similar_tracks and len(similar_tracks) >= 3:
            text = f"🎵 **Similar to** `{song_title}`:\n\n"
            for i, t in enumerate(similar_tracks[:8], 1):
                text += f"{i}. **{t['name']}** — {t['artist']}\n"
        else:
            results = search_jiosaavn_multiple(f"songs like {song_title}", 7)
            results += search_jiosaavn_multiple(f"similar {song_title} hindi", 3)
            seen, unique = set(), []
            for s in results:
                if s["name"] not in seen:
                    seen.add(s["name"])
                    unique.append(s)
            if not unique:
                await msg.edit("❌ No similar songs found!")
                await cb.answer()
                return
            text = f"🎵 **Similar to** `{song_title}`:\n\n"
            for i, s in enumerate(unique[:8], 1):
                artist = s.get("primaryArtists", s.get("artist", "Unknown"))
                text += f"{i}. **{s['name']}** — {artist}\n"
        text += "\n📥 `/download [song name]`"
        await msg.edit(text)
    except Exception as e:
        await msg.edit("❌ Could not fetch similar songs!")
        print(f"[similar_cb] {e}")
    await cb.answer()

@app.on_callback_query(filters.regex(r"^lyr_"))
async def lyrics_callback(_, cb):
    song_title = cb.data[4:]
    msg = await cb.message.reply("🔍 Fetching lyrics...")
    lyrics_text, title = get_lyrics(song_title)
    if not lyrics_text:
        await msg.edit("❌ Lyrics not found!")
        await cb.answer()
        return
    header = f"📝 **Lyrics: {title}**\n\n"
    full = header + lyrics_text
    if len(full) <= 4096:
        await msg.edit(full)
    else:
        await msg.edit(header + lyrics_text[:4000])
        remaining = lyrics_text[4000:]
        while remaining:
            await cb.message.reply(remaining[:4096])
            remaining = remaining[4096:]
    await cb.answer()

@app.on_callback_query(filters.regex(r"^react_"))
async def reaction_callback(_, cb):
    parts = cb.data.split("_")
    reaction = parts[1]
    song = "_".join(parts[2:])
    db.ensure_user(cb.from_user.id, cb.from_user.first_name)
    db.save_reaction(cb.from_user.id, song, reaction)
    all_reactions = db.get_song_reactions(song)
    likes = all_reactions.get("like", 0)
    fires = all_reactions.get("fire", 0)
    sads = all_reactions.get("sad", 0)
    emoji_map = {"like": "👍", "fire": "🔥", "sad": "💔"}
    await cb.answer(f"{emoji_map[reaction]} Reacted!", show_alert=False)
    try:
        await cb.message.edit_reply_markup(InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Download", callback_data=f"dl_{song[:30]}"),
             InlineKeyboardButton("📝 Lyrics", callback_data=f"lyr_{song[:35]}")],
            [InlineKeyboardButton("🎵 Similar", callback_data=f"sim_{song[:40]}"),
             InlineKeyboardButton("⭐ Save", callback_data=f"save_{song[:40]}")],
            [InlineKeyboardButton(f"👍 {likes}", callback_data=f"react_like_{song[:25]}"),
             InlineKeyboardButton(f"🔥 {fires}", callback_data=f"react_fire_{song[:25]}"),
             InlineKeyboardButton(f"💔 {sads}", callback_data=f"react_sad_{song[:25]}")],
        ]))
    except: pass

@app.on_callback_query(filters.regex("dl_birthday"))
async def birthday_dl(_, cb):
    await cb.answer()
    msg = await cb.message.reply("⬇️ Downloading...")
    await send_song(cb.message, "Baar Baar Din Yeh Aaye", msg)

@app.on_callback_query(filters.regex(r"^rate_"))
async def rate_callback(_, cb):
    parts = cb.data.split("_")
    rating, song = int(parts[1]), "_".join(parts[2:])
    db.ensure_user(cb.from_user.id, cb.from_user.first_name)
    db.save_rating(cb.from_user.id, song, rating)
    db.add_xp(cb.from_user.id, XP_REWARDS["rate_song"])
    avg, count = db.get_avg_rating(song)
    await cb.answer(f"✅ Rated {rating}⭐ +{XP_REWARDS['rate_song']} XP!", show_alert=False)
    try:
        await cb.message.edit_reply_markup(InlineKeyboardMarkup([[
            InlineKeyboardButton(f"⭐ {avg:.1f}/5 ({count} votes)", callback_data="none")
        ]]))
    except: pass

@app.on_callback_query(filters.regex(r"^qual_"))
async def quality_callback(_, cb):
    parts = cb.data.split("_")
    quality, song = parts[1], "_".join(parts[2:])
    await cb.answer(f"Downloading {quality}kbps...", show_alert=False)
    msg = await cb.message.reply(f"⬇️ Downloading `{song}` in **{quality}kbps**...")
    await send_song(cb.message, song, msg, quality)

@app.on_callback_query(filters.regex(r"^vote_"))
async def vote_callback(_, cb):
    parts = cb.data.split("_")
    group_id = int(parts[1])
    choice = int(parts[2])
    user_id = cb.from_user.id
    if group_id not in group_votes:
        await cb.answer("Vote ended!", show_alert=False)
        return
    group_votes[group_id]["votes"][user_id] = choice
    await cb.answer(f"✅ Voted for option {choice+1}!", show_alert=False)

@app.on_callback_query(filters.regex(r"^help_(?!back)"))
async def help_category(_, cb):
    cat = cb.data[5:]
    texts = {
        "download": (
            "🎵 **Download & Search**\n\n"
            "📥 `/download [song]`\n🎧 `/quality [song]`\n🎵 `/preview [song]`\n"
            "🔍 `/search [song]`\nℹ️ `/info [song]`\n📝 `/lyrics [song-artist]`\n"
            "📦 `/batch`\n🎛 `/remix [song]`\n🎸 `/acoustic [song]`\n"
            "🎤 `/cover [song]`\n🎼 `/lofi [song]`"
        ),
        "discover": (
            "🌍 **Browse & Discover**\n\n"
            "🤖 `/ai_playlist`\n💿 `/album`\n💿 `/albuminfo`\n🎤 `/artist`\nℹ️ `/artistinfo`\n"
            "🎂 `/birthday`\n🔗 `/chain`\n📅 `/daily`\n🌐 `/english` `/hindi` `/punjabi`\n"
            "🔤 `/findlyrics`\n🎸 `/genre`\n🎼 `/karaoke`\n🔤 `/letter`\n🎭 `/mood`\n"
            "🆕 `/newreleases`\n🌙 `/night`\n🎵 `/playlist`\n🎲 `/random`\n🎯 `/recommend`\n"
            "🌍 `/regional`\n⏱ `/short`\n🎵 `/similar`\n🎤 `/similarartist`\n"
            "🏆 `/topartist`\n🎬 `/topbollywood`\n🇮🇳 `/topindia`\n🔥 `/top2025`\n"
            "🔥 `/trendingartist`\n🌍 `/trending`\n🎭 `/vibe`\n📅 `/year`\n💿 `/discography`"
        ),
        "games": (
            "🎮 **Music Games**\n\n"
            "🎯 `/guesssong` — Lyrics se song guess karo\n"
            "🎮 `/musicquiz` — A/B/C/D music quiz\n"
            "🎤 `/artistquiz` — Kaunse artist ne gaaya?\n"
            "🎯 `/fillblank` — Lyrics mein blank bharo\n"
            "📅 `/yeargame` — Song ka year guess karo\n"
            "📅 `/challenge` — Daily challenge\n"
            "🏆 `/tournament` — Song tournament\n"
            "⚖️ `/compare [s1] | [s2]` — Compare songs\n\n"
            "**👥 Group Music Games:**\n"
            "🎮 `/groupquiz` — Group quiz\n"
            "⚔️ `/songbattle [s1] | [s2]` — Song battle\n"
            "📊 `/votesong` — Group vote\n\n"
            "**🎉 Party Mode:**\n"
            "🎉 `/party` — Party mode\n"
            "➕ `/addsong [song]` — Queue mein add\n"
            "📋 `/partyqueue` | ⏭ `/skipparty` | 🛑 `/stopparty`\n\n"
            "**⭐ Ratings:**\n"
            "⭐ `/rate [song]` | 🏆 `/topsongs`"
        ),
        "fungames": (
            "🕹 **Fun Games**\n\n"
            "🎰 `/slots` — Slot machine! Teen same = jackpot!\n"
            "🎲 `/dice` — Dice roll (default 6, try `/dice 20`)\n"
            "🔢 `/guess` — 1-100 number guess karo\n"
            "💣 `/bomb` — Bomb pass karo group mein!\n"
            "   └ `/passbomb @user` — Pass karo\n"
            "⚔️ `/duel @user` — 1v1 HP battle\n"
            "   └ `/attack` — Attack karo\n"
            "   └ `/defend` — Block karo\n"
            "🟩 `/wordle` — 5-letter word guess game\n\n"
            "🏆 Jitne pe XP milte hain!\n"
            "💬 `/quote` | 🎵 `/musicfact` | 🥚 `/easteregg`"
        ),
        "account": (
            "👤 **My Account**\n\n"
            "🏅 `/badges`\n💾 `/favorites`\n📊 `/genrestats`\n📜 `/history`\n"
            "🤝 `/invite`\n🎵 `/lastdownload`\n🏆 `/leaderboard`\n👤 `/mystats`\n"
            "📝 `/note`\n👤 `/profile`\n🗑 `/removefav`\n⭐ `/save`\n📤 `/share`\n"
            "🔔 `/subscribe`\n🔕 `/unsubscribe`\n🔥 `/streak`\n🎁 `/dailyreward`\n"
            "📋 `/wishlist`\n📋 `/mywishlist`"
        ),
        "stats": (
            "📊 **Stats & Info**\n\n"
            "📊 `/activestats`\n⏱ `/ping`\n📤 `/share`\n🎵 `/songstats`\n"
            "📊 `/stats`\n📅 `/todaystats`\n⏰ `/uptime`\n\n"
            "**👥 Group Stats:**\n"
            "🏆 `/gleaderboard`\n📊 `/groupstats`\n🥇 `/topuser`"
        )
    }
    text = texts.get(cat, "❌ Unknown category!")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="help_back")]])
    await cb.message.edit_text(text, reply_markup=keyboard)
    await cb.answer()

@app.on_callback_query(filters.regex(r"^help_back$"))
async def help_back(_, cb):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 Download & Search", callback_data="help_download"),
         InlineKeyboardButton("🌍 Discover", callback_data="help_discover")],
        [InlineKeyboardButton("🎮 Music Games", callback_data="help_games"),
         InlineKeyboardButton("🕹 Fun Games", callback_data="help_fungames")],
    [InlineKeyboardButton("👤 My Account", callback_data="help_account")],
        [InlineKeyboardButton("📊 Stats & Info", callback_data="help_stats")]
    ])
    await cb.message.edit_text(f"❓ **{BOT_NAME} Help Menu**\n\nChoose a category:", reply_markup=keyboard)
    await cb.answer()

@app.on_callback_query(filters.regex(r"^none$"))
async def none_cb(_, cb):
    await cb.answer()

# ========== COMMANDS A to Z ==========

# A


@safe_handler
@app.on_message(filters.command("activestats"))
async def activestats(_, m: Message):
    users = db.get_all_users()
    if not users:
        await m.reply("❌ No data yet!")
        return
    text = "📊 **Most Active Users:**\n\n"
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    for i, u in enumerate(users[:5], 0):
        text += f"{medals[i]} **{u['name']}** — {u['downloads']} downloads\n"
    await m.reply(text)


@safe_handler
@app.on_message(filters.command("ai_playlist"))
async def ai_playlist(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("🤖 **Choose activity:**\n`/ai_playlist gym` 💪\n`/ai_playlist study` 📚\n`/ai_playlist heartbreak` 💔\n`/ai_playlist sleep` 😴\n`/ai_playlist party` 🎉\n`/ai_playlist romantic` 💕\n`/ai_playlist morning` 🌅\n`/ai_playlist roadtrip` 🚗")
        return
    activity = parts[1].strip().lower()
    queries = {"gym": "workout gym motivation", "study": "study focus calm instrumental",
               "heartbreak": "heartbreak sad emotional hindi", "sleep": "sleep relaxing calm",
               "party": "party dance upbeat hindi", "romantic": "romantic love songs",
               "morning": "morning fresh motivational", "roadtrip": "roadtrip travel songs"}
    emojis = {"gym": "💪", "study": "📚", "heartbreak": "💔", "sleep": "😴", "party": "🎉", "romantic": "💕", "morning": "🌅", "roadtrip": "🚗"}
    if activity not in queries:
        await m.reply("❌ Available: `gym` `study` `heartbreak` `sleep` `party` `romantic` `morning` `roadtrip`")
        return
    msg = await m.reply(f"🤖 **Creating AI Playlist: {activity}...**")
    results = search_jiosaavn_multiple(queries[activity], 8)
    if not results:
        await msg.edit("❌ No songs found!")
        return
    text = f"🤖 **AI Playlist: {activity.capitalize()}** {emojis[activity]}\n\n"
    for i, s in enumerate(results, 1):
        text += f"{i}. **{s['name']}** - {s['primaryArtists']}\n"
    text += "\n📥 `/download [song name]`"
    await msg.edit(text)





@safe_handler
@app.on_message(filters.command("artistquiz"))
async def artistquiz(_, m: Message):
    msg = await m.reply("🎤 **Preparing Artist Quiz...**")
    chat_id = m.chat.id
    q1 = random.choice(QUIZ_QUERIES)
    q2 = random.choice([q for q in QUIZ_QUERIES if q != q1])
    results = search_jiosaavn_multiple(q1, 15)
    results += search_jiosaavn_multiple(q2, 10)
    seen, unique = set(), []
    for s in results:
        if s["name"] not in seen:
            seen.add(s["name"])
            unique.append(s)
    if not unique:
        await msg.edit("❌ Could not fetch!")
        return
    correct = random.choice(unique)
    correct_song = correct["name"]
    correct_artist = correct.get("primaryArtists", correct.get("artist","Unknown")).split(",")[0].strip()
    # Get wrong artists from results
    wrong_from_results = list(set([
        s.get("primaryArtists", s.get("artist","")).split(",")[0].strip()
        for s in unique
        if s.get("primaryArtists", s.get("artist","")).split(",")[0].strip() != correct_artist
    ]))
    # Also add from pool for variety
    wrong_from_pool = [a for a in ARTIST_POOL if a.lower() != correct_artist.lower()]
    all_wrong = list(set(wrong_from_results + wrong_from_pool))
    random.shuffle(all_wrong)
    wrong_options = all_wrong[:3]
    # Always ensure exactly 4 options
    options = [correct_artist] + wrong_options
    while len(options) < 4:
        options.append(random.choice(ARTIST_POOL))
    options = options[:4]
    random.shuffle(options)
    if correct_artist not in options:
        options[0] = correct_artist
        random.shuffle(options)
    labels = ["A", "B", "C", "D"]
    correct_idx = options.index(correct_artist)
    active_quiz[chat_id] = {
        "answer": correct_artist.lower(), "title": correct_song,
        "artist": correct_artist, "type": "artistquiz", "options": options
    }
    text = f"🎤 **Artist Quiz!**\n\n🎵 **Song:** {correct_song}\n\n❓ **Kisne gaaya ye song?**\n\n"
    for i, opt in enumerate(options):
        text += f"**{labels[i]}.** {opt}\n"
    text += "\n💭 Reply A, B, C or D!\n⏱ 20 seconds!"
    await msg.edit(text)
    await asyncio.sleep(20)
    if chat_id in active_quiz and active_quiz[chat_id].get("type") == "artistquiz":
        del active_quiz[chat_id]
        await m.reply(f"⏱ **Time's up!**\nAnswer: **{labels[correct_idx]}. {correct_artist}**")


# A — MERGED COMMANDS

@safe_handler
@app.on_message(filters.command("artist"))
async def artist(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("❌ Example: `/artist Arijit Singh`")
        return
    query = parts[1].strip()
    msg = await m.reply(f"🎤 **Fetching artist:** `{query}`...")
    try:
        info = await asyncio.to_thread(apis.get_artist_info, query)
        top_tracks = await asyncio.to_thread(apis.get_artist_top_tracks, query, 8)
        similar = await asyncio.to_thread(apis.get_similar_artists, query)
        text = f"🎤 **{query}**\n\n"
        if info and info.get("listeners"):
            listeners = info["listeners"]
            if str(listeners).isdigit():
                listeners = f"{int(listeners):,}"
            text += f"👥 Listeners: {listeners}\n"
        if info and info.get("tags"):
            text += f"🎸 Genres: {', '.join(info['tags'][:3])}\n"
        if info and info.get("bio"):
            text += f"\n📖 {info['bio'][:200]}...\n"
        if top_tracks:
            text += "\n**🏆 Top Songs:**\n"
            for i, t in enumerate(top_tracks[:8], 1):
                text += f"{i}. {t['name']}\n"
        if similar:
            text += f"\n🎵 **Similar Artists:** {', '.join(similar[:4])}"
        text += f"\n\n📥 `/download [song name]`"
        await msg.edit(text)
    except Exception as e:
        await msg.edit(f"❌ Could not fetch! Try again.")

@safe_handler
@app.on_message(filters.command("album"))
async def album(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("❌ Example: `/album Aashiqui 2`")
        return
    query = parts[1].strip()
    msg = await m.reply(f"💿 **Fetching album:** `{query}`...")
    results = await asyncio.to_thread(search_jiosaavn_multiple, f"{query} album", 10)
    if not results:
        await msg.edit("❌ Album not found!")
        return
    album_name = results[0].get("album", {}).get("name", query) if isinstance(results[0].get("album"), dict) else query
    artist = results[0].get("primaryArtists", "Unknown")
    year = results[0].get("year", "Unknown")
    lang = results[0].get("language", "Unknown").capitalize()
    total_dur = sum(int(s.get("duration", 0)) for s in results)
    text = (f"💿 **{album_name}**\n\n"
            f"👤 **Artist:** {artist}\n"
            f"📅 **Year:** {year} | 🌐 {lang}\n"
            f"🎵 **Songs:** {len(results)}+ | ⏱ ~{total_dur//60} mins\n\n"
            f"**Tracklist:**\n")
    for i, s in enumerate(results[:10], 1):
        d = int(s["duration"])
        text += f"{i}. {s['name']} ({d//60}:{d%60:02d})\n"
    text += "\n📥 `/download [song name]`"
    await msg.edit(text)

@safe_handler
@app.on_message(filters.command("lang"))
async def lang_cmd(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply(
            "🌐 **Language Songs**\n\n"
            "Usage: `/lang [language]`\n\n"
            "Examples:\n"
            "`/lang hindi` `//lang punjabi` `/lang english`\n"
            "`/lang tamil` `/lang telugu` `/lang marathi`\n"
            "`/lang bengali` `/lang bhojpuri` `/lang korean`"
        )
        return
    language = parts[1].strip().lower()
    msg = await m.reply(f"🌐 **Fetching {language.capitalize()} songs...**")
    results = await asyncio.to_thread(apis.search_by_language, language, 10)
    if not results:
        await msg.edit(f"❌ No songs found for `{language}`!")
        return
    text = f"🌐 **Top {language.capitalize()} Songs:**\n\n"
    for i, s in enumerate(results[:10], 1):
        artist = s.get("artist", s.get("primaryArtists", "Unknown"))
        text += f"{i}. **{s['name']}** — {artist}\n"
    text += "\n📥 `/download [song name]`"
    await msg.edit(text)

@safe_handler
@app.on_message(filters.command("srec"))
async def srec(_, m: Message):
    """Similar songs + Recommendations merged"""
    parts = m.text.split(None, 1)
    user_id = m.from_user.id
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        # No song given — use history for recommendations
        msg = await m.reply("🎯 **Finding recommendations for you...**")
        try:
            hist = db.get_history(user_id, 5)
            if hist:
                last = hist[0]
                artist = last.split(" - ")[-1].strip() if " - " in last else ""
                song = last.split(" - ")[0].strip() if " - " in last else last
                results = []
                if artist:
                    results += await asyncio.to_thread(search_jiosaavn_multiple, f"{artist} songs hits", 5)
                results += await asyncio.to_thread(search_jiosaavn_multiple, f"songs like {song}", 5)
                seen, unique = set(), []
                for s in results:
                    n = s["name"]
                    if n not in seen and n.lower() != song.lower():
                        seen.add(n)
                        unique.append(s)
                text = f"🎧 **Because you listened to** `{song}`:\n\n"
            else:
                results = await asyncio.to_thread(search_jiosaavn_multiple, "top hindi songs popular", 8)
                seen, unique = set(), []
                for s in results:
                    if s["name"] not in seen:
                        seen.add(s["name"])
                        unique.append(s)
                text = "🎯 **Top Picks for You:**\n\n"
            for i, s in enumerate(unique[:6], 1):
                artist = s.get("primaryArtists", s.get("artist", "Unknown"))
                text += f"{i}. **{s['name']}** — {artist}\n"
            text += "\n👇 Tap to download:"
            num_emojis = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣"]
            btn_rows = []
            row = []
            for i, s in enumerate(unique[:6]):
                row.append(InlineKeyboardButton(num_emojis[i], callback_data=f"dl_{s['name'][:30]}"))
                if len(row) == 3:
                    btn_rows.append(row)
                    row = []
            if row:
                btn_rows.append(row)
            await msg.edit(text, reply_markup=InlineKeyboardMarkup(btn_rows))
        except Exception as e:
            await msg.edit("❌ Could not fetch! Try again.")
        return
    query = parts[1].strip()
    msg = await m.reply(f"🎵 **Finding similar to:** `{query}`...")
    try:
        _, _, _, song_data = await asyncio.to_thread(search_jiosaavn, query)
        artist_name = ""
        song_name = query
        if song_data:
            artist_name = song_data.get("artist", song_data.get("primaryArtists", "")).split(",")[0].strip()
            song_name = song_data.get("name", query)
        similar_tracks = await asyncio.to_thread(apis.get_similar_tracks, artist_name, song_name)
        if similar_tracks and len(similar_tracks) >= 3:
            text = f"🎵 **Similar to** `{song_name}`:\n\n"
            for i, t in enumerate(similar_tracks[:8], 1):
                text += f"{i}. **{t['name']}** — {t['artist']}\n"
        else:
            results = []
            if artist_name:
                results += await asyncio.to_thread(search_jiosaavn_multiple, f"{artist_name} best songs", 5)
            results += await asyncio.to_thread(search_jiosaavn_multiple, f"songs like {song_name}", 5)
            seen, unique = set(), []
            for s in results:
                if s["name"] not in seen and s["name"].lower() != song_name.lower():
                    seen.add(s["name"])
                    unique.append(s)
            text = f"🎵 **Similar to** `{song_name}`:\n\n"
            for i, s in enumerate(unique[:8], 1):
                artist = s.get("primaryArtists", s.get("artist", "Unknown"))
                text += f"{i}. **{s['name']}** — {artist}\n"
        text += "\n👇 Tap to download:"
        num_emojis = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣"]
        btn_rows2 = []
        row2 = []
        result_list = similar_tracks[:6] if similar_tracks and len(similar_tracks) >= 3 else unique[:6]
        for i, t in enumerate(result_list):
            name = t.get("name", "") if isinstance(t, dict) else t
            row2.append(InlineKeyboardButton(num_emojis[i], callback_data=f"dl_{name[:30]}"))
            if len(row2) == 3:
                btn_rows2.append(row2)
                row2 = []
        if row2:
            btn_rows2.append(row2)
        await msg.edit(text, reply_markup=InlineKeyboardMarkup(btn_rows2) if btn_rows2 else None)
    except Exception as e:
        await msg.edit("❌ Could not fetch! Try again.")

@safe_handler
@app.on_message(filters.command("rlc"))
async def rlc(_, m: Message):
    """Remix / Lofi / Cover merged"""
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply(
            "🎛 **Remix / Lo-Fi / Cover / Acoustic**\n\n"
            "Usage: `/rlc [song] [type]`\n\n"
            "Examples:\n"
            "`/rlc Tum Hi Ho remix`\n"
            "`/rlc Tum Hi Ho lofi`\n"
            "`/rlc Tum Hi Ho cover`\n"
            "`/rlc Tum Hi Ho acoustic`\n\n"
            "Or just: `/rlc Tum Hi Ho` — bot will show all options"
        )
        return
    text_parts = parts[1].strip().rsplit(None, 1)
    version_map = {"remix": "remix", "lofi": "lofi", "lo-fi": "lofi", "cover": "cover", "acoustic": "acoustic", "unplugged": "acoustic"}
    if len(text_parts) == 2 and text_parts[1].lower() in version_map:
        query = text_parts[0].strip()
        version = version_map[text_parts[1].lower()]
    else:
        query = parts[1].strip()
        version = None

    if version:
        msg = await m.reply(f"🎛 **Searching {version}:** `{query}`...")
        queries_map = {
            "remix": [f"{query} remix", f"{query} dj remix"],
            "lofi": [f"{query} lofi", f"lofi {query}"],
            "cover": [f"{query} cover", f"{query} cover version"],
            "acoustic": [f"{query} acoustic", f"{query} unplugged"],
        }
        results = []
        for q in queries_map[version]:
            results += await asyncio.to_thread(search_jiosaavn_multiple, q, 3)
        seen, unique = set(), []
        for s in results:
            if s["name"] not in seen:
                seen.add(s["name"])
                unique.append(s)
        if not unique:
            await msg.edit(f"❌ No {version} found!\n💡 Try: `/download {query} {version}`")
            return
        emoji_map = {"remix": "🎛", "lofi": "🎵", "cover": "🎤", "acoustic": "🎸"}
        text = f"{emoji_map[version]} **{version.capitalize()} of:** `{query}`\n\n"
        for i, s in enumerate(unique[:6], 1):
            text += f"{i}. **{s['name']}** — {s['primaryArtists']}\n"
        text += "\n📥 `/download [song name]`"
        await msg.edit(text)
    else:
        # Show all options with inline buttons
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎛 Remix", callback_data=f"rlc_remix_{query[:30]}"),
                InlineKeyboardButton("🎵 Lo-Fi", callback_data=f"rlc_lofi_{query[:30]}"),
            ],
            [
                InlineKeyboardButton("🎤 Cover", callback_data=f"rlc_cover_{query[:30]}"),
                InlineKeyboardButton("🎸 Acoustic", callback_data=f"rlc_acoustic_{query[:30]}"),
            ],
        ])
        await m.reply(f"🎛 **Choose version for:** `{query}`", reply_markup=keyboard)

@app.on_callback_query(filters.regex(r"^rlc_(remix|lofi|cover|acoustic)_"))
async def rlc_callback(_, cb):
    parts = cb.data.split("_", 2)
    version = parts[1]
    query = parts[2]
    await cb.answer(f"Searching {version}...", show_alert=False)
    msg = await cb.message.reply(f"🎛 **Searching {version}:** `{query}`...")
    queries_map = {
        "remix": [f"{query} remix", f"{query} dj remix"],
        "lofi": [f"{query} lofi", f"lofi {query}"],
        "cover": [f"{query} cover", f"{query} cover version"],
        "acoustic": [f"{query} acoustic", f"{query} unplugged"],
    }
    results = []
    for q in queries_map[version]:
        results += await asyncio.to_thread(search_jiosaavn_multiple, q, 3)
    seen, unique = set(), []
    for s in results:
        if s["name"] not in seen:
            seen.add(s["name"])
            unique.append(s)
    if not unique:
        await msg.edit(f"❌ No {version} found!\n💡 Try: `/download {query} {version}`")
        return
    emoji_map = {"remix": "🎛", "lofi": "🎵", "cover": "🎤", "acoustic": "🎸"}
    text = f"{emoji_map[version]} **{version.capitalize()} of:** `{query}`\n\n"
    for i, s in enumerate(unique[:6], 1):
        text += f"{i}. **{s['name']}** — {s['primaryArtists']}\n"
    text += "\n📥 `/download [song name]`"
    await msg.edit(text)

# B

@safe_handler
@app.on_message(filters.command("badges"))
async def badges(_, m: Message):
    user_id = m.from_user.id
    db.ensure_user(user_id, get_user_name(m))
    user = db.get_user(user_id) or {}
    downloads = user.get("downloads", 0)
    xp = user.get("xp", 0)
    level = user.get("level", 1)
    badge_list = get_badges(user_id)
    text = (f"🏅 **{get_user_name(m)}'s Badges:**\n\n")
    for b in badge_list:
        text += f"• {b}\n"
    text += (f"\n📥 Downloads: {downloads}\n"
             f"✨ XP: {xp} | {get_xp_bar(xp)}\n"
             f"🎖 Level: {level} — {get_level_title(level)}")
    await m.reply(text)

@safe_handler
@app.on_message(filters.command("batch"))
async def batch(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        await m.reply("📦 **Batch Download!**\n\nFormat:\n```\n/batch Tum Hi Ho\nKesariya\nBlinding Lights```\n\n⚠️ Max 5 songs!")
        return
    songs = [s.strip() for s in parts[1].strip().split("\n") if s.strip()][:5]
    if not songs:
        await m.reply("❌ Song names likho!")
        return
    await m.reply(f"📦 **Downloading {len(songs)} songs...**\n⚠️ Wait karo!")
    for i, song in enumerate(songs, 1):
        try:
            msg = await m.reply(f"⬇️ **{i}/{len(songs)}:** `{song}`...")
            await send_song(m, song, msg)
            await asyncio.sleep(2)
        except:
            await m.reply(f"❌ **{song}** failed!")


@safe_handler
@app.on_message(filters.command(["chat", "c"]))
async def chat_cmd(_, m: Message):
    if not GROQ_API_KEY:
        await m.reply("❌ Chat feature setup nahi hai!")
        return
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        await m.reply(
            "💬 **BeatNova AI Chat!**\n\n"
            "Mujhse kuch bhi pooch — music, songs, ya bas baat karo!\n\n"
            "Example:\n"
            "`/chat Arijit Singh ke best songs kaunse hain?`\n"
            "`/chat Mujhe sad songs suggest karo`\n"
            "`/chat Kya chal raha hai?`\n\n"
            "🗑 `/clearchat` — Chat history clear karo"
        )
        return
    user_id = m.from_user.id
    user_msg = parts[1].strip()
    # Daily limit check
    today = datetime.date.today()
    if user_id not in chat_daily_count or chat_daily_count[user_id]["date"] != today:
        chat_daily_count[user_id] = {"count": 0, "date": today}
    if chat_daily_count[user_id]["count"] >= CHAT_DAILY_LIMIT:
        await m.reply(f"⚠️ Daily chat limit reached ({CHAT_DAILY_LIMIT}/day)!\nCome back tomorrow 🕛")
        return
    chat_daily_count[user_id]["count"] += 1
    remaining = CHAT_DAILY_LIMIT - chat_daily_count[user_id]["count"]
    # Init history
    if user_id not in chat_histories:
        chat_histories[user_id] = []
    # Add user message
    chat_histories[user_id].append({"role": "user", "content": user_msg})
    # Keep last 20 messages only
    if len(chat_histories[user_id]) > 20:
        chat_histories[user_id] = chat_histories[user_id][-20:]
    msg = await m.reply("💬 **Thinking...**")
    try:
        messages = [{"role": "system", "content": BEATNOVA_SYSTEM_PROMPT}]
        messages += chat_histories[user_id]
        def call_groq():
            r = requests.post(
                GROQ_URL,
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": messages,
                    "max_tokens": 300,
                    "temperature": 0.8,
                },
                timeout=30
            )
            return r.json()
        data = await asyncio.to_thread(call_groq)
        if "choices" in data:
            reply_text = data["choices"][0]["message"]["content"].strip()
            chat_histories[user_id].append({"role": "assistant", "content": reply_text})
            await msg.edit(f"💬 {reply_text}\n\n`{remaining} chats left today`")
        else:
            # Log actual error
            err = data.get("error", {}).get("message", str(data))
            print(f"[GROQ ERROR] {err}")
            await msg.edit(f"❌ API Error: `{err[:80]}`")
    except Exception as e:
        print(f"[GROQ EXCEPTION] {e}")
        await msg.edit(f"❌ Error: `{str(e)[:80]}`\nDobara try karo!")

@safe_handler
@app.on_message(filters.command("clearchat"))
async def clearchat(_, m: Message):
    user_id = m.from_user.id
    chat_histories.pop(user_id, None)
    await m.reply("🗑 **Chat history cleared!**\nStart fresh with `/chat`!")


@safe_handler
@app.on_message(filters.command("challenge"))
async def challenge(_, m: Message):
    now = datetime.datetime.now()
    random.seed(now.day + now.month * 100 + now.year)
    results = search_jiosaavn_multiple("popular hindi songs", 20)
    if not results:
        await m.reply("❌ Could not fetch!")
        return
    song = random.choice(results)
    random.seed()
    title, artist = song["name"], song["primaryArtists"]
    lyrics_text, _ = get_lyrics(f"{title} - {artist}")
    if lyrics_text:
        lines = [l.strip() for l in lyrics_text.split("\n") if len(l.strip()) > 20]
        line = random.choice(lines[:10]) if lines else f"Hint: Artist is **{artist}**"
    else:
        line = f"Hint: Artist is **{artist}**"
    chat_id = m.chat.id
    active_quiz[chat_id] = {"answer": title.lower(), "title": title, "artist": artist, "type": "guess"}
    await m.reply(f"🎯 **Daily Challenge!**\n📅 {now.strftime('%d %b %Y')}\n\n"
                  f"🎵 **Guess this song:**\n_{line}_\n\n💭 Reply with song name!\n⏱ 30 seconds!")
    await asyncio.sleep(30)
    if chat_id in active_quiz and active_quiz[chat_id].get("type") == "guess":
        del active_quiz[chat_id]
        await m.reply(f"⏱ **Time's up!**\nAnswer: **{title}** by {artist}")

@safe_handler
@app.on_message(filters.command("compare"))
async def compare(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or "|" not in parts[1]:
        await m.reply("❌ Example: `/compare Tum Hi Ho | Kesariya`")
        return
    songs = parts[1].split("|")
    if len(songs) != 2:
        await m.reply("❌ Example: `/compare Tum Hi Ho | Kesariya`")
        return
    s1, s2 = songs[0].strip(), songs[1].strip()
    msg = await m.reply("⚖️ **Comparing...**")
    _, t1, d1, data1 = search_jiosaavn(s1)
    _, t2, d2, data2 = search_jiosaavn(s2)
    if not data1 or not data2:
        await msg.edit("❌ One or both songs not found!")
        return
    await msg.edit(
        f"⚖️ **Song Comparison:**\n\n"
        f"**1️⃣ {data1['name']}**\n👤 {data1['primaryArtists']}\n"
        f"💿 {data1.get('album',{}).get('name','Unknown')} | 📅 {data1.get('year','?')}\n"
        f"⏱ {d1//60}:{d1%60:02d}\n\n**VS**\n\n"
        f"**2️⃣ {data2['name']}**\n👤 {data2['primaryArtists']}\n"
        f"💿 {data2.get('album',{}).get('name','Unknown')} | 📅 {data2.get('year','?')}\n"
        f"⏱ {d2//60}:{d2%60:02d}\n\n"
        f"📥 `/download {data1['name']}` or `/download {data2['name']}`"
    )


@safe_handler
@app.on_message(filters.command("daily"))
async def daily(_, m: Message):
    now = datetime.datetime.now()
    keywords = ["hindi hits popular", "bollywood popular songs", "top songs india", "romantic hindi"]
    random.seed(now.day + now.month * 100)
    query = random.choice(keywords)
    random.seed()
    msg = await m.reply("📅 **Fetching today's song...**")
    results = search_jiosaavn_multiple(query, 20)
    if not results:
        await msg.edit("❌ No songs found!")
        return
    random.seed(now.day * now.month)
    song = random.choice(results)
    random.seed()
    await send_song(m, song["name"], msg)

@safe_handler
@app.on_message(filters.command("dailygroup"))
async def dailygroup(_, m: Message):
    if m.chat.type.name not in ("GROUP", "SUPERGROUP"):
        await m.reply("❌ Use this in a group!")
        return
    current = db.get_group_setting(m.chat.id, "daily_song")
    new_val = 0 if current else 1
    db.set_group_setting(m.chat.id, "daily_song", new_val)
    if new_val:
        await m.reply("🔔 **Daily Group Song: ON!**\nHar roz subah ek song aayega! 🎵")
    else:
        await m.reply("🔕 **Daily Group Song: OFF**")

@safe_handler
@app.on_message(filters.command("dailyreward"))
async def dailyreward(_, m: Message):
    user_id = m.from_user.id
    db.ensure_user(user_id, get_user_name(m))
    if not db.can_claim_reward(user_id):
        await m.reply("⏰ **Already claimed today!**\nAao kal phir! 🌅\n\nXP earn karne ke liye songs download karo!")
        return
    db.claim_reward(user_id)
    xp_earned = XP_REWARDS["daily_reward"]
    total_xp, level = db.add_xp(user_id, xp_earned)
    user = db.get_user(user_id)
    streak = user.get("streak", 0)
    await m.reply(
        f"🎁 **Daily Reward Claimed!**\n\n"
        f"✨ **+{xp_earned} XP** earned!\n"
        f"🔥 Streak: {streak} days\n"
        f"{get_xp_bar(total_xp)}\n"
        f"🎖 Level: {level} — {get_level_title(level)}\n\n"
        f"Kal phir aao double reward ke liye! 🌟"
    )

@safe_handler
@app.on_message(filters.command("discography"))
async def discography(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("❌ Example: `/discography Arijit Singh`")
        return
    query = parts[1].strip()
    msg = await m.reply(f"💿 **Fetching discography:** `{query}`...")
    results = []
    for q in [f"{query} songs", f"best of {query}", f"{query} hits"]:
        results += search_jiosaavn_multiple(q, 5)
    seen, unique = set(), []
    for s in results:
        if s["name"] not in seen:
            seen.add(s["name"])
            unique.append(s)
    if not unique:
        await msg.edit("❌ No songs found!")
        return
    text = f"💿 **{query}'s Discography ({len(unique)} songs):**\n\n"
    for i, s in enumerate(unique[:15], 1):
        d = int(s["duration"])
        text += f"{i}. **{s['name']}** | ⏱ {d//60}:{d%60:02d}\n"
    text += "\n📥 `/download [song name]`"
    await msg.edit(text)

# Words that mean a song is a cover/remake — filter from download results
_COVER_SIGNALS_NAME = [
    "cover", "tribute", "recreat", "remake", "karaoke",
    "instrumental", "sing along", "sing-along", "lofi", "lo-fi",
    "slowed", "reverb", "unplugged version", "backing track",
    # Mashup/crossover patterns
    " x ", " X ", " vs ", " vs. ",
    "mashup", "medley", "jukebox",
    # Version tags
    "trending version", "viral version", "reels version",
    "short version", "clip version", "promo",
]

_COVER_SIGNALS_ARTIST = [
    "afusic", "anukriti", "karaoke", "sing along",
    "recreated", "tribute", "cover", "unplugged",
]

def _is_valid_result(song):
    """Filter: remove short clips, unknown artists, covers, mashups, wrong versions"""
    import re as _re
    name = song.get("name", "").lower()
    artist = song.get("primaryArtists", song.get("artist", "")).lower().strip()
    duration = int(song.get("duration", 0))

    # duration=0 means YouTube source (duration not yet known) — always allow
    # Only reject if we KNOW it's a short clip (1-55s)
    if 0 < duration < 55:
        return False

    # Remove unknown/blank artist
    if not artist or artist in ("unknown", "various artists", ""):
        return False

    # Remove version number songs like "Song 2.0", "Song 3.0" — almost always unwanted
    if _re.search(r'\b\d+\.\d+\b', name):
        return False

    # Remove if name has cover/mashup/version signals
    for sig in _COVER_SIGNALS_NAME:
        if sig in name:
            return False

    # Remove known cover/tribute artists
    for sig in _COVER_SIGNALS_ARTIST:
        if sig in artist:
            return False

    # Remove remix patterns: "Song Name - Remix", "Song (DJ Mix)"
    if _re.search(r'[\(\[](remix|mix|edit|dj|remaster|remastered|version|ver\.)[\)\]]', name):
        return False

    return True

def _dedup_by_artist(results):
    """One result per artist — if same artist appears multiple times keep longest"""
    artist_best = {}
    for s in results:
        artist = s.get("primaryArtists", s.get("artist", "")).split(",")[0].strip().lower()
        duration = int(s.get("duration", 0))
        if artist not in artist_best or duration > int(artist_best[artist].get("duration", 0)):
            artist_best[artist] = s
    return list(artist_best.values())

@app.on_message(filters.command("download"))
async def download(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("❌ Example: `/download Tum Hi Ho`")
        return
    query = parts[1].strip()
    is_group = m.chat.type.name in ("GROUP", "SUPERGROUP")
    first_name = get_user_name(m) if m.from_user else "User"
    user_id = m.from_user.id if m.from_user else None

    # In group with slowmode: skip the "Searching..." reply in group, go straight to DM
    try:
        msg = await m.reply(f"🔍 **Searching:** `{query}`...")
    except Exception as e:
        if "SLOWMODE_WAIT" in str(e) and is_group and user_id:
            # Can't reply in group due to slowmode — send directly to DM
            try:
                dm_msg = await app.send_message(user_id, f"🔍 **Searching:** `{query}`...")
                await send_song(dm_msg, query, dm_msg, _user_id=user_id, _first_name=first_name)
            except Exception as dm_e:
                if "USER_PRIVACY_RESTRICTED" in str(dm_e):
                    pass  # Can't DM either, silently fail
            return
        else:
            return  # Unknown error, give up

    # Fetch multiple results
    raw_results = await asyncio.to_thread(search_jiosaavn_multiple, query, 15)

    # Filter: remove short clips, unknown artists, obvious covers
    filtered = [s for s in raw_results if _is_valid_result(s)]

    # Dedup by artist
    filtered = _dedup_by_artist(filtered)

    # Max 6
    filtered = filtered[:6]

    if not filtered:
        # Fallback — use original single result method
        if is_group:
            try:
                dm_msg = await app.send_message(user_id, f"🔍 **Searching:** `{query}`...")
                GROUP_ACK = [
                    f"📩 Sending to your DMs, {first_name}! 🎧",
                    f"🚀 Check your DMs, {first_name}!",
                    f"💌 On its way to your inbox, {first_name}!",
                ]
                try:
                    await msg.edit(random.choice(GROUP_ACK))
                except: pass
                await send_song(dm_msg, query, dm_msg, _user_id=user_id, _first_name=first_name)
            except Exception as e:
                if "USER_PRIVACY_RESTRICTED" in str(e):
                    try:
                        await msg.edit(f"📩 **Can't DM you!**\n\nStart a chat first: {BOT_USERNAME}\nThen try again!")
                    except: pass
                elif "SLOWMODE_WAIT" in str(e):
                    # Group has slowmode on DM send too? Very rare but handle
                    try:
                        await send_song(dm_msg, query, dm_msg, _user_id=user_id, _first_name=first_name)
                    except: pass
                else:
                    await send_song(m, query, msg, _user_id=user_id, _first_name=first_name)
        else:
            await send_song(m, query, msg)
        return

    # If only 1 clean result — download directly
    if len(filtered) == 1:
        song = filtered[0]
        song_query = f"{song['name']} {song['primaryArtists'].split(',')[0].strip()}"
        if is_group:
            try:
                dm_msg = await app.send_message(user_id, f"🔍 **Searching:** `{song_query}`...")
                GROUP_ACK = [
                    f"📩 Sending to your DMs, {first_name}! 🎧",
                    f"🚀 Check your DMs, {first_name}!",
                ]
                try:
                    await msg.edit(random.choice(GROUP_ACK))
                except: pass
                await send_song(dm_msg, song_query, dm_msg, _user_id=user_id, _first_name=first_name)
            except Exception as e:
                if "USER_PRIVACY_RESTRICTED" in str(e):
                    try:
                        await msg.edit(f"📩 **Can't DM you!**\n\nStart a chat first: {BOT_USERNAME}")
                    except: pass
                elif "SLOWMODE_WAIT" in str(e):
                    pass  # Group slowmode — can't send DM init msg, ignore
                else:
                    await send_song(m, song_query, msg, _user_id=user_id, _first_name=first_name)
        else:
            await send_song(m, song_query, msg)
        return

    # Multiple results — show choice buttons
    num_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"]
    text = f"🎵 **Multiple matches for:** `{query}`\n\n"
    btn_rows = []
    row = []
    for i, song in enumerate(filtered):
        d = int(song.get("duration", 0))
        dur = f"{d//60}:{d%60:02d}" if d else "?"
        artist = song.get("primaryArtists", song.get("artist", "Unknown")).split(",")[0].strip()
        text += f"{num_emojis[i]} **{song['name']}** — {artist} | ⏱ {dur}\n"
        # callback: pick_{i}_{query encoded}
        cb_data = f"pick_{i}_{query[:25]}"
        row.append(InlineKeyboardButton(num_emojis[i], callback_data=cb_data))
        if len(row) == 3:
            btn_rows.append(row)
            row = []
    if row:
        btn_rows.append(row)
    text += "\n👇 Tap to download:"

    # Store results temporarily in memory keyed by user+query
    _pending_downloads[f"{user_id}:{query[:25]}"] = {
        "results": filtered,
        "is_group": is_group,
        "user_id": user_id,
        "first_name": first_name,
    }

    try:
        await msg.edit(text, reply_markup=InlineKeyboardMarkup(btn_rows))
    except Exception as e:
        if "SLOWMODE_WAIT" in str(e):
            # Group slowmode hit on edit — try sending fresh message to DM
            try:
                dm_msg = await app.send_message(user_id, text, reply_markup=InlineKeyboardMarkup(btn_rows))
            except: pass

@app.on_callback_query(filters.regex(r"^pick_\d+_"))
async def pick_callback(_, cb):
    parts = cb.data.split("_", 2)
    idx = int(parts[1])
    query_key = parts[2] if len(parts) > 2 else ""
    user_id = cb.from_user.id
    first_name = cb.from_user.first_name
    key = f"{user_id}:{query_key}"

    pending = _pending_downloads.get(key)
    if not pending:
        await cb.answer("Session expired! Try /download again.", show_alert=True)
        return

    songs = pending["results"]
    is_group = pending["is_group"]
    if idx >= len(songs):
        await cb.answer("Invalid choice!", show_alert=True)
        return

    song = songs[idx]
    artist = song.get("primaryArtists", song.get("artist", "")).split(",")[0].strip()
    song_query = f"{song['name']} {artist}"

    await cb.answer(f"Downloading: {song['name']}", show_alert=False)

    # Remove from pending
    _pending_downloads.pop(key, None)

    if is_group:
        try:
            dm_msg = await app.send_message(user_id, f"🔍 **Searching:** `{song_query}`...")
            GROUP_ACK = [
                f"📩 Sending to your DMs, {first_name}! 🎧",
                f"🚀 Check your DMs, {first_name}!",
                f"💌 On its way to your inbox, {first_name}!",
            ]
            try:
                await cb.message.edit(random.choice(GROUP_ACK))
            except: pass
            await send_song(dm_msg, song_query, dm_msg, _user_id=user_id, _first_name=first_name)
        except Exception as e:
            err_str = str(e)
            if "USER_PRIVACY_RESTRICTED" in err_str:
                try:
                    await cb.message.reply(f"📩 **Can't DM you!**\n\nStart a chat with me first: {BOT_USERNAME}")
                except: pass
            elif "SLOWMODE_WAIT" in err_str:
                # Group has slowmode — just notify user
                try:
                    await app.send_message(user_id, f"⚠️ Group slowmode active. Use `/download {song_query}` here in DM!")
                except: pass
            else:
                # Fallback: try replying in group
                try:
                    msg2 = await cb.message.reply(f"🔍 **Searching:** `{song_query}`...")
                    await send_song(cb.message, song_query, msg2, _user_id=user_id, _first_name=first_name)
                except Exception as e2:
                    if "SLOWMODE_WAIT" not in str(e2):
                        print(f"[pick_callback fallback] {e2}")
    else:
        try:
            msg2 = await cb.message.reply(f"🔍 **Searching:** `{song_query}`...")
            await send_song(cb.message, song_query, msg2, _user_id=user_id, _first_name=first_name)
        except Exception as e:
            print(f"[pick_callback private] {e}")

@safe_handler
@app.on_message(filters.command("duet"))
async def duet(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        await m.reply("❌ Example: `/duet Arijit Shreya`")
        return
    query = parts[1].strip()
    msg = await m.reply(f"🎶 **Fetching duets:** `{query}`...")
    results = search_jiosaavn_multiple(f"{query} duet collab", 8)
    if not results:
        await msg.edit("❌ No results!")
        return
    text = f"🎶 **Duets/Collabs: {query}**\n\n"
    for i, s in enumerate(results, 1):
        text += f"{i}. **{s['name']}** - {s['primaryArtists']}\n"
    text += "\n📥 `/download [song name]`"
    await msg.edit(text)

# E

@safe_handler
@app.on_message(filters.command("easteregg"))
async def easteregg(_, m: Message):
    await m.reply(random.choice(EASTER_EGGS))


@safe_handler
@app.on_message(filters.command("favorites"))
async def show_favorites(_, m: Message):
    user_id = m.from_user.id
    favs = db.get_favorites(user_id)
    if not favs:
        await m.reply("💾 **No favorites yet!**\n\n💡 **Try:**\n⭐ Save songs while downloading — tap the Save button\n🎵 Download first: `/download Tum Hi Ho`")
        return
    text = "⭐ **Your Favorites:**\n\n"
    for i, s in enumerate(favs, 1):
        text += f"{i}. {s}\n"
    text += "\n📥 `/download [song name]`"
    await m.reply(text)


@safe_handler
@app.on_message(filters.command("findlyrics"))
async def findlyrics(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("❌ Example: `/findlyrics tere bin nahi lagda`")
        return
    query = parts[1].strip()
    msg = await m.reply(f"🔤 **Searching by lyrics:** `{query}`...")
    try:
        r = requests.get(f"https://lrclib.net/api/search?q={query}", headers={"User-Agent": "MusicBot/1.0"}, timeout=15)
        data = r.json()
        if data:
            text = f"🔤 **Songs matching:** `{query}`\n\n"
            for i, item in enumerate(data[:5], 1):
                text += f"{i}. **{item.get('trackName','Unknown')}** - {item.get('artistName','Unknown')}\n"
            text += "\n📥 `/download [song name]`"
            await msg.edit(text)
        else:
            results = search_jiosaavn_multiple(query, 5)
            text = f"🔤 **Possible songs:**\n\n"
            for i, s in enumerate(results, 1):
                text += f"{i}. **{s['name']}** - {s['primaryArtists']}\n"
            await msg.edit(text)
    except Exception as e:
        await msg.edit(f"❌ Error: `{str(e)}`")

# G

@safe_handler
@app.on_message(filters.command("genre"))
async def genre(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("🎸 **Choose:**\n`/genre rock` `/genre pop` `/genre jazz`\n`/genre classical` `/genre rap` `/genre indie`\n`/genre sufi` `/genre folk`")
        return
    g = parts[1].strip().lower()
    queries = {"rock": "rock songs", "pop": "pop hits", "jazz": "jazz music", "classical": "classical instrumental", "rap": "rap hip hop", "indie": "indie hindi", "sufi": "sufi songs", "folk": "folk india"}
    emojis = {"rock": "🎸", "pop": "🎵", "jazz": "🎷", "classical": "🎻", "rap": "🎤", "indie": "🌿", "sufi": "🌙", "folk": "🪘"}
    if g not in queries:
        await m.reply("❌ Available: `rock` `pop` `jazz` `classical` `rap` `indie` `sufi` `folk`")
        return
    msg = await m.reply(f"🔍 **Fetching {g} songs...**")
    results = apis.search_genre(g, 10)
    if not results:
        await msg.edit("❌ No songs found!")
        return
    text = f"{emojis[g]} **{g.capitalize()} Songs:**\n\n"
    for i, s in enumerate(results[:10], 1):
        artist = s.get("artist", s.get("primaryArtists","Unknown"))
        text += f"{i}. **{s['name']}** - {artist}\n"
    text += "\n📥 `/download [song name]`"
    await msg.edit(text)

@safe_handler
@app.on_message(filters.command("genrestats"))
async def genrestats(_, m: Message):
    user_id = m.from_user.id
    songs = db.get_history(user_id, 50)
    if not songs:
        await m.reply("📊 **No listening history yet!**\n\n💡 **Start downloading to see your genre stats:**\n📥 `/download Tum Hi Ho`\n📥 `/download Shape of You`")
        return
    total = len(songs)
    hindi = sum(1 for s in songs if any(w in s.lower() for w in ["hindi","tum","dil","pyar","ishq","tera","mera"]))
    english = sum(1 for s in songs if any(w in s.lower() for w in ["love","baby","night","light","heart"]))
    punjabi = sum(1 for s in songs if any(w in s.lower() for w in ["punjabi","jatt","kudi","yaar"]))
    other = max(0, total - hindi - english - punjabi)
    def pct(n): return f"{(n/total*100):.0f}%" if total > 0 else "0%"
    await m.reply(f"📊 **{get_user_name(m)}'s Genre Breakdown:**\n\n"
                  f"🇮🇳 Hindi: {hindi} ({pct(hindi)})\n🌍 English: {english} ({pct(english)})\n"
                  f"🎵 Punjabi: {punjabi} ({pct(punjabi)})\n🎶 Other: {other} ({pct(other)})\n\n"
                  f"📥 Total: {total}")

@safe_handler
@app.on_message(filters.command("gleaderboard"))
async def gleaderboard(_, m: Message):
    if m.chat.type.name not in ("GROUP", "SUPERGROUP"):
        await m.reply("❌ Use this in a group!")
        return
    rows = db.get_group_leaderboard(m.chat.id)
    if not rows:
        await m.reply("❌ No downloads in this group yet!")
        return
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    text = f"🏆 **{m.chat.title} Leaderboard:**\n\n"
    for i, row in enumerate(rows, 0):
        text += f"{medals[i]} **{row['user_name']}** — {row['downloads']} downloads\n"
    text += "\n🎵 Download songs to climb up!"
    await m.reply(text)

@safe_handler
@app.on_message(filters.command("groupmood"))
async def groupmood(_, m: Message):
    if m.chat.type.name not in ("GROUP", "SUPERGROUP"):
        await m.reply("❌ Use this in a group!")
        return
    moods = ["happy 😊", "sad 😢", "party 🎉", "romantic 💕", "chill 😌"]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("😊 Happy", callback_data="none"),
         InlineKeyboardButton("😢 Sad", callback_data="none")],
        [InlineKeyboardButton("🎉 Party", callback_data="none"),
         InlineKeyboardButton("💕 Romantic", callback_data="none")],
        [InlineKeyboardButton("😌 Chill", callback_data="none")]
    ])
    await m.reply(f"🎭 **Group Mood Poll!**\n\nSabka mood kya hai?\nBot best playlist suggest karega!\n\n"
                  f"Vote karo neeche 👇", reply_markup=keyboard)

@safe_handler
@app.on_message(filters.command("groupquiz"))
async def groupquiz(_, m: Message):
    if m.chat.type.name not in ("GROUP", "SUPERGROUP"):
        await m.reply("❌ Use this in a group!")
        return
    msg = await m.reply("🎮 **Group Quiz shuru ho raha hai...**")
    chat_id = m.chat.id
    # Rotate quiz types for group
    group_quiz_type = random.choice(["lyrics_guess", "musicquiz_group", "lyrics_guess"])
    query = random.choice(QUIZ_QUERIES)
    results = search_jiosaavn_multiple(query, 20)
    results += search_jiosaavn_multiple(random.choice(QUIZ_QUERIES), 10)
    seen, unique = set(), []
    for s in results:
        if s["name"] not in seen:
            seen.add(s["name"])
            unique.append(s)
    if not unique:
        await msg.edit("❌ Could not fetch! Try again.")
        return
    if group_quiz_type == "musicquiz_group":
        # MCQ style for group
        correct = random.choice(unique)
        title = correct["name"]
        artist = correct.get("primaryArtists", correct.get("artist","Unknown")).split(",")[0].strip()
        wrong_pool = [s for s in unique if s["name"] != title]
        wrong_options = random.sample(wrong_pool, min(3, len(wrong_pool)))
        options = [title] + [s["name"] for s in wrong_options]
        while len(options) < 4:
            options.append("Unknown Song")
        options = options[:4]
        random.shuffle(options)
        correct_idx = options.index(title)
        labels = ["A", "B", "C", "D"]
        active_quiz[chat_id] = {
            "answer": title.lower(), "title": title,
            "artist": artist, "type": "quiz", "options": options
        }
        text = f"🎮 **Group Quiz!** 👥\n\n👤 **Artist:** {artist}\n\n❓ **Kaunsa song hai is artist ka?**\n\n"
        for i, opt in enumerate(options):
            text += f"**{labels[i]}.** {opt}\n"
        text += "\n💭 Sabse pehle A/B/C/D reply karo!\n⏱ 30 seconds! 🏆"
        await msg.edit(text)
        await asyncio.sleep(30)
        if chat_id in active_quiz and active_quiz[chat_id].get("type") == "quiz":
            del active_quiz[chat_id]
            await m.reply(f"⏱ **Time's up!**\nAnswer: **{labels[correct_idx]}. {title}** by {artist}")
    else:
        # Lyrics guess - find song with good lyrics
        found = False
        for attempt in range(8):
            correct = unique[attempt % len(unique)]
            title = correct["name"]
            artist = correct.get("primaryArtists", correct.get("artist","Unknown"))
            lyrics_text, _ = get_lyrics(f"{title} - {artist}")
            if lyrics_text:
                lines = [l.strip() for l in lyrics_text.split("\n")
                        if len(l.strip()) > 25 and not l.strip().startswith("[")
                        and is_valid_quiz_line(l)]
                if len(lines) >= 2:
                    found = True
                    break
        if not found:
            await msg.edit("❌ Lyrics nahi mile! `/musicquiz` try karo.")
            return
        line = random.choice(lines[:15])
        active_quiz[chat_id] = {"answer": title.lower(), "title": title, "artist": artist, "type": "guess"}
        await msg.edit(
            f"🎮 **Group Guess The Song!** 👥\n\n"
            f"🎵 **In lyrics ka song guess karo:**\n\n"
            f"_{line}_\n\n"
            f"💭 **Sabse pehle sahi answer karega wo jitega!** 🏆\n"
            f"⏱ 30 seconds!"
        )
        await asyncio.sleep(15)
        if chat_id in active_quiz and active_quiz[chat_id].get("type") == "guess":
            # Hint after 15 sec
            try:
                other_line = random.choice([l for l in lines if l != line][:10]) if len(lines) > 1 else line
                await m.reply(f"💡 **Hint:** _{other_line}_")
            except: pass
        await asyncio.sleep(15)
        if chat_id in active_quiz and active_quiz[chat_id].get("type") == "guess":
            del active_quiz[chat_id]
            await m.reply(f"⏱ **Time's up! Kisi ne sahi jawab nahi diya!**\n🎵 Answer: **{title}**\n👤 {artist}")

@safe_handler
@app.on_message(filters.command("groupstats"))
async def groupstats(_, m: Message):
    if m.chat.type.name not in ("GROUP", "SUPERGROUP"):
        await m.reply("❌ Use this in a group!")
        return
    group_id = m.chat.id
    total = db.get_group_total_downloads(group_id)
    members = db.get_group_members_count(group_id)
    top = db.get_group_leaderboard(group_id, 1)
    top_name = top[0]["user_name"] if top else "None"
    await m.reply(f"📊 **{m.chat.title} Stats:**\n\n"
                  f"👥 Active Members: {members}\n"
                  f"📥 Total Downloads: {total}\n"
                  f"🥇 Top User: {top_name}\n\n"
                  f"🏆 `/gleaderboard` — See full ranking")

@safe_handler
@app.on_message(filters.command("guesssong"))
async def guesssong(_, m: Message):
    msg = await m.reply("🎯 **Fetching quiz song...**")
    chat_id = m.chat.id
    # Use Hindi queries for lyrics (avoid Punjabi/regional scripts)
    query = random.choice(HINDI_QUIZ_QUERIES)
    results = search_jiosaavn_multiple(query, 20)
    if not results:
        await msg.edit("❌ Could not fetch! Try again.")
        return
    # Try multiple songs to find one with good lyrics
    random.shuffle(results)
    found = False
    for attempt in range(5):
        song = results[attempt % len(results)]
        title, artist = song["name"], song.get("primaryArtists", song.get("artist", "Unknown"))
        lyrics_text, _ = get_lyrics(f"{title} - {artist}")
        if lyrics_text:
            lines = [l.strip() for l in lyrics_text.split("\n")
                    if len(l.strip()) > 25 and not l.strip().startswith("[")
                    and is_valid_quiz_line(l)]
            if len(lines) >= 3:
                found = True
                break
    if not found:
        await msg.edit("❌ Could not get good lyrics! Try again.")
        return
    # Pick a random lyric line as hint
    line = random.choice(lines[:20])
    # Scramble artist name slightly as extra hint
    active_quiz[chat_id] = {
        "answer": title.lower(), "title": title,
        "artist": artist, "type": "guess",
        "hint_used": False
    }
    await msg.edit(
        f"🎯 **Guess The Song!**\n\n"
        f"🎵 **Fill in the lyrics:**\n\n"
        f"_{line}_\n\n"
        f"💭 Song ka naam reply karo!\n"
        f"⏱ 30 seconds! | `/skip` to skip"
    )
    await asyncio.sleep(15)
    # Give hint after 15 sec
    if chat_id in active_quiz and active_quiz[chat_id].get("type") == "guess":
        first_letter = title[0].upper()
        hint_line = random.choice([l for l in lines if l != line][:10]) if len(lines) > 1 else line
        try:
            await m.reply(
                f"💡 **Hint:** Song ka pehla letter **'{first_letter}'** hai!\n"
                f"🎵 Another line: _{hint_line}_"
            )
        except: pass
    await asyncio.sleep(15)
    if chat_id in active_quiz and active_quiz[chat_id].get("type") == "guess":
        del active_quiz[chat_id]
        await m.reply(f"⏱ **Time's up!**\n🎵 Answer: **{title}**\n👤 Artist: {artist}")

# H

@safe_handler
@app.on_message(filters.command("help"))
async def help_cmd(_, m: Message):
    _bot_raw = BOT_USERNAME.replace("@", "")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 Music", callback_data="menu_music_1"),
         InlineKeyboardButton("🌍 Discover", callback_data="menu_discover_1")],
        [InlineKeyboardButton("🎮 Games", callback_data="menu_games_1"),
         InlineKeyboardButton("🕹 Fun Games", callback_data="menu_fun_1")],
        [InlineKeyboardButton("💬 Chat & More", callback_data="menu_chat_1"),
         InlineKeyboardButton("👤 Profile", callback_data="menu_profile_1")],
        [InlineKeyboardButton("📊 Stats", callback_data="menu_stats_1")],
        [InlineKeyboardButton("➕ Add BeatNova to Your Group", url=f"https://t.me/{_bot_raw}?startgroup=true")],
    ])
    await m.reply(
        "🎧 **BeatNova Help Menu**\n\n"
        "👇 Choose a category:\n"
        "━━━━━━━━━━━━━━━\n"
        "🎧 Powered by BeatNova",
        reply_markup=keyboard
    )


@safe_handler
@app.on_message(filters.command("history"))
async def show_history(_, m: Message):
    user_id = m.from_user.id
    songs = db.get_history(user_id)
    if not songs:
        await m.reply("📜 **No history yet!**\n\n💡 **Get started:**\n📥 `/download Tum Hi Ho`\n🔍 `/search Arijit Singh`")
        return
    text = "📜 **Recent Songs:**\n\n"
    for i, s in enumerate(songs, 1):
        text += f"{i}. {s}\n"
    await m.reply(text)

# I

@safe_handler
@app.on_message(filters.command("info"))
async def song_info(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("❌ Example: `/info Tum Hi Ho`")
        return
    query = parts[1].strip()
    msg = await m.reply(f"🔍 **Getting info:** `{query}`...")
    dl_url, title, duration, song_data = search_jiosaavn(query)
    if not song_data:
        await msg.edit("❌ Song not found!")
        return
    mins, secs = duration // 60, duration % 60
    g_stats = db.get_song_global_stats(song_data['name'])
    avg_rating, vote_count = db.get_avg_rating(song_data['name'][:25])
    album_raw = song_data.get("album", "Unknown")
    album_name = album_raw.get("name", "Unknown") if isinstance(album_raw, dict) else (album_raw or "Unknown")
    await msg.edit(f"ℹ️ **Song Info:**\n\n🎵 **Title:** {song_data['name']}\n"
                   f"👤 **Artist:** {song_data.get('artist', song_data.get('primaryArtists','Unknown'))}\n"
                   f"💿 **Album:** {album_name}\n"
                   f"📅 **Year:** {song_data.get('year', 'Unknown')}\n"
                   f"🌐 **Language:** {song_data.get('language', 'Unknown').capitalize()}\n"
                   f"⏱ **Duration:** {mins}:{secs:02d}\n"
                   f"📥 **Bot Downloads:** {g_stats.get('downloads', 0)}\n"
                   f"⭐ **Rating:** {avg_rating:.1f}/5 ({vote_count} votes)\n\n"
                   f"📥 `/download {song_data['name']}`")

@safe_handler
@app.on_message(filters.command("invite"))
async def invite(_, m: Message):
    user_id = m.from_user.id
    db.ensure_user(user_id, get_user_name(m))
    await m.reply(f"🤝 **Invite Friends to {BOT_NAME}!**\n\n"
                  f"Share this bot:\n👉 {BOT_USERNAME}\n\n"
                  f"_Share the music, spread the love!_ 🎵")

# K


@safe_handler
@app.on_message(filters.command("lastdownload"))
async def lastdownload(_, m: Message):
    s = db.get_last_downloaded(m.from_user.id)
    if not s:
        await m.reply("🎵 No song downloaded yet!")
        return
    await m.reply(f"🎵 **Last Downloaded:**\n\n🎶 **{s['title']}**\n⏱ {s['duration']} | 👤 {s['by_name']}\n\n📥 `/download {s['title']}`")

@safe_handler
@app.on_message(filters.command("leaderboard"))
async def leaderboard(_, m: Message):
    users = db.get_all_users()
    if not users:
        await m.reply("❌ No data yet!")
        return
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    text = "🏆 **Top Music Lovers:**\n\n"
    for i, u in enumerate(users[:10], 0):
        streak_text = f" 🔥{u['streak']}" if u.get("streak", 0) >= 3 else ""
        xp_text = f" ✨{u.get('xp',0)}xp"
        text += f"{medals[i]} **{u['name']}** — {u['downloads']} downloads{streak_text}{xp_text}\n"
    text += "\n📥 Download more to climb up! 🚀"
    await m.reply(text)



@safe_handler
@app.on_message(filters.command("lyrics"))
async def lyrics(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("❌ Format: `/lyrics Song - Artist`")
        return
    query = parts[1].strip()
    msg = await m.reply(f"🔍 **Searching lyrics:** `{query}`...")
    lyrics_text, title = get_lyrics(query)
    if not lyrics_text:
        await msg.edit("❌ Lyrics not found!")
        return
    header = f"📝 **Lyrics: {title}**\n\n"
    full = header + lyrics_text
    if len(full) <= 4096:
        await msg.edit(full)
    else:
        await msg.edit(header + lyrics_text[:4000])
        remaining = lyrics_text[4000:]
        while remaining:
            await m.reply(remaining[:4096])
            remaining = remaining[4096:]

# M

@safe_handler
@app.on_message(filters.command("mood"))
async def mood(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply(
            "🎭 **Mood Music:**\n\n"
            "`/mood happy` 😊 `/mood sad` 😢\n"
            "`/mood party` 🎉 `/mood romantic` 💕\n"
            "`/mood workout` 💪 `/mood chill` 😌\n"
            "`/mood angry` 😤 `/mood lonely` 🌧"
        )
        return
    mood_type = parts[1].strip().lower()
    mood_data = {
        "happy":    (["happy songs hindi", "khushi ke gaane", "upbeat bollywood hits", "feel good hindi"], "😊"),
        "sad":      (["sad hindi songs hits", "dard bhare gaane", "emotional bollywood", "breakup songs hindi", "bekhayali arijit"], "😢"),
        "party":    (["party songs hindi 2024", "dance hits bollywood", "dj songs hindi", "party anthem"], "🎉"),
        "romantic": (["romantic hindi songs", "love songs bollywood", "pyaar ke gaane", "ishq songs hits"], "💕"),
        "workout":  (["workout motivation songs", "gym music hindi", "power songs energy", "pump up songs"], "💪"),
        "chill":    (["chill hindi songs", "lo-fi bollywood", "relaxing hindi music", "calm songs india"], "😌"),
        "angry":    (["angry songs hindi", "attitude songs", "rap hindi aggressive", "power angry music"], "😤"),
        "lonely":   (["lonely songs hindi", "alone sad songs", "tanha songs bollywood", "missing someone songs"], "🌧"),
    }
    if mood_type not in mood_data:
        await m.reply("❌ Available: `happy` `sad` `party` `romantic` `workout` `chill` `angry` `lonely`")
        return
    queries, emoji = mood_data[mood_type]
    msg = await m.reply(f"🎭 **Fetching {mood_type} songs...**")
    try:
        results = []
        for q in queries[:2]:
            results += search_jiosaavn_multiple(q, 6)
        seen, unique = set(), []
        for s in results:
            if s["name"] not in seen:
                seen.add(s["name"])
                unique.append(s)
        if not unique:
            await msg.edit(f"❌ No {mood_type} songs found! Try `/download {mood_type} songs`")
            return
        text = f"🎭 **{mood_type.capitalize()} Songs** {emoji}\n\n"
        for i, s in enumerate(unique[:8], 1):
            artist = s.get("primaryArtists", s.get("artist", "Unknown"))
            text += f"{i}. **{s['name']}** — {artist}\n"
        text += "\n👇 Tap to download:"
        num_emojis = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣"]
        btn_rows2 = []
        row2 = []
        result_list = similar_tracks[:6] if similar_tracks and len(similar_tracks) >= 3 else unique[:6]
        for i, t in enumerate(result_list):
            name = t.get("name", "") if isinstance(t, dict) else t
            row2.append(InlineKeyboardButton(num_emojis[i], callback_data=f"dl_{name[:30]}"))
            if len(row2) == 3:
                btn_rows2.append(row2)
                row2 = []
        if row2:
            btn_rows2.append(row2)
        await msg.edit(text, reply_markup=InlineKeyboardMarkup(btn_rows2) if btn_rows2 else None)
    except Exception as e:
        await msg.edit("❌ Could not fetch! Try again.")
        print(f"[mood] {e}")

@safe_handler
@app.on_message(filters.command("musicfact"))
async def musicfact(_, m: Message):
    await m.reply(f"🎵 **Music Fact:**\n\n{random.choice(MUSIC_FACTS)}")

@safe_handler
@app.on_message(filters.command("musicmatch"))
async def musicmatch(_, m: Message):
    if m.chat.type.name not in ("GROUP", "SUPERGROUP"):
        await m.reply("❌ Group mein use karo!\nExample: `/musicmatch @user1 @user2`")
        return
    await m.reply("🎵 **Music Match!**\n\nDono users ke downloads compare ho rahe hain...\n\n"
                  "_(Feature coming soon — abhi apni history `/history` mein dekho!)_ 🎵")

@safe_handler
@app.on_message(filters.command("musicquiz"))
async def musicquiz(_, m: Message):
    msg = await m.reply("🎮 **Preparing Music Quiz...**")
    chat_id = m.chat.id

    # Fetch from 2 different random queries for variety
    q1 = random.choice(QUIZ_QUERIES)
    q2 = random.choice([q for q in QUIZ_QUERIES if q != q1])
    results = search_jiosaavn_multiple(q1, 15)
    results += search_jiosaavn_multiple(q2, 10)
    
    # Deduplicate
    seen, unique = set(), []
    for s in results:
        if s["name"] not in seen:
            seen.add(s["name"])
            unique.append(s)
    
    if len(unique) < 4:
        await msg.edit("❌ Could not fetch enough songs! Try again.")
        return

    # Quiz type rotation: song→artist, artist→song, year→song
    quiz_types = ["which_song", "which_artist", "which_year"]
    quiz_type = random.choice(quiz_types)
    
    correct = random.choice(unique)
    correct_title = correct["name"]
    correct_artist = correct.get("primaryArtists", correct.get("artist", "Unknown"))
    correct_year = str(correct.get("year", "Unknown"))
    
    wrong_pool = [s for s in unique if s["name"] != correct_title]
    
    if quiz_type == "which_song" or len(wrong_pool) < 3:
        # Q: Artist diya, song guess karo
        wrong_options = random.sample(wrong_pool, min(3, len(wrong_pool)))
        options = [correct_title] + [s["name"] for s in wrong_options]
        random.shuffle(options)
        correct_idx = options.index(correct_title)
        labels = ["A", "B", "C", "D"]
        question = f"👤 **Artist:** {correct_artist.split(',')[0].strip()}\n\n❓ **Kaunsa song hai is artist ka?**"
        answer = correct_title.lower()
        answer_display = f"{labels[correct_idx]}. {correct_title}"

    elif quiz_type == "which_artist":
        # Q: Song diya, artist guess karo
        wrong_artists = list(set([
            s.get("primaryArtists", s.get("artist","")).split(",")[0].strip()
            for s in wrong_pool
            if s.get("primaryArtists", s.get("artist","")).split(",")[0].strip() != correct_artist.split(",")[0].strip()
        ]))
        if len(wrong_artists) < 3:
            wrong_artists = wrong_artists + ["Arijit Singh", "Neha Kakkar", "Jubin Nautiyal"]
            wrong_artists = [a for a in wrong_artists if a != correct_artist.split(",")[0].strip()]
        wrong_artists = random.sample(wrong_artists, min(3, len(wrong_artists)))
        correct_a = correct_artist.split(",")[0].strip()
        options = [correct_a] + wrong_artists[:3]
        random.shuffle(options)
        correct_idx = options.index(correct_a)
        labels = ["A", "B", "C", "D"]
        question = f"🎵 **Song:** {correct_title}\n\n❓ **Kaunse artist ne ye gaaya?**"
        answer = correct_a.lower()
        answer_display = f"{labels[correct_idx]}. {correct_a}"
    
    else:
        # Q: Song diya, year guess karo
        if correct_year == "Unknown":
            quiz_type = "which_song"
            wrong_options = random.sample(wrong_pool, min(3, len(wrong_pool)))
            options = [correct_title] + [s["name"] for s in wrong_options]
            random.shuffle(options)
            correct_idx = options.index(correct_title)
            labels = ["A", "B", "C", "D"]
            question = f"👤 **Artist:** {correct_artist.split(',')[0].strip()}\n\n❓ **Kaunsa song hai is artist ka?**"
            answer = correct_title.lower()
            answer_display = f"{labels[correct_idx]}. {correct_title}"
        else:
            try:
                yr = int(correct_year)
                year_options = [str(yr), str(yr-1), str(yr+1), str(yr-2)]
                random.shuffle(year_options)
                correct_idx = year_options.index(str(yr))
                labels = ["A", "B", "C", "D"]
                options = year_options
                question = f"🎵 **Song:** {correct_title}\n👤 {correct_artist.split(',')[0].strip()}\n\n❓ **Kab release hua ye song?**"
                answer = str(yr)
                answer_display = f"{labels[correct_idx]}. {yr}"
            except:
                wrong_options = random.sample(wrong_pool, min(3, len(wrong_pool)))
                options = [correct_title] + [s["name"] for s in wrong_options]
                random.shuffle(options)
                correct_idx = options.index(correct_title)
                labels = ["A", "B", "C", "D"]
                question = f"👤 **Artist:** {correct_artist.split(',')[0].strip()}\n\n❓ **Kaunsa song hai is artist ka?**"
                answer = correct_title.lower()
                answer_display = f"{labels[correct_idx]}. {correct_title}"

    active_quiz[chat_id] = {
        "answer": answer, "title": correct_title,
        "artist": correct_artist, "type": "quiz",
        "options": options, "quiz_subtype": quiz_type
    }
    
    text = f"🎮 **Music Quiz!**\n\n{question}\n\n"
    for i, opt in enumerate(options[:4]):
        text += f"**{labels[i]}.** {opt}\n"
    text += "\n💭 Reply A, B, C or D!\n⏱ 20 seconds!"
    await msg.edit(text)
    await asyncio.sleep(20)
    if chat_id in active_quiz and active_quiz[chat_id].get("type") == "quiz":
        del active_quiz[chat_id]
        await m.reply(f"⏱ **Time's up!**\nAnswer: **{answer_display}**")

@safe_handler
@app.on_message(filters.command("mystats"))
async def mystats(_, m: Message):
    user_id = m.from_user.id
    user = db.get_user(user_id)
    if not user or user["downloads"] == 0:
        await m.reply(f"👤 **{get_user_name(m)}'s Stats:**\n\n📥 Downloads: 0\n\n💡 **Get started:**\n📥 `/download Tum Hi Ho`\n🎮 `/wordle` — Play a quick game!")
        return
    songs = db.get_history(user_id, 50)
    most = max(set(songs), key=songs.count) if songs else "None"
    xp = user.get("xp", 0)
    level = user.get("level", 1)
    await m.reply(f"👤 **{get_user_name(m)}'s Stats:**\n\n"
                  f"📥 Downloads: {user['downloads']}\n"
                  f"🎵 Most Downloaded: {most}\n"
                  f"📜 History: {len(db.get_history(user_id))}\n"
                  f"⭐ Favorites: {db.count_favorites(user_id)}\n"
                  f"🔥 Streak: {user.get('streak', 0)} days\n"
                  f"✨ XP: {xp} | {get_xp_bar(xp)}\n"
                  f"🎖 Level: {level} — {get_level_title(level)}\n"
                  f"🎸 Genre: {get_user_genre_from_history(user_id)}\n"
                  f"🏅 Rank: {get_level(user['downloads'])}")

@safe_handler
@app.on_message(filters.command("mywishlist"))
async def mywishlist(_, m: Message):
    items = db.get_wishlist(m.from_user.id)
    if not items:
        await m.reply("📋 **Your wishlist is empty!**\n\n💡 **Save songs for later:**\n`/wishlist Tum Hi Ho`\n`/wishlist Blinding Lights`")
        return
    text = "📋 **Your Wishlist:**\n\n"
    for i, s in enumerate(items, 1):
        text += f"{i}. {s}\n"
    text += "\n📥 `/download [song name]`"
    await m.reply(text)

# N

@safe_handler
@app.on_message(filters.command("newreleases"))
async def newreleases(_, m: Message):
    msg = await m.reply("🆕 **Fetching latest releases...**")
    try:
        queries = [
            "new hindi songs 2025", "latest bollywood 2025",
            "new releases india 2025", "new songs april 2025"
        ]
        results = []
        for q in queries:
            results += search_jiosaavn_multiple(q, 5)
        seen, unique = set(), []
        for s in results:
            if s["name"] not in seen:
                seen.add(s["name"])
                unique.append(s)
        if not unique:
            await msg.edit("❌ Could not fetch new releases!")
            return
        text = "🆕 **Latest Releases 2025:**\n\n"
        for i, s in enumerate(unique[:10], 1):
            artist = s.get("primaryArtists", s.get("artist", "Unknown"))
            text += f"{i}. **{s['name']}** — {artist}\n"
        text += "\n👇 Tap to download:"
        num_emojis = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣"]
        btn_rows2 = []
        row2 = []
        result_list = similar_tracks[:6] if similar_tracks and len(similar_tracks) >= 3 else unique[:6]
        for i, t in enumerate(result_list):
            name = t.get("name", "") if isinstance(t, dict) else t
            row2.append(InlineKeyboardButton(num_emojis[i], callback_data=f"dl_{name[:30]}"))
            if len(row2) == 3:
                btn_rows2.append(row2)
                row2 = []
        if row2:
            btn_rows2.append(row2)
        await msg.edit(text, reply_markup=InlineKeyboardMarkup(btn_rows2) if btn_rows2 else None)
    except Exception as e:
        await msg.edit("❌ Could not fetch! Try again.")
        print(f"[newreleases] {e}")


@safe_handler
@app.on_message(filters.command("note"))
async def note(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or "|" not in parts[1]:
        await m.reply("❌ Format: `/note Song | Note`\nExample: `/note Tum Hi Ho | Best song ever!`")
        return
    song, note_text = parts[1].split("|", 1)
    db.save_note(m.from_user.id, song.strip(), note_text.strip())
    await m.reply(f"📝 **Note saved!**\n\n🎵 **{song.strip()}**\n💬 _{note_text.strip()}_")

# P



@safe_handler
@app.on_message(filters.command("ping"))
async def ping(_, m: Message):
    start = datetime.datetime.now()
    msg = await m.reply("🏓 **Pinging...**")
    latency = (datetime.datetime.now() - start).microseconds // 1000
    await msg.edit(f"🏓 **Pong!**\n\n⚡ Latency: **{latency}ms**\n✅ Status: Online")



@safe_handler
@app.on_message(filters.command("playlist"))
async def playlist(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("❌ Example: `/playlist happy`\nAvailable: `happy` `sad` `party` `romantic` `workout` `chill`")
        return
    mood_type = parts[1].strip().lower()
    queries = {"happy": "happy upbeat bollywood", "sad": "sad emotional hindi", "party": "party dance hindi", "romantic": "romantic love hindi", "workout": "workout gym motivation", "chill": "chill relaxing hindi"}
    emojis = {"happy": "😊", "sad": "😢", "party": "🎉", "romantic": "💕", "workout": "💪", "chill": "😌"}
    if mood_type not in queries:
        await m.reply("❌ Available: `happy` `sad` `party` `romantic` `workout` `chill`")
        return
    results = search_jiosaavn_multiple(queries[mood_type], 5)
    await m.reply(f"🎵 **{mood_type.capitalize()} Playlist** {emojis[mood_type]}\nDownloading {len(results)} songs...\n⚠️ Few minutes!")
    for s in results:
        try:
            msg = await m.reply(f"⬇️ `{s['name']}`...")
            await send_song(m, s["name"], msg)
            await asyncio.sleep(2)
        except: pass


@safe_handler
@app.on_message(filters.command("profile"))
async def profile(_, m: Message):
    user_id = m.from_user.id
    db.ensure_user(user_id, get_user_name(m))
    user = db.get_user(user_id)
    downloads = user["downloads"]
    xp = user.get("xp", 0)
    level = user.get("level", 1)
    songs = db.get_history(user_id, 50)
    most = max(set(songs), key=songs.count) if songs else "None"
    badge_list = get_badges(user_id)
    await m.reply(f"👤 **{get_user_name(m)}'s Profile**\n\n"
                  f"📅 Since: {user.get('joined', 'Unknown')}\n"
                  f"📥 Downloads: {downloads}\n"
                  f"🎵 Top Song: {most}\n"
                  f"🎸 Genre: {get_user_genre_from_history(user_id)}\n"
                  f"⭐ Favorites: {db.count_favorites(user_id)}\n"
                  f"🔥 Streak: {user.get('streak', 0)} days\n"
                  f"✨ XP: {xp}\n"
                  f"{get_xp_bar(xp)}\n"
                  f"🎖 Level: {level} — {get_level_title(level)}\n"
                  f"🔔 Subscribed: {'Yes ✅' if db.is_subscribed(user_id) else 'No ❌'}\n\n"
                  f"**Badges:**\n" + "\n".join(f"• {b}" for b in badge_list))


@safe_handler
@app.on_message(filters.command("quality"))
async def quality_select(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("❌ Example: `/quality Tum Hi Ho`")
        return
    song = parts[1].strip()
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎵 128 kbps", callback_data=f"qual_128_{song[:30]}"),
        InlineKeyboardButton("🎵 192 kbps", callback_data=f"qual_192_{song[:30]}"),
        InlineKeyboardButton("🎵 320 kbps", callback_data=f"qual_320_{song[:30]}"),
    ]])
    await m.reply(f"🎧 **Select Quality:**\n`{song}`\n\n128kbps — Data saver 📶\n192kbps — Balanced ⚖️\n320kbps — Best quality 🎵", reply_markup=keyboard)

@safe_handler
@app.on_message(filters.command("quote"))
async def quote(_, m: Message):
    msg = await m.reply("💬 **Fetching quote...**")
    await msg.edit(f"💬 **Music Quote:**\n\n{fetch_quote()}")

# R

@safe_handler
@app.on_message(filters.command("random"))
async def random_song(_, m: Message):
    keywords = [
        "hindi popular songs", "bollywood hits 2024", "arijit singh songs",
        "romantic hindi songs", "punjabi hits", "party songs hindi",
        "sad hindi songs", "new bollywood 2024", "shreya ghoshal songs",
        "atif aslam songs", "english pop hits", "jubin nautiyal songs"
    ]
    msg = await m.reply("🎲 **Fetching random song...**")
    try:
        results = search_jiosaavn_multiple(random.choice(keywords), 20)
        if not results:
            await msg.edit("❌ No songs found! Try again.")
            return
        song = random.choice(results)
        await msg.edit(f"🎲 **Random Pick:** `{song['name']}`\nDownloading...")
        await send_song(m, song["name"], msg)
    except Exception as e:
        await msg.edit("❌ Could not fetch! Try again.")
        print(f"[random] {e}")

@safe_handler
@app.on_message(filters.command("rate"))
async def rate(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("❌ Example: `/rate Tum Hi Ho`")
        return
    song = parts[1].strip()
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("1⭐", callback_data=f"rate_1_{song[:25]}"),
        InlineKeyboardButton("2⭐", callback_data=f"rate_2_{song[:25]}"),
        InlineKeyboardButton("3⭐", callback_data=f"rate_3_{song[:25]}"),
        InlineKeyboardButton("4⭐", callback_data=f"rate_4_{song[:25]}"),
        InlineKeyboardButton("5⭐", callback_data=f"rate_5_{song[:25]}"),
    ]])
    await m.reply(f"⭐ **Rate:** `{song}`", reply_markup=keyboard)


@safe_handler
@app.on_message(filters.command("regional"))
async def regional(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("🌍 **Choose:**\n`/regional marathi` `/regional tamil` `/regional telugu`\n`/regional bhojpuri` `/regional bengali` `/regional gujarati`")
        return
    lang = parts[1].strip().lower()
    msg = await m.reply(f"🌍 **Fetching {lang} songs...**")
    results = apis.search_by_language(lang, 10)
    if not results:
        await msg.edit("❌ No songs found!")
        return
    text = f"🌍 **Top {lang.capitalize()} Songs:**\n\n"
    for i, s in enumerate(results[:10], 1):
        artist = s.get("artist", s.get("primaryArtists", "Unknown"))
        text += f"{i}. **{s['name']}** - {artist}\n"
    text += "\n📥 `/download [song name]`"
    await msg.edit(text)


@safe_handler
@app.on_message(filters.command("removefav"))
async def removefav(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("❌ Example: `/removefav Tum Hi Ho`")
        return
    if db.remove_favorite(m.from_user.id, parts[1].strip()):
        await m.reply(f"🗑 **Removed:** `{parts[1].strip()}`")
    else:
        await m.reply("❌ Not in favorites!")

@safe_handler
@app.on_message(filters.command("requestsong"))
async def requestsong(_, m: Message):
    if m.chat.type.name not in ("GROUP", "SUPERGROUP"):
        await m.reply("❌ Use this in a group!")
        return
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        await m.reply("❌ Example: `/requestsong Tum Hi Ho`")
        return
    song = parts[1].strip()
    await m.reply(f"🎵 **Song Request!**\n\n🎶 `{song}`\n👤 Requested by: **{get_user_name(m)}**\n\n📥 `/download {song}` to download!")

# S

@safe_handler
@app.on_message(filters.command("save"))
async def save(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("❌ Example: `/save Tum Hi Ho`")
        return
    query = parts[1].strip()
    user_id = m.from_user.id
    db.ensure_user(user_id, get_user_name(m))
    if db.is_favorite(user_id, query):
        await m.reply("⭐ Already in favorites!")
        return
    if db.count_favorites(user_id) >= 20:
        await m.reply("❌ Favorites full! Max 20.")
        return
    db.add_favorite(user_id, query)
    db.increment_song_favorites(query)
    await m.reply(f"⭐ **Saved:** `{query}`")

@safe_handler
@app.on_message(filters.command("search"))
async def search(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("❌ Example: `/search Arijit Singh`")
        return
    query = parts[1].strip()
    msg = await m.reply(f"🔍 **Searching:** `{query}`...")
    results = await asyncio.to_thread(search_jiosaavn_multiple, query, 6)
    if not results:
        await msg.edit("❌ No results found!")
        return
    text = f"🎵 **Search Results:**\n\n"
    for i, song in enumerate(results[:6], 1):
        d = int(song["duration"])
        text += f"{i}. **{song['name']}** — {song['primaryArtists']} | ⏱ {d//60}:{d%60:02d}\n"
    text += "\n👇 Tap to download:"
    num_emojis = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣"]
    btn_rows = []
    row = []
    for i, song in enumerate(results[:6]):
        row.append(InlineKeyboardButton(num_emojis[i], callback_data=f"dl_{song['name'][:30]}"))
        if len(row) == 3:
            btn_rows.append(row)
            row = []
    if row:
        btn_rows.append(row)
    keyboard = InlineKeyboardMarkup(btn_rows)
    await msg.edit(text, reply_markup=keyboard)

@safe_handler
@app.on_message(filters.command("secret"))
async def secret(_, m: Message):
    secrets = [
        "🔮 **Secret #1:** Type `/musicfact` for hidden music knowledge!",
        "🤫 **Secret #2:** Your streak gives you bonus XP! Try `/dailyreward`",
        "🔮 **Secret #3:** Rate songs with `/rate` to earn XP!",
        "🤫 **Secret #4:** Try `/party` in a group for the ultimate experience!",
        "🔮 **Secret #5:** `/easteregg` has more secrets hidden inside! 🥚",
    ]
    await m.reply(random.choice(secrets))




@safe_handler
@app.on_message(filters.command("skip"))
async def skip(_, m: Message):
    chat_id = m.chat.id
    if chat_id not in active_quiz:
        await m.reply("❌ No active quiz running!")
        return
    quiz = active_quiz.pop(chat_id)
    await m.reply(f"⏭ **Skipped!**\nThe answer was: **{quiz['title']}** by {quiz['artist']}")


@safe_handler
@app.on_message(filters.command("songbattle"))
async def songbattle(_, m: Message):
    if m.chat.type.name not in ("GROUP", "SUPERGROUP"):
        await m.reply("❌ Use this in a group!")
        return
    parts = m.text.split(None, 1)
    if len(parts) < 2 or "|" not in parts[1]:
        await m.reply("❌ Format: `/songbattle Song1 | Song2`\nExample: `/songbattle Husn | Kesariya`")
        return
    songs = parts[1].split("|")
    if len(songs) != 2:
        await m.reply("❌ 2 songs likho `|` se alag karke!")
        return
    s1, s2 = songs[0].strip(), songs[1].strip()
    group_id = m.chat.id
    group_votes[group_id] = {"songs": [s1, s2], "votes": {}, "active": True}
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🎵 {s1[:20]}", callback_data=f"vote_{group_id}_0"),
        InlineKeyboardButton(f"🎵 {s2[:20]}", callback_data=f"vote_{group_id}_1"),
    ]])
    msg = await m.reply(f"⚔️ **Song Battle!**\n\n🎵 **{s1}**\n  VS\n🎵 **{s2}**\n\nVote karo! ⏱ 30 seconds!", reply_markup=keyboard)
    await asyncio.sleep(30)
    if group_id in group_votes and group_votes[group_id].get("active"):
        votes = group_votes[group_id]["votes"]
        v0 = sum(1 for v in votes.values() if v == 0)
        v1 = sum(1 for v in votes.values() if v == 1)
        winner = s1 if v0 >= v1 else s2
        del group_votes[group_id]
        await m.reply(f"🏆 **Battle Result!**\n\n🎵 **{s1}**: {v0} votes\n🎵 **{s2}**: {v1} votes\n\n👑 **Winner: {winner}!**\n\n📥 `/download {winner}`")

@safe_handler
@app.on_message(filters.command("songstats"))
async def songstats(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("❌ Example: `/songstats Husn`")
        return
    query = parts[1].strip()
    msg = await m.reply(f"📊 **Fetching stats:** `{query}`...")
    dl_url, title, duration, song_data = search_jiosaavn(query)
    if not song_data:
        await msg.edit("❌ Song not found!")
        return
    song_name = song_data['name']
    g_stats = db.get_song_global_stats(song_name)
    avg_rating, vote_count = db.get_avg_rating(song_name[:25])
    reactions = db.get_song_reactions(song_name[:25])
    await msg.edit(f"📊 **{song_name}**\n\n"
                   f"👤 {song_data['primaryArtists']}\n"
                   f"💿 {song_data.get('album',{}).get('name','Unknown')} | 📅 {song_data.get('year','Unknown')}\n\n"
                   f"📥 **Bot Downloads:** {g_stats['downloads']}\n"
                   f"⭐ **Favorites:** {g_stats['favorites']}\n"
                   f"🌟 **Rating:** {'⭐ ' + f'{avg_rating:.1f}/5 ({vote_count} votes)' if vote_count > 0 else 'Not rated yet'}\n"
                   f"👍 Likes: {reactions.get('like',0)} | 🔥 Fire: {reactions.get('fire',0)} | 💔 Sad: {reactions.get('sad',0)}\n\n"
                   f"📥 `/download {song_name}`")

@safe_handler
@app.on_message(filters.command("start"))
async def start(_, m: Message):
    user_id = m.from_user.id
    db.ensure_user(user_id, get_user_name(m))
    _bot_raw = BOT_USERNAME.replace("@", "")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 Music", callback_data="menu_music_1"),
         InlineKeyboardButton("🌍 Discover", callback_data="menu_discover_1")],
        [InlineKeyboardButton("🎮 Games", callback_data="menu_games_1"),
         InlineKeyboardButton("🕹 Fun Games", callback_data="menu_fun_1")],
        [InlineKeyboardButton("💬 Chat & More", callback_data="menu_chat_1"),
         InlineKeyboardButton("👤 Profile", callback_data="menu_profile_1")],
        [InlineKeyboardButton("📊 Stats", callback_data="menu_stats_1")],
        [InlineKeyboardButton("➕ Add BeatNova to Your Group", url=f"https://t.me/{_bot_raw}?startgroup=true")],
    ])
    await m.reply(
        f"🎧 **Welcome to BeatNova!**\n\n"
        f"Download your favorite songs instantly 🎵\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📥 Try now:\n"
        f"`/download Tum Hi Ho`\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👇 Tap below to explore\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🎧 Powered by BeatNova\n"
        f"⚠️ Support: @BF_ZeroShade",
        reply_markup=keyboard
    )

@safe_handler
@app.on_message(filters.command("stats"))
async def bot_stats(_, m: Message):
    update_today_stats()
    uptime = datetime.datetime.now() - START_TIME
    hours = int(uptime.total_seconds() // 3600)
    mins = int((uptime.total_seconds() % 3600) // 60)
    await m.reply(f"📊 **{BOT_NAME} Statistics:**\n\n"
                  f"👥 Total Users: {db.get_total_users()}\n"
                  f"📥 Total Downloads: {db.get_total_downloads()}\n"
                  f"📅 Today: {today_downloads['count']}\n"
                  f"🔔 Subscribers: {len(db.get_subscribers())}\n"
                  f"⏰ Uptime: {hours}h {mins}m\n"
                  f"🎵 Database: JioSaavn + SQLite\n"
                  f"━━━━━━━━━━━━━━━\n"
                  f"🎧 Powered by BeatNova")


@safe_handler
@app.on_message(filters.command("streak"))
async def streak(_, m: Message):
    user_id = m.from_user.id
    db.ensure_user(user_id, get_user_name(m))
    u = db.get_user(user_id)
    current_streak = u["streak"] if u else 0
    if current_streak == 0:
        await m.reply("🔥 **Streak: 0 days**\n\nDownload a song today to start! 🎵\n🎁 `/dailyreward` — Claim free XP!")
        return
    if current_streak >= 30: emoji = "👑"
    elif current_streak >= 7: emoji = "⚡"
    elif current_streak >= 3: emoji = "🔥"
    else: emoji = "✨"
    await m.reply(f"{emoji} **{get_user_name(m)}'s Streak:**\n\n"
                  f"🔥 **{current_streak} day streak!**\n\n"
                  f"{'👑 Legendary!' if current_streak >= 30 else '⚡ Week streak! Amazing!' if current_streak >= 7 else '🔥 3 days! Keep going!' if current_streak >= 3 else '✨ Good start!'}\n\n"
                  f"📥 Download daily to keep it going!")

@safe_handler
@app.on_message(filters.command("subscribe"))
async def subscribe(_, m: Message):
    user_id = m.from_user.id
    if db.is_subscribed(user_id):
        await m.reply("🔔 Already subscribed!\nUse `/unsubscribe` to stop.")
        return
    db.ensure_user(user_id, get_user_name(m))
    db.set_subscribed(user_id, True)
    await m.reply("🔔 **Subscribed!**\n\nHar roz subah 9 AM par ek song milega!\nUse `/unsubscribe` to stop.")

# T


@safe_handler
@app.on_message(filters.command("topartist"))
async def topartist(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("❌ Example: `/topartist Arijit Singh`")
        return
    query = parts[1].strip()
    msg = await m.reply(f"🏆 **Top songs by:** `{query}`...")
    results = search_jiosaavn_multiple(f"best of {query}", 8)
    if not results:
        await msg.edit("❌ No results!")
        return
    text = f"🏆 **Top Songs by {query}:**\n\n"
    for i, s in enumerate(results, 1):
        d = int(s["duration"])
        text += f"{i}. **{s['name']}** | ⏱ {d//60}:{d%60:02d}\n"
    text += "\n📥 `/download [song name]`"
    await msg.edit(text)

@safe_handler
@app.on_message(filters.command("topbollywood"))
async def topbollywood(_, m: Message):
    msg = await m.reply("🎬 **Fetching Top Bollywood...**")
    results = search_jiosaavn_multiple("top bollywood hits 2024", 5)
    results += search_jiosaavn_multiple("best bollywood songs popular", 5)
    seen, unique = set(), []
    for s in results:
        if s["name"] not in seen:
            seen.add(s["name"])
            unique.append(s)
    text = "🎬 **Top Bollywood Songs:**\n\n"
    for i, s in enumerate(unique[:10], 1):
        text += f"{i}. **{s['name']}** - {s['primaryArtists']}\n"
    text += "\n📥 `/download [song name]`"
    await msg.edit(text)

@safe_handler
@app.on_message(filters.command("topindia"))
async def topindia(_, m: Message):
    msg = await m.reply("🇮🇳 **Fetching Top India...**")
    results = search_jiosaavn_multiple("hindi hits popular 2024", 5)
    results += search_jiosaavn_multiple("trending bollywood 2024", 5)
    seen, unique = set(), []
    for s in results:
        if s["name"] not in seen:
            seen.add(s["name"])
            unique.append(s)
    text = "🇮🇳 **Top Songs in India:**\n\n"
    for i, s in enumerate(unique[:10], 1):
        text += f"{i}. **{s['name']}** - {s['primaryArtists']}\n"
    text += "\n📥 `/download [song name]`"
    await msg.edit(text)

@safe_handler
@app.on_message(filters.command("topsongs"))
async def topsongs(_, m: Message):
    top = db.get_top_rated_songs()
    if not top:
        await m.reply("❌ No rated songs yet!\nUse `/rate [song]`")
        return
    text = "🏆 **Top Rated Songs:**\n\n"
    for i, row in enumerate(top, 1):
        text += f"{i}. **{row['song']}** — ⭐ {row['avg_r']:.1f}/5 ({row['cnt']} votes)\n"
    await m.reply(text)

@safe_handler
@app.on_message(filters.command("topuser"))
async def topuser(_, m: Message):
    if m.chat.type.name not in ("GROUP", "SUPERGROUP"):
        await m.reply("❌ Use this in a group!")
        return
    top = db.get_group_leaderboard(m.chat.id, 1)
    if not top:
        await m.reply("❌ No downloads in this group yet!")
        return
    await m.reply(f"🥇 **Top User in {m.chat.title}:**\n\n"
                  f"👤 **{top[0]['user_name']}**\n📥 Downloads: {top[0]['downloads']}\n\n"
                  f"🏆 `/gleaderboard` — Full ranking")


@safe_handler
@app.on_message(filters.command("tournament"))
async def tournament(_, m: Message):
    msg = await m.reply("🏆 **Setting up Tournament...**")
    results = search_jiosaavn_multiple("popular hindi songs hits", 8)
    if len(results) < 4:
        await msg.edit("❌ Could not fetch songs!")
        return
    songs = [s["name"] for s in results[:8]]
    text = "🏆 **Song Tournament!**\n\n**🎵 Contestants:**\n\n"
    for i, s in enumerate(songs, 1):
        text += f"{i}. {s}\n"
    text += "\n**Vote with the number of your favourite!** 🎵"
    await msg.edit(text)

@safe_handler
@app.on_message(filters.command("trendingartist"))
async def trendingartist(_, m: Message):
    msg = await m.reply("🔥 **Fetching Trending Artists...**")
    results = []
    for q in ["trending hindi 2024", "popular bollywood 2024", "viral songs 2024"]:
        results += search_jiosaavn_multiple(q, 5)
    artists, seen_artists = [], set()
    for s in results:
        for a in s.get("primaryArtists", "").split(","):
            a = a.strip()
            if a and a not in seen_artists:
                seen_artists.add(a)
                artists.append(a)
    if not artists:
        await msg.edit("❌ Could not fetch!")
        return
    text = "🔥 **Trending Artists:**\n\n"
    for i, a in enumerate(artists[:10], 1):
        text += f"{i}. **{a}**\n"
    text += f"\n🎵 Use `/artist [name]` to see their songs!"
    await msg.edit(text)

@safe_handler
@app.on_message(filters.command("trending"))
async def trending(_, m: Message):
    parts = m.text.split(None, 1)
    country = parts[1].strip().lower() if len(parts) > 1 and parts[1].strip() else "india"
    msg = await m.reply(f"🌍 **Fetching trending in {country.title()}...**")
    try:
        # Try LastFM
        tracks = await asyncio.to_thread(apis.get_trending, country)
        if tracks and len(tracks) >= 3:
            text = f"🌍 **Trending in {country.title()}:**\n\n"
            for i, t in enumerate(tracks[:10], 1):
                text += f"{i}. **{t['name']}** — {t['artist']}\n"
            text += "\n📥 `/download [song name]`"
            await msg.edit(text)
            return
        # Fallback JioSaavn
        queries = ["trending india 2025", "viral hindi songs 2025", "top hits india april 2025"]
        results = []
        for q in queries:
            results += search_jiosaavn_multiple(q, 5)
        seen, unique = set(), []
        for s in results:
            if s["name"] not in seen:
                seen.add(s["name"])
                unique.append(s)
        if not unique:
            await msg.edit("❌ Could not fetch trending!")
            return
        text = "🌍 **Trending Right Now:**\n\n"
        for i, s in enumerate(unique[:10], 1):
            artist = s.get("primaryArtists", s.get("artist", "Unknown"))
            text += f"{i}. **{s['name']}** — {artist}\n"
        text += "\n📥 `/download [song name]`\n💡 Try: `/trending punjabi` or `/trending global`"
        await msg.edit(text)
    except Exception as e:
        await msg.edit("❌ Could not fetch! Try again.")
        print(f"[trending] {e}")

# U

@safe_handler
@app.on_message(filters.command("unsubscribe"))
async def unsubscribe(_, m: Message):
    user_id = m.from_user.id
    if not db.is_subscribed(user_id):
        await m.reply("❌ Not subscribed!\nUse `/subscribe` to start.")
        return
    db.set_subscribed(user_id, False)
    await m.reply("🔕 **Unsubscribed!**\nYou won't receive daily songs anymore.")

@safe_handler
@app.on_message(filters.command("uptime"))
async def uptime(_, m: Message):
    delta = datetime.datetime.now() - START_TIME
    total = int(delta.total_seconds())
    days, hours = total // 86400, (total % 86400) // 3600
    mins, secs = (total % 3600) // 60, total % 60
    await m.reply(f"⏰ **{BOT_NAME} Uptime:**\n\n🕐 **{days}d {hours}h {mins}m {secs}s**\n\n✅ Status: Online\n🤖 Bot: {BOT_USERNAME}")

# V

@safe_handler
@app.on_message(filters.command("vibe"))
async def vibe(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("❌ Example: `/vibe Tum Hi Ho`")
        return
    query = parts[1].strip()
    msg = await m.reply(f"🎭 **Analyzing vibe:** `{query}`...")
    dl_url, title, duration, song_data = search_jiosaavn(query)
    if not song_data:
        await msg.edit("❌ Song not found!")
        return
    name = song_data.get("name", "").lower()
    mins, secs = duration // 60, duration % 60
    lang = song_data.get("language", "").lower()
    if any(k in name for k in ["sad","dard","judai","alvida","rona","toota","bekhayali","tanha","rone","roya","aansoo","dard","gham","dil toota"]):
        vibe_r, desc = "😢 Sad / Emotional", "Perfect for heartfelt moments. Grab tissues! 🤧"
    elif any(k in name for k in ["love","ishq","pyar","mohabbat","dil","kesariya","raataan","tera","mera","tere","romantic","saathiya","rabba"]):
        vibe_r, desc = "💕 Romantic", "Perfect for date nights and special moments. 🌹"
    elif any(k in name for k in ["party","dance","dj","club","nachna","jalsa","badtameez","hookah","Saturday","Saturday","Friday"]):
        vibe_r, desc = "🎉 Party / Dance", "Turn it up! Perfect for celebrations. 🔥"
    elif any(k in name for k in ["power","fire","thunder","believer","warrior","champion","winner","rise","fight","hero"]):
        vibe_r, desc = "💪 Energetic / Motivational", "Perfect for workouts and hustle! 🏋️"
    elif any(k in name for k in ["chill","lofi","slow","calm","peaceful","sleep","rain","coffee","lazy"]):
        vibe_r, desc = "😌 Chill / Relaxing", "Perfect for lazy days and relaxing. ☁️"
    elif duration > 320:
        vibe_r, desc = "🎭 Epic / Cinematic", f"A long {duration//60}:{duration%60:02d} min epic! 🎬"
    elif duration < 160:
        vibe_r, desc = "⚡ Short & Punchy", "Quick but impactful! ⚡"
    elif lang in ["punjabi"]:
        vibe_r, desc = "🎵 Punjabi Vibe", "Full on Punjabi energy! 🕺"
    else:
        vibe_r, desc = "😌 Chill / Neutral", "Good for any mood and any time!"
    await msg.edit(f"🎭 **Vibe Analysis:**\n\n🎵 **{song_data['name']}**\n"
                   f"👤 {song_data['primaryArtists']}\n"
                   f"⏱ {mins}:{secs:02d} | 🌐 {song_data.get('language','Unknown').capitalize()}\n\n"
                   f"**Vibe:** {vibe_r}\n📝 {desc}")

@safe_handler
@app.on_message(filters.command("votesong"))
async def votesong(_, m: Message):
    if m.chat.type.name not in ("GROUP", "SUPERGROUP"):
        await m.reply("❌ Use this in a group!")
        return
    msg = await m.reply("📊 **Creating Song Vote...**")
    results = search_jiosaavn_multiple("popular hindi songs", 10)
    if not results:
        await msg.edit("❌ Could not fetch!")
        return
    songs = random.sample(results, min(4, len(results)))
    group_id = m.chat.id
    group_votes[group_id] = {"songs": [s["name"] for s in songs], "votes": {}, "active": True}
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🎵 {songs[0]['name'][:20]}", callback_data=f"vote_{group_id}_0"),
         InlineKeyboardButton(f"🎵 {songs[1]['name'][:20]}", callback_data=f"vote_{group_id}_1")],
        [InlineKeyboardButton(f"🎵 {songs[2]['name'][:20]}", callback_data=f"vote_{group_id}_2"),
         InlineKeyboardButton(f"🎵 {songs[3]['name'][:20]}", callback_data=f"vote_{group_id}_3")] if len(songs) > 3 else []
    ])
    text = "📊 **Group Song Vote!**\n\nKaunsa song download karein?\n\n"
    for i, s in enumerate(songs, 1):
        text += f"{i}. {s['name']}\n"
    text += "\n⏱ 30 seconds!"
    await msg.edit(text, reply_markup=keyboard)
    await asyncio.sleep(30)
    if group_id in group_votes and group_votes[group_id].get("active"):
        votes = group_votes[group_id]["votes"]
        song_names = group_votes[group_id]["songs"]
        counts = [sum(1 for v in votes.values() if v == i) for i in range(len(song_names))]
        winner_idx = counts.index(max(counts))
        winner = song_names[winner_idx]
        del group_votes[group_id]
        result_text = "📊 **Vote Result!**\n\n"
        for i, (s, c) in enumerate(zip(song_names, counts)):
            result_text += f"{'👑 ' if i == winner_idx else '  '}**{s}**: {c} votes\n"
        result_text += f"\n🏆 **Winner: {winner}!**\n📥 `/download {winner}`"
        await m.reply(result_text)

# W

@safe_handler
@app.on_message(filters.command("wishlist"))
async def wishlist(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip() or parts[1].strip().lower() in PLACEHOLDERS:
        await m.reply("❌ Example: `/wishlist Tum Hi Ho`\nView: `/mywishlist`")
        return
    query = parts[1].strip()
    user_id = m.from_user.id
    db.ensure_user(user_id, get_user_name(m))
    if not db.add_wishlist(user_id, query):
        await m.reply("📋 Already in wishlist!")
        return
    await m.reply(f"📋 **Added to Wishlist:** `{query}`\n\nView: `/mywishlist`\nDownload: `/download {query}`")

# Y

@safe_handler
@app.on_message(filters.command("year"))
async def year_cmd(_, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        await m.reply("❌ Example: `/year 2000`")
        return
    year = parts[1].strip()
    if not year.isdigit() or not (1990 <= int(year) <= 2025):
        await m.reply("❌ Valid year likho (1990-2025)!")
        return
    msg = await m.reply(f"📅 **Fetching songs from {year}...**")
    results = search_jiosaavn_multiple(f"hindi songs {year} hits", 8)
    if not results:
        await msg.edit("❌ No songs found!")
        return
    text = f"📅 **Songs from {year}:**\n\n"
    for i, s in enumerate(results, 1):
        text += f"{i}. **{s['name']}** - {s['primaryArtists']}\n"
    text += "\n📥 `/download [song name]`"
    await msg.edit(text)


# ========== QUIZ CHECK (always last) ==========

@safe_handler
@app.on_message(filters.text & ~filters.regex(r"^/"))
async def quiz_check(_, m: Message):
    chat_id = m.chat.id
    if chat_id not in active_quiz:
        return
    quiz = active_quiz[chat_id]
    user_ans = m.text.strip().lower()
    correct = quiz["answer"].lower()
    quiz_type = quiz.get("type", "guess")

    if quiz_type in ("quiz", "artistquiz"):
        option_map = {"a": 0, "b": 1, "c": 2, "d": 3}
        if user_ans in option_map:
            idx = option_map[user_ans]
            if idx >= len(quiz.get("options", [])):
                return
            selected = quiz["options"][idx]
            if selected.lower() == correct:
                del active_quiz[chat_id]
                db.ensure_user(m.from_user.id, get_user_name(m))
                db.add_xp(m.from_user.id, XP_REWARDS["quiz_win"])
                await m.reply(
                    f"✅ **Sahi Jawab! {get_user_name(m)}!** 🎉\n"
                    f"🎵 **{quiz['title']}** — {quiz['artist']}\n"
                    f"✨ **+{XP_REWARDS['quiz_win']} XP!**\n\n"
                    f"📥 `/download {quiz['title']}`"
                )
            else:
                await m.reply(f"❌ **Galat!** Dobara try karo! 💡")

    elif quiz_type == "fillblank":
        if user_ans == correct or correct in user_ans:
            del active_quiz[chat_id]
            db.ensure_user(m.from_user.id, get_user_name(m))
            db.add_xp(m.from_user.id, XP_REWARDS["quiz_win"])
            await m.reply(f"✅ **Correct! {get_user_name(m)}!** 🎉\n"
                          f"Word: **{correct}** | Song: **{quiz['title']}**\n"
                          f"✨ **+{XP_REWARDS['quiz_win']} XP!**")
        else:
            await m.reply(f"❌ **Wrong!** Starts with **{correct[0]}**")

    elif quiz_type == "yeargame":
        if user_ans == correct or user_ans in correct:
            del active_quiz[chat_id]
            db.ensure_user(m.from_user.id, get_user_name(m))
            db.add_xp(m.from_user.id, XP_REWARDS["quiz_win"])
            await m.reply(f"✅ **Sahi! {get_user_name(m)}!** 🎉\nYear: **{correct}**\n✨ **+{XP_REWARDS['quiz_win']} XP!**")
        else:
            try:
                diff = abs(int(user_ans) - int(correct))
                if diff <= 1: hint = "🔥 Bahut close!"
                elif diff <= 3: hint = "📅 Kaafi close!"
                else: hint = "📅 Dobara try karo!"
                await m.reply(f"❌ **Galat!** {hint}")
            except:
                await m.reply("❌ Sirf year number reply karo!")

    else:  # guess
        if any(w in user_ans for w in correct.split() if len(w) > 3):
            del active_quiz[chat_id]
            db.ensure_user(m.from_user.id, get_user_name(m))
            db.add_xp(m.from_user.id, XP_REWARDS["quiz_win"])
            await m.reply(f"✅ **Correct! {get_user_name(m)}!** 🎉\n"
                          f"🎵 **{quiz['title']}** by {quiz['artist']}\n"
                          f"✨ **+{XP_REWARDS['quiz_win']} XP!**\n\n"
                          f"📥 `/download {quiz['title']}`")

# ========== DAILY SONG TASK ==========

async def send_daily_songs():
    while True:
        now = datetime.datetime.now()
        # Daily song at 9 AM
        if now.hour == 9 and now.minute == 0:
            subs = db.get_subscribers()
            if subs:
                results = search_jiosaavn_multiple("popular hindi songs 2024", 20)
                if results:
                    song = random.choice(results)
                    for user_id in subs:
                        try:
                            msg_obj = await app.send_message(user_id,
                                f"🔔 **Good Morning! Daily Song from {BOT_NAME}:**\n\n"
                                f"🎵 `{song['name']}`\n\n⬇️ Downloading...")
                            await send_song(msg_obj, song["name"], msg_obj)
                        except: pass
        await asyncio.sleep(60)

async def send_micro_tips():
    """Send random micro tips to active users every few hours"""
    await asyncio.sleep(300)  # Wait 5 min after bot starts
    while True:
        try:
            # Send tip to recently active users (last 10 downloaders)
            all_users = db.get_all_users()
            active = [u["user_id"] for u in all_users[:10] if u.get("downloads", 0) > 0]
            if active:
                tip = random.choice(MICRO_TIPS)
                target = random.choice(active)
                await app.send_message(target, tip)
        except: pass
        # Every 4-6 hours randomly
        await asyncio.sleep(random.randint(14400, 21600))

async def send_hook_challenge():
    """Send random mini song challenge to active users"""
    await asyncio.sleep(600)  # Wait 10 min after bot starts
    while True:
        try:
            all_users = db.get_all_users()
            active = [u["user_id"] for u in all_users[:15] if u.get("downloads", 0) > 2]
            if active:
                song_name = random.choice(HOOK_SONGS)
                # Get lyrics hint
                lyrics, _ = get_lyrics(song_name)
                if lyrics:
                    lines = [l.strip() for l in lyrics.split("\n") if l.strip() and len(l.strip()) > 15]
                    if lines:
                        hint_line = random.choice(lines[:10])
                        target = random.choice(active)
                        keyboard = InlineKeyboardMarkup([[
                            InlineKeyboardButton("🎮 Guess Now!", callback_data=f"hook_guess_{song_name[:30]}"),
                            InlineKeyboardButton("⏭ Skip", callback_data="hook_skip"),
                        ]])
                        await app.send_message(
                            target,
                            f"🎯 **Mini Challenge!**\n\n"
                            f"Guess this song from the lyrics 👇\n\n"
                            f"*\"...{hint_line}...\"*\n\n"
                            f"Do you know it? 🎵",
                            reply_markup=keyboard
                        )
        except: pass
        # Every 3-5 hours randomly
        await asyncio.sleep(random.randint(10800, 18000))

@app.on_callback_query(filters.regex(r"^hook_guess_"))
async def hook_guess_callback(_, cb):
    song = cb.data[11:]
    await cb.answer("Starting guess game!", show_alert=False)
    msg = await cb.message.reply(f"🎯 **Guess:** Is it `{song}`?\n\nDownload to confirm: `/download {song}`")
    # Start a guesssong-style mini game
    await cb.message.reply(
        f"🎵 **Song Challenge!**\n\n"
        f"Use `/download {song}` to hear it and find out!\n\n"
        f"Or type the song name as your answer below 👇"
    )

@app.on_callback_query(filters.regex(r"^hook_skip$"))
async def hook_skip_callback(_, cb):
    await cb.answer("Skipped! Next challenge coming soon 😄", show_alert=True)
    await cb.message.delete()

# ==================== NEW GAMES ====================

SLOT_EMOJIS = ["🍒", "🍋", "🍊", "💎", "7️⃣", "🎵", "⭐", "🔔"]
SLOT_WINS = {
    ("💎","💎","💎"): ("JACKPOT! 💎💎💎", 500),
    ("7️⃣","7️⃣","7️⃣"): ("LUCKY 777! 🎰", 300),
    ("🎵","🎵","🎵"): ("MUSIC WIN! 🎵", 200),
    ("⭐","⭐","⭐"): ("TRIPLE STAR! ⭐", 150),
}

@safe_handler
@app.on_message(filters.command("slots"))
async def slots_cmd(_, m: Message):
    import random as _r
    s1, s2, s3 = _r.choice(SLOT_EMOJIS), _r.choice(SLOT_EMOJIS), _r.choice(SLOT_EMOJIS)
    msg = await m.reply("🎰 Spinning...")
    await asyncio.sleep(1)
    result = f"🎰 **SLOTS**\n\n| {s1} | {s2} | {s3} |\n\n"
    combo = (s1, s2, s3)
    if combo in SLOT_WINS:
        label, xp = SLOT_WINS[combo]
        db.ensure_user(m.from_user.id, get_user_name(m))
        db.add_xp(m.from_user.id, xp)
        result += f"🎉 **{label}**\n✨ +{xp} XP!"
    elif s1 == s2 or s2 == s3 or s1 == s3:
        db.ensure_user(m.from_user.id, get_user_name(m))
        db.add_xp(m.from_user.id, 50)
        result += f"✅ **2 same! Small win!**\n✨ +50 XP!"
    else:
        result += "❌ **Koi match nahi! Try again!**"
    result += "\n\n🎰 `/slots` — Dobara spin karo!"
    await msg.edit(result)

@safe_handler
@app.on_message(filters.command("dice"))
async def dice_cmd(_, m: Message):
    """Simple dice roll 1-6"""
    result = random.randint(1, 6)
    faces = ["", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"]
    msg_text = f"🎲 **Dice Roll!**\n\n{faces[result]} You rolled: **{result}**\n\n"
    if result == 6: msg_text += "🔥 **Max roll!** Lucky!"
    elif result == 1: msg_text += "😬 **Snake eyes!** Unlucky!"
    else: msg_text += "Roll again with `/dice`!"
    await m.reply(msg_text)

@safe_handler
@app.on_message(filters.command("guess"))
async def guess_cmd(_, m: Message):
    chat_id = m.chat.id
    parts = m.text.split(None, 1)
    # Check if number provided with command
    if len(parts) >= 2 and parts[1].strip().isdigit():
        # Guess attempt via command
        if chat_id not in active_guess:
            # Auto-start if no game active
            number = random.randint(1, 100)
            active_guess[chat_id] = {"number": number, "attempts": 0, "starter": get_user_name(m)}
            await m.reply(
                f"🔢 **Number Guess Game!**\n\n"
                f"I picked a number between 1-100!\n"
                f"Just reply with numbers to guess!\n"
                f"🏆 Fewer attempts = more XP!"
            )
            return
        await _process_guess(m, chat_id, int(parts[1].strip()))
        return
    # Start new game or show status
    if chat_id in active_guess:
        g = active_guess[chat_id]
        await m.reply(
            f"🔢 **Game Active!**\n\n"
            f"Number between 1-100\n"
            f"Attempts so far: **{g['attempts']}**\n\n"
            f"Just type a number to guess!\n"
            f"❌ `/endguess` — End game"
        )
    else:
        number = random.randint(1, 100)
        active_guess[chat_id] = {"number": number, "attempts": 0, "starter": get_user_name(m)}
        await m.reply(
            f"🔢 **Number Guess Game!**\n\n"
            f"I picked a number between **1-100**!\n\n"
            f"💭 **Just type a number to guess!**\n"
            f"🏆 Fewer attempts = more XP!\n"
            f"❌ `/endguess` — End game"
        )

async def _process_guess(m, chat_id, guess):
    g = active_guess[chat_id]
    g["attempts"] += 1
    active_guess[chat_id] = g
    if guess == g["number"]:
        del active_guess[chat_id]
        db.ensure_user(m.from_user.id, get_user_name(m))
        attempts = g["attempts"]
        xp = max(10, 100 - (attempts-1)*8)
        db.add_xp(m.from_user.id, xp)
        await m.reply(
            f"🎉 **CORRECT! {get_user_name(m)}!**\n\n"
            f"Number was: **{g['number']}**\n"
            f"Attempts: **{attempts}**\n"
            f"✨ +{xp} XP!\n\n"
            f"🔢 `/guess` — New game!"
        )
    elif guess < g["number"]:
        await m.reply(f"📈 **{guess}** — Go **higher**! (Attempt #{g['attempts']})")
    else:
        await m.reply(f"📉 **{guess}** — Go **lower**! (Attempt #{g['attempts']})")

@safe_handler
@app.on_message(filters.command("endguess"))
async def endguess_cmd(_, m: Message):
    chat_id = m.chat.id
    if chat_id not in active_guess:
        await m.reply("❌ No active guess game!")
        return
    g = active_guess.pop(chat_id)
    await m.reply(f"❌ **Game ended!**\nNumber was: **{g['number']}**")

@safe_handler
@app.on_message(filters.command("bomb"))
async def bomb_cmd(_, m: Message):
    if m.chat.type.name not in ("GROUP", "SUPERGROUP"):
        await m.reply("❌ Group only!")
        return
    chat_id = m.chat.id
    if chat_id in active_bomb:
        b = active_bomb[chat_id]
        if b.get("started"):
            await m.reply("💣 Bomb game already running!")
        else:
            players = b.get("players", {})
            names = ", ".join(players.values()) if players else "None"
            await m.reply(
                f"💣 **Bomb Game — Joining Phase!**\n\n"
                f"Players joined: **{len(players)}**\n"
                f"👥 {names}\n\n"
                f"⚡ `/joinb` — Join the game!\n"
                f"🚀 `/startbomb` — Start (2+ players needed)"
            )
        return
    # Create new lobby
    active_bomb[chat_id] = {
        "holder": None, "name": None,
        "players": {m.from_user.id: get_user_name(m)},
        "started": False
    }
    await m.reply(
        f"💣 **BOMB GAME LOBBY!**\n\n"
        f"**{get_user_name(m)}** created the game!\n\n"
        f"⚡ `/joinb` — Join karo (need 2+ players)\n"
        f"🚀 `/startbomb` — Start game\n"
        f"❌ `/cancelbomb` — Cancel"
    )

@safe_handler
@app.on_message(filters.command("joinb"))
async def joinbomb_cmd(_, m: Message):
    chat_id = m.chat.id
    if chat_id not in active_bomb:
        await m.reply("❌ No active bomb lobby! `/bomb` se create karo!")
        return
    b = active_bomb[chat_id]
    if b.get("started"):
        await m.reply("❌ Game already started!")
        return
    uid = m.from_user.id
    if uid in b["players"]:
        await m.reply(f"✅ {get_user_name(m)}, you already joined!")
        return
    b["players"][uid] = get_user_name(m)
    active_bomb[chat_id] = b
    names = ", ".join(b["players"].values())
    await m.reply(
        f"✅ **{get_user_name(m)} joined!**\n\n"
        f"👥 Players ({len(b['players'])}): {names}\n"
        f"🚀 `/startbomb` — Start when ready!"
    )

@safe_handler
@app.on_message(filters.command("startbomb"))
async def startbomb_cmd(_, m: Message):
    chat_id = m.chat.id
    if chat_id not in active_bomb:
        await m.reply("❌ No lobby! `/bomb` se create karo!")
        return
    b = active_bomb[chat_id]
    if b.get("started"):
        await m.reply("❌ Already started!")
        return
    if len(b["players"]) < 2:
        await m.reply("❌ Need at least 2 players! `/joinb` karo!")
        return
    # Pick random starting holder from players
    holder_id = random.choice(list(b["players"].keys()))
    holder_name = b["players"][holder_id]
    timer = random.randint(45, 120)
    b["holder"] = holder_id
    b["name"] = holder_name
    b["timer"] = timer
    b["started"] = True
    active_bomb[chat_id] = b
    names = ", ".join(b["players"].values())
    await m.reply(
        f"💣 **BOMB GAME STARTED!**\n\n"
        f"👥 Players: {names}\n\n"
        f"💣 Bomb starts with: **{holder_name}**\n"
        f"⏱ Timer: Hidden!\n\n"
        f"⚡ **Pass it! Reply to any player's message: `/passbomb`**\n"
        f"💥 Whoever holds it when it explodes — LOSES!"
    )
    asyncio.create_task(_bomb_timer(chat_id, m, timer))

async def _bomb_timer(chat_id, m, timer):
    await asyncio.sleep(timer)
    if chat_id in active_bomb and active_bomb[chat_id].get("started"):
        bomb = active_bomb.pop(chat_id)
        try:
            await m.reply(
                f"💥 **BOOM!**\n\n"
                f"**{bomb['name']}** was holding the bomb!\n\n"
                f"😂 **{bomb['name']} LOSES!** 💀"
            )
        except: pass

@safe_handler
@app.on_message(filters.command("cancelbomb"))
async def cancelbomb_cmd(_, m: Message):
    chat_id = m.chat.id
    if chat_id not in active_bomb:
        await m.reply("❌ No active game!")
        return
    active_bomb.pop(chat_id)
    await m.reply("❌ **Bomb game cancelled!**")

@safe_handler
@app.on_message(filters.command("passbomb"))
async def passbomb_cmd(_, m: Message):
    chat_id = m.chat.id
    if chat_id not in active_bomb:
        await m.reply("❌ No active bomb! `/bomb` to start!")
        return
    bomb = active_bomb[chat_id]
    if not bomb.get("started"):
        await m.reply("❌ Game not started yet! `/startbomb` karo!")
        return
    if bomb["holder"] != m.from_user.id:
        await m.reply(f"❌ Bomb is not with you! It's with **{bomb['name']}**!")
        return
    if not m.reply_to_message:
        await m.reply("❌ Reply to a player's message to pass!")
        return
    target = m.reply_to_message.from_user
    if target.id == m.from_user.id:
        await m.reply("❌ Can't pass to yourself!")
        return
    # Check if target is in players list
    if target.id not in bomb["players"]:
        player_names = ", ".join(bomb["players"].values())
        await m.reply(
            f"❌ **{target.first_name}** is not in this game!\n"
            f"👥 Players: {player_names}"
        )
        return
    bomb["holder"] = target.id
    bomb["name"] = target.first_name
    active_bomb[chat_id] = bomb
    await m.reply(
        f"💣 **Passed!**\n\n"
        f"**{get_user_name(m)}** → **{target.first_name}**\n"
        f"⚡ Pass it fast or BOOM! 💥"
    )

@safe_handler
@app.on_message(filters.command("duel"))
async def duel_cmd(_, m: Message):
    if m.chat.type.name not in ("GROUP", "SUPERGROUP"):
        await m.reply("❌ Use this in a group!")
        return
    chat_id = m.chat.id
    if chat_id in active_duel:
        await m.reply("⚔️ Duel already chal raha hai!")
        return
    if not m.reply_to_message:
        await m.reply(
            "⚔️ **Duel Challenge!**\n\n"
            "Kisi ke message pe reply karke `/duel` karo!"
        )
        return
    p1 = m.from_user
    p2 = m.reply_to_message.from_user
    if p2.is_bot or p2.id == p1.id:
        await m.reply("❌ Invalid opponent!")
        return
    active_duel[chat_id] = {
        "p1": p1.id, "p1name": p1.first_name, "hp1": 100,
        "p2": p2.id, "p2name": p2.first_name, "hp2": 100,
        "turn": p1.id
    }
    await m.reply(
        f"⚔️ **DUEL!**\n\n"
        f"👤 **{p1.first_name}** (❤️ 100 HP)\n"
        f"VS\n"
        f"👤 **{p2.first_name}** (❤️ 100 HP)\n\n"
        f"🗡 **{p1.first_name}** ka turn hai!\n"
        f"⚔️ `/attack` — Attack karo (10-30 damage)\n"
        f"🛡 `/defend` — Defend karo (next attack block)"
    )

duel_defending = {}  # {user_id: True}

@safe_handler
@app.on_message(filters.command("attack"))
async def attack_cmd(_, m: Message):
    chat_id = m.chat.id
    if chat_id not in active_duel:
        await m.reply("❌ Koi active duel nahi! `/duel @user` se challenge karo!")
        return
    duel = active_duel[chat_id]
    if m.from_user.id != duel["turn"]:
        other = duel["p1name"] if duel["turn"] == duel["p1"] else duel["p2name"]
        await m.reply(f"❌ Not your turn! **{other}** goes next!")
        return
    damage = random.randint(10, 35)
    attacker = get_user_name(m)
    if m.from_user.id == duel["p1"]:
        defender_id, defender_name = duel["p2"], duel["p2name"]
        hp_key = "hp2"
    else:
        defender_id, defender_name = duel["p1"], duel["p1name"]
        hp_key = "hp1"
    # Check if defending
    if duel_defending.get(defender_id):
        duel_defending.pop(defender_id)
        await m.reply(f"🛡 **{defender_name}** ne attack block kar diya! 0 damage!")
        duel["turn"] = defender_id
        return
    duel[hp_key] = max(0, duel[hp_key] - damage)
    hp1, hp2 = duel["hp1"], duel["hp2"]
    # Check win
    if duel[hp_key] <= 0:
        del active_duel[chat_id]
        db.ensure_user(m.from_user.id, get_user_name(m))
        db.add_xp(m.from_user.id, 100)
        await m.reply(
            f"⚔️ **{attacker}** hits **{defender_name}** for **{damage}** damage!\n\n"
            f"💀 **{defender_name}** haara!\n\n"
            f"🏆 **{attacker} WINS!** ✨ +100 XP!"
        )
        return
    duel["turn"] = defender_id
    active_duel[chat_id] = duel
    await m.reply(
        f"⚔️ **{attacker}** attacks **{defender_name}** — **{damage}** damage!\n\n"
        f"❤️ {duel['p1name']}: **{hp1}** HP\n"
        f"❤️ {duel['p2name']}: **{hp2}** HP\n\n"
        f"🗡 **{defender_name}** ka turn!\n"
        f"⚔️ `/attack` ya 🛡 `/defend`"
    )

@safe_handler
@app.on_message(filters.command("defend"))
async def defend_cmd(_, m: Message):
    chat_id = m.chat.id
    if chat_id not in active_duel:
        await m.reply("❌ Koi active duel nahi!")
        return
    duel = active_duel[chat_id]
    if m.from_user.id != duel["turn"]:
        await m.reply("❌ Tumhara turn nahi!")
        return
    duel_defending[m.from_user.id] = True
    if m.from_user.id == duel["p1"]:
        duel["turn"] = duel["p2"]
        other = duel["p2name"]
    else:
        duel["turn"] = duel["p1"]
        other = duel["p1name"]
    active_duel[chat_id] = duel
    await m.reply(
        f"🛡 **{get_user_name(m)}** defend mode mein hai!\n"
        f"Next attack block ho jayega!\n\n"
        f"⚔️ **{other}** ka turn!"
    )

# Wordle game
WORDLE_WORDS = [
    "MUSIC", "BEATS", "NOTES", "TUNES", "SONGS", "ALBUM", "LYRIC",
    "DANCE", "PIANO", "VOCAL", "GENRE", "BANDS", "DRUMS", "FLUTE",
    "SOUND", "AUDIO", "RADIO", "DISCO", "BLUES", "JAZZY", "CHART",
    "REMIX", "TRACK", "STAGE", "TEMPO", "PITCH", "CHORD", "SYNTH",
    "MANIA", "DREAM", "HEART", "FLAME", "NIGHT", "LIGHT", "SHINE",
    "STORM", "GHOST", "BRAVE", "GRACE", "POWER", "MAGIC", "SMILE",
    "LAUGH", "TEARS", "BLOOM", "SPARK", "BLAZE", "RIVER", "OCEAN",
    "VIVID", "LUNAR", "SOLAR", "PRISM", "BRISK", "CRISP", "SWIRL",
    "GLIDE", "FROST", "FLICK", "DRIFT", "CREST", "CLOUD", "PLAIN",
    "STEEP", "GRIND", "BLEND", "FLARE", "FLAIR", "GRAZE", "PLUCK",
    "SIREN", "BLISS", "FORTE", "SCOUT", "WALTZ", "EPOCH", "RIVET",
]

WORDLE_FUN_LINES = [
    "Lucky guess? Or pure skill? 😏",
    "Getting warmer... or colder? 👀",
    "You're on fire today! 🔥",
    "That was a bold move! 😤",
    "Big brain energy right there 🧠",
    "Not bad, not bad at all 😎",
    "Is this your final answer? 🤔",
    "The word is watching you 👁",
    "Almost there... or are you? 😈",
    "Legend behavior detected 👑",
    "Trust the process! 💪",
    "One step closer to glory ⚡",
]

def _get_wordle_hint(word, attempts):
    guessed_letters = set()
    for attempt in attempts:
        parts = attempt.split()
        if len(parts) > 1:
            for ch in parts[-1]:
                guessed_letters.add(ch)
    unrevealed = [i for i, ch in enumerate(word) if ch not in guessed_letters]
    if not unrevealed:
        return "💡 Hint: Position 1 is **" + word[0] + "**"
    pos = random.choice(unrevealed)
    return "💡 Hint: Position " + str(pos+1) + " is **" + word[pos] + "**"

@safe_handler
@app.on_message(filters.command("wordle"))
async def wordle_cmd(_, m: Message):
    user_id = m.from_user.id
    if user_id in active_wordle:
        del active_wordle[user_id]
    word = random.choice(WORDLE_WORDS)
    active_wordle[user_id] = {"word": word, "attempts": []}
    stats = db.get_wordle_stats(user_id)
    streak = stats.get("streak", 0)
    streak_text = ""
    if streak > 0:
        streak_text = "\n🔥 Your streak: **" + str(streak) + "** days"
    await m.reply(
        "🟩 **WORDLE!**\n\n"
        "Guess the 5-letter word!\n\n"
        "🟩 = Right letter, right spot\n"
        "🟨 = Right letter, wrong spot\n"
        "⬜ = Letter not in word\n\n"
        "**20 attempts** | Hints at attempt 11 & 18" + streak_text + "\n\n"
        "Use `/g WORD` — e.g. `/g MUSIC`"
    )

@safe_handler
@app.on_message(filters.command("dwordle"))
async def dwordle_cmd(_, m: Message):
    word = db.get_or_create_daily_wordle(WORDLE_WORDS)
    user_id = m.from_user.id
    if user_id in active_wordle and active_wordle[user_id].get("daily"):
        w = active_wordle[user_id]
        prev = "\n".join(w["attempts"]) if w["attempts"] else "No guesses yet"
        att = len(w["attempts"])
        await m.reply(
            "📅 **Daily Wordle** — game already active!\n\n" +
            prev + "\n\nAttempts: **" + str(att) + "/20**\n"
            "Use `/g WORD` to guess"
        )
        return
    active_wordle[user_id] = {"word": word, "attempts": [], "daily": True}
    import datetime as _dt
    today_str = _dt.date.today().strftime("%B %d")
    await m.reply(
        "📅 **Daily Wordle — " + today_str + "**\n\n"
        "Same word for everyone today! Compete with friends 🏆\n\n"
        "🟩 = Right letter, right spot\n"
        "🟨 = Right letter, wrong spot\n"
        "⬜ = Letter not in word\n\n"
        "**20 attempts** | Use `/g WORD` to guess"
    )

@safe_handler
@app.on_message(filters.command("lwordle"))
async def lwordle_cmd(_, m: Message):
    rows = db.get_wordle_leaderboard(10)
    if not rows:
        await m.reply("🏆 **Wordle Leaderboard**\n\nNo wins yet! Play with `/wordle`")
        return
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    text = "🏆 **Wordle Leaderboard**\n\n"
    for i, row in enumerate(rows):
        medal = medals[i] if i < len(medals) else str(i+1) + "."
        s = row.get("streak", 0)
        streak_part = " | 🔥" + str(s) if s > 1 else ""
        text += medal + " **" + str(row["name"]) + "** — " + str(row["wins"]) + " wins" + streak_part + "\n"
    await m.reply(text)

@safe_handler
@app.on_message(filters.command("g"))
async def wordle_guess(_, m: Message):
    user_id = m.from_user.id
    parts = m.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        if user_id in active_wordle:
            w = active_wordle[user_id]
            prev = "\n".join(w["attempts"]) if w["attempts"] else "No guesses yet"
            att = len(w["attempts"])
            dtype = "Daily " if w.get("daily") else ""
            await m.reply("🟩 **" + dtype + "Wordle Active:**\n\n" + prev + "\n\nAttempts: **" + str(att) + "/20**\n`/g WORD` to guess")
        else:
            await m.reply("❌ No active game! Use `/wordle` or `/dwordle`")
        return
    if user_id not in active_wordle:
        await m.reply("❌ Start a game first! Use `/wordle` or `/dwordle`")
        return
    guess = parts[1].strip().upper()
    if len(guess) != 5 or not guess.isalpha():
        await m.reply("❌ Must be a 5-letter English word! e.g. `/g MUSIC`")
        return
    w = active_wordle[user_id]
    word = w["word"]
    result = ""
    for i, ch in enumerate(guess):
        if ch == word[i]:
            result += "🟩"
        elif ch in word:
            result += "🟨"
        else:
            result += "⬜"
    attempt_line = result + " " + guess
    w["attempts"].append(attempt_line)
    active_wordle[user_id] = w
    attempts_used = len(w["attempts"])
    prev = "\n".join(w["attempts"])
    fun_line = random.choice(WORDLE_FUN_LINES)

    if guess == word:
        del active_wordle[user_id]
        db.ensure_user(user_id, get_user_name(m))
        db.record_wordle_win(user_id, get_user_name(m))
        stats = db.get_wordle_stats(user_id)
        if attempts_used <= 5:
            xp = 30
        elif attempts_used <= 10:
            xp = 25
        else:
            xp = 15
        db.add_xp(user_id, xp)
        streak_val = stats.get("streak", 1)
        streak_line = "\n🔥 Streak: **" + str(streak_val) + " days**!" if streak_val > 1 else ""
        await m.reply(
            "🎉 **Correct, " + get_user_name(m) + "!**\n\n" +
            prev + "\n\n" +
            "🟩 Word: **" + word + "**\n" +
            "Attempts: **" + str(attempts_used) + "/20** | ✨ +" + str(xp) + " XP" + streak_line + "\n\n" +
            "Play again: `/wordle` | Leaderboard: `/lwordle`"
        )
    elif attempts_used >= 20:
        del active_wordle[user_id]
        await m.reply(
            "💀 **Game Over!**\n\n" +
            prev + "\n\n" +
            "🔤 Word was: **" + word + "**\n" +
            "Try again: `/wordle`"
        )
    else:
        hint_text = ""
        if attempts_used == 11 or attempts_used == 18:
            hint_text = "\n\n" + _get_wordle_hint(word, w["attempts"])
        await m.reply(
            prev + "\n\n" +
            "Attempts: **" + str(attempts_used) + "/20** | " + fun_line + hint_text + "\n" +
            "`/g WORD` — Next guess!"
        )

# ===== PAGINATED MENU SYSTEM =====

MENU_PAGES = {
    "music": [
        [
            ("📥 /download", "Download songs"), ("🔍 /search", "Search songs"),
            ("📝 /lyrics", "Get lyrics"), ("ℹ️ /info", "Song info"),
            ("🎧 /quality", "Choose quality"), ("📦 /batch", "Batch download"),
        ],
        [
            ("🎛 /rlc", "Remix/Lofi/Cover"), ("🎵 /srec", "Similar & Recommend"),
            ("🎶 /duet", "Duets"), ("📅 /year", "Songs by year"),
            ("🤖 /ai_playlist", "AI Playlist"), ("📅 /daily", "Daily song"),
        ],
    ],
    "discover": [
        [
            ("🎭 /mood", "Mood songs"), ("🎲 /random", "Random song"),
            ("🌍 /trending", "Trending now"), ("🆕 /newreleases", "New releases"),
            ("🎸 /genre", "By genre"), ("🎭 /vibe", "Vibe check"),
        ],
        [
            ("🌐 /lang", "Songs by language"), ("🌍 /regional", "Regional languages"),
            ("🎤 /artist", "Artist info & songs"), ("💿 /album", "Album songs"),
            ("🏆 /topartist", "Top by artist"), ("💿 /discography", "Discography"),
        ],
        [
            ("🔤 /findlyrics", "Find by lyrics"), ("🎵 /playlist", "Playlist"),
        ],
    ],
    "games": [
        [
            ("🎯 /guesssong", "Guess the song"), ("🎮 /musicquiz", "Music quiz"),
            ("🎤 /artistquiz", "Artist quiz"), ("📅 /challenge", "Daily challenge"),
            ("🏆 /tournament", "Tournament"), ("⚖️ /compare", "Compare songs"),
        ],
        [
            ("👥 /groupquiz", "Group quiz"), ("⚔️ /songbattle", "Song battle"),
            ("📊 /votesong", "Vote song"), ("⭐ /rate", "Rate song"),
            ("🏆 /topsongs", "Top rated"),
        ],
    ],
    "wordle": [
        [
            ("🟩 /wordle", "New Wordle game"), ("📅 /dwordle", "Daily Wordle"),
            ("🏆 /lwordle", "Wordle leaderboard"),
        ],
    ],
    "fun": [
        [
            ("🎰 /slots", "Slot machine"), ("🎲 /dice", "Dice roll"),
            ("🔢 /guess", "Number guess"), ("💣 /bomb", "Bomb game"),
            ("⚔️ /duel", "Duel"), ("🟩 /wordle", "Wordle"),
        ],
    ],
    "chat": [
        [
            ("💬 /chat", "Start AI chat"), ("🗑 /clearchat", "Clear chat history"),
            ("💬 /quote", "Music quote"), ("🎵 /musicfact", "Music fact"),
            ("🥚 /easteregg", "Easter egg"), ("🔮 /secret", "Secret"),
        ],
    ],
    "profile": [
        [
            ("👤 /profile", "Your profile"), ("📊 /mystats", "Your stats"),
            ("🏅 /badges", "Badges"), ("🔥 /streak", "Streak"),
            ("🎁 /dailyreward", "Daily reward"), ("🏆 /leaderboard", "Leaderboard"),
        ],
        [
            ("⭐ /favorites", "Favorites"), ("💾 /save", "Save song"),
            ("🗑 /removefav", "Remove fav"), ("📜 /history", "History"),
            ("📋 /wishlist", "Add wishlist"), ("📋 /mywishlist", "My wishlist"),
        ],
        [
            ("🔔 /subscribe", "Subscribe"), ("🔕 /unsubscribe", "Unsubscribe"),
            ("🤝 /invite", "Invite friends"), ("📝 /note", "Add note"),
        ],
    ],
    "stats": [
        [
            ("📊 /stats", "Bot stats"), ("⏰ /uptime", "Uptime"),
            ("🏓 /ping", "Ping"), ("🎵 /songstats", "Song stats"),
            ("📊 /activestats", "Active users"), ("📊 /genrestats", "Genre stats"),
        ],
        [
            ("🏆 /gleaderboard", "Group leaderboard"), ("📊 /groupstats", "Group stats"),
            ("🥇 /topuser", "Top user"), ("🎵 /lastdownload", "Last download"),
            ("🎵 /musicmatch", "Music match"),
        ],
    ],
}

MENU_TITLES = {
    "music": "🎵 Music", "discover": "🌍 Discover",
    "games": "🎮 Games", "fun": "🕹 Fun Games",
    "chat": "💬 Chat & More", "profile": "👤 Profile",
    "stats": "📊 Stats",
}

def build_menu_keyboard(section, page):
    pages = MENU_PAGES[section]
    total = len(pages)
    page = max(1, min(page, total))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Back", callback_data=f"menu_{section}_{page-1}"))
    nav.append(InlineKeyboardButton("🏠 Home", callback_data="menu_home"))
    if page < total:
        nav.append(InlineKeyboardButton("➡️ Next", callback_data=f"menu_{section}_{page+1}"))
    return InlineKeyboardMarkup([nav])

def build_menu_text(section, page):
    pages = MENU_PAGES[section]
    total = len(pages)
    page = max(1, min(page, total))
    items = pages[page - 1]
    title = MENU_TITLES[section]
    
    text = f"**{title} ({page}/{total})**\n\n"
    for cmd, desc in items:
        text += f"{cmd} — {desc}\n"
    text += f"\n━━━━━━━━━━━━━━━\n🎧 Powered by BeatNova"
    return text

@app.on_callback_query(filters.regex(r"^menu_home$"))
async def menu_home(_, cb):
    bot_username_raw = BOT_USERNAME.replace("@", "")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 Music", callback_data="menu_music_1"),
         InlineKeyboardButton("🌍 Discover", callback_data="menu_discover_1")],
        [InlineKeyboardButton("🎮 Games", callback_data="menu_games_1"),
         InlineKeyboardButton("🕹 Fun Games", callback_data="menu_fun_1")],
        [InlineKeyboardButton("💬 Chat & More", callback_data="menu_chat_1"),
         InlineKeyboardButton("👤 Profile", callback_data="menu_profile_1")],
        [InlineKeyboardButton("📊 Stats", callback_data="menu_stats_1")],
        [InlineKeyboardButton("➕ Add BeatNova to Your Group", url=f"https://t.me/{bot_username_raw}?startgroup=true")],
    ])
    await cb.message.edit_text(
        "🎧 **BeatNova Menu**\n\n"
        "👇 Choose a category:\n"
        "━━━━━━━━━━━━━━━\n"
        "🎧 Powered by BeatNova",
        reply_markup=keyboard
    )
    await cb.answer()

@app.on_callback_query(filters.regex(r"^menu_(music|discover|games|fun|chat|profile|stats)_(\d+)$"))
async def menu_page(_, cb):
    parts = cb.data.split("_")
    section = parts[1]
    page = int(parts[2])
    text = build_menu_text(section, page)
    keyboard = build_menu_keyboard(section, page)
    await cb.message.edit_text(text, reply_markup=keyboard)
    await cb.answer()

async def main():
    await app.start()
    db.init_db()
    print(f"✅ {BOT_NAME} started!")
    asyncio.create_task(send_daily_songs())
    asyncio.create_task(send_micro_tips())
    asyncio.create_task(send_hook_challenge())
    await asyncio.Event().wait()

app.run(main())
