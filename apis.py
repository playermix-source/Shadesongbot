# apis.py - BeatNova Multi-API Music System (Upgraded)
# APIs: saavn.dev, JioSaavn fallback, iTunes, Deezer, LastFM, YouTube Music (yt-dlp fallback)
import requests
import re
import os
import tempfile

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BeatNovaBot/2.0)"}
TIMEOUT = 15

LASTFM_KEY = "c9b16bfc1f90c14d1e3b20a5d7c2fead"

# Words that indicate unwanted versions — penalize these in search results
PENALTY_WORDS = [
    "2.0", "3.0", "4.0", "v2", "v3", "v4", "ii", "iii", "iv",
    "remix", "remixed", "re-mix", "mashup", "mash-up", "bootleg",
    "reprise", "remastered", "remaster", "redux", "rework", "reworked",
    "version", "edit", "edited", "mix",
    "cover", "covered", "tribute", "recreated", "recreation",
    "extended", "radio", "acoustic", "unplugged",
    "instrumental", "karaoke", "lofi", "lo-fi", "slowed", "reverb",
    "ft", "feat", "featuring",
    "live", "concert", "session", "performance", "tour",
    "official", "lyric", "video", "audio",
    "soundtrack", "ost", "bgm",
]

# ==================== BEST MATCH ALGORITHM ====================

def _find_best_match(results, query):
    """
    Smart best match — finds exact or closest song, avoids unwanted versions.
    Handles artist name in query (e.g. "pal pal talwiinder" or "mujhe peene do darshan raval").
    """
    if not results:
        return None
    if len(results) == 1:
        return results[0]

    query_clean = query.lower().strip()
    # Remove common prefixes users add
    for prefix in ["download ", "song ", "full song ", "audio "]:
        query_clean = query_clean.replace(prefix, "")
    query_words = set(query_clean.split())

    # Build a list of all artist names from results for artist-word detection
    all_artist_words = set()
    for song in results:
        artist_str = song.get("primaryArtists", song.get("artist", "")).lower()
        for part in artist_str.split(","):
            for w in part.strip().split():
                if len(w) > 2:
                    all_artist_words.add(re.sub(r'[^a-z0-9]', '', w))

    # Detect which query words are likely artist names (present in some result's artist field)
    artist_query_words = set()
    for w in query_words:
        w_clean = re.sub(r'[^a-z0-9]', '', w)
        if w_clean in all_artist_words:
            artist_query_words.add(w)

    # Song title words = query minus detected artist words
    song_title_words = query_words - artist_query_words

    scored = []
    for song in results:
        name = song.get("name", "").lower().strip()
        artist = song.get("primaryArtists", song.get("artist", "")).lower()
        name_words = set(name.split())
        score = 0

        # 1. Exact name match (on song title portion)
        if name == query_clean or name == " ".join(song_title_words):
            score += 100

        # 2. Word match score — match against song title words if we detected artist words
        if artist_query_words:
            matched = song_title_words & name_words
        else:
            matched = query_words & name_words
        score += len(matched) * 10

        # 3. Penalize extra words not in query
        extra = name_words - query_words
        for word in extra:
            word_clean = re.sub(r'[^a-z0-9]', '', word)
            if word_clean in [re.sub(r'[^a-z0-9]', '', p) for p in PENALTY_WORDS]:
                score -= 20

        # 4. Penalize year in name if not in query
        if not re.search(r'\b(19|20)\d{2}\b', query_clean):
            if re.search(r'\b(19|20)\d{2}\b', name):
                score -= 10

        # 5. Bonus if name starts with first song-title query word
        title_first = list(song_title_words)[0] if song_title_words else (query_clean.split()[0] if query_clean.split() else "")
        if name.startswith(title_first):
            score += 8

        # 6. Strong bonus if artist name matches the artist portion of query
        artist_words_song = set(re.sub(r'[^a-z0-9 ]', '', artist.split(",")[0].strip()).split())
        if artist_query_words:
            # User typed artist name — give HIGH bonus for exact artist match
            artist_q_clean = set(re.sub(r'[^a-z0-9]', '', w) for w in artist_query_words)
            artist_s_clean = set(re.sub(r'[^a-z0-9]', '', w) for w in artist_words_song)
            if artist_q_clean & artist_s_clean:
                score += 30  # Strong artist match bonus
        else:
            if artist_words_song & query_words:
                score += 5

        # 7. Shorter name = closer to original (penalize long additions)
        ref_words = song_title_words if song_title_words else query_words
        name_extra_len = len(name) - len(" ".join(ref_words))
        if name_extra_len > 10:
            score -= min(name_extra_len // 5, 10)

        scored.append((score, song))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]

def _get_best_download_url(dl_urls, quality="320", key="url"):
    """Pick best quality URL from downloadUrl list"""
    if not dl_urls:
        return None
    q_target = f"{quality}kbps"
    # Try exact quality match first
    for item in reversed(dl_urls):
        if isinstance(item, dict):
            item_q = item.get("quality", "")
            item_url = item.get(key) or item.get("url") or item.get("link")
            if item_q == q_target and item_url:
                return item_url
    # Fallback: highest quality
    for item in reversed(dl_urls):
        if isinstance(item, dict):
            url = item.get(key) or item.get("url") or item.get("link")
            if url:
                return url
    return None

# ==================== JIOSAAVN APIs ====================

def _saavn_dev(query, limit=10):
    """saavn.dev — primary JioSaavn API"""
    try:
        r = requests.get(
            "https://saavn.dev/api/search/songs",
            params={"query": query, "limit": limit, "page": 1},
            headers=HEADERS, timeout=TIMEOUT
        )
        if r.status_code != 200:
            return []
        results = r.json().get("data", {}).get("results", [])
        out = []
        for s in results:
            dl_urls = s.get("downloadUrl", [])
            dl_url = _get_best_download_url(dl_urls, "320", "url")
            if not dl_url:
                continue
            artists = s.get("artists", {}).get("primary", [])
            artist_str = ", ".join(a["name"] for a in artists) if artists else "Unknown"
            album_raw = s.get("album", {})
            album_str = album_raw.get("name", "Unknown") if isinstance(album_raw, dict) else str(album_raw or "Unknown")
            out.append({
                "source": "jiosaavn",
                "name": s.get("name", "Unknown"),
                "artist": artist_str,
                "album": album_str,
                "year": str(s.get("year", "Unknown")),
                "duration": int(s.get("duration", 0)),
                "language": s.get("language", "hindi").capitalize(),
                "download_url": dl_url,
                "id": s.get("id", ""),
                "quality": "320kbps",
            })
        return out
    except Exception as e:
        print(f"[saavn.dev] {e}")
        return []

def _saavn_old(query, limit=10):
    """jiosaavn-api-privatecvc2 — fallback"""
    try:
        r = requests.get(
            "https://jiosaavn-api-privatecvc2.vercel.app/search/songs",
            params={"query": query, "page": 1, "limit": limit},
            headers=HEADERS, timeout=TIMEOUT
        )
        if r.status_code != 200:
            return []
        results = r.json()["data"]["results"]
        out = []
        for s in results:
            dl_urls = s.get("downloadUrl", [])
            dl_url = _get_best_download_url(dl_urls, "320", "link")
            if not dl_url:
                continue
            album_raw = s.get("album", {})
            album_str = album_raw.get("name", "Unknown") if isinstance(album_raw, dict) else str(album_raw or "Unknown")
            out.append({
                "source": "jiosaavn",
                "name": s.get("name", "Unknown"),
                "artist": s.get("primaryArtists", "Unknown"),
                "album": album_str,
                "year": str(s.get("year", "Unknown")),
                "duration": int(s.get("duration", 0)),
                "language": s.get("language", "hindi").capitalize(),
                "download_url": dl_url,
                "id": s.get("id", ""),
                "quality": "320kbps",
            })
        return out
    except Exception as e:
        print(f"[saavn_old] {e}")
        return []

def _saavn_quality(query, quality="320", limit=10):
    """Get best quality song download URL — skips short clips (<90s)"""
    # Try saavn.dev first
    try:
        r = requests.get(
            "https://saavn.dev/api/search/songs",
            params={"query": query, "limit": limit},
            headers=HEADERS, timeout=TIMEOUT
        )
        if r.status_code == 200:
            results = r.json().get("data", {}).get("results", [])
            if results:
                mapped = [{
                    "name": x.get("name", ""),
                    "artist": ", ".join(a["name"] for a in x.get("artists", {}).get("primary", [])),
                    "primaryArtists": ", ".join(a["name"] for a in x.get("artists", {}).get("primary", [])),
                    "duration": int(x.get("duration", 0)),
                    "_raw": x
                } for x in results]

                # Filter short clips BEFORE scoring — don't pick a 57s verse over a 3min full song
                full_songs = [m for m in mapped if int(m.get("duration", 0)) >= 90]
                pool = full_songs if full_songs else mapped

                s = _find_best_match(pool, query)
                if s:
                    raw = s.get("_raw") or next((x for x in results if x.get("name") == s.get("name")), results[0])
                    dur = int(raw.get("duration", 0))
                    if dur > 0 and dur < 90:
                        print(f"[saavn.dev] ⚠️ Only short clip ({dur}s) found: {raw.get('name')} — will try yt-dlp")
                        return None
                    dl_urls = raw.get("downloadUrl", [])
                    dl_url = _get_best_download_url(dl_urls, quality, "url")
                    if dl_url:
                        artists = raw.get("artists", {}).get("primary", [])
                        artist_str = ", ".join(a["name"] for a in artists) if artists else "Unknown"
                        album_raw = raw.get("album", {})
                        album_str = album_raw.get("name", "Unknown") if isinstance(album_raw, dict) else str(album_raw or "Unknown")
                        print(f"[saavn.dev] ✅ {raw.get('name')} ({dur}s)")
                        return {
                            "source": "jiosaavn",
                            "name": raw.get("name", "Unknown"),
                            "artist": artist_str,
                            "album": album_str,
                            "year": str(raw.get("year", "Unknown")),
                            "duration": dur,
                            "language": raw.get("language", "hindi").capitalize(),
                            "download_url": dl_url,
                            "id": raw.get("id", ""),
                            "quality": f"{quality}kbps",
                        }
    except Exception as e:
        print(f"[saavn.dev quality] {e}")

    # Fallback: old API
    try:
        r2 = requests.get(
            "https://jiosaavn-api-privatecvc2.vercel.app/search/songs",
            params={"query": query, "page": 1, "limit": limit},
            headers=HEADERS, timeout=TIMEOUT
        )
        if r2.status_code == 200:
            results_old = r2.json()["data"]["results"]
            if results_old:
                mapped = [{
                    "name": x.get("name", ""),
                    "artist": x.get("primaryArtists", ""),
                    "primaryArtists": x.get("primaryArtists", ""),
                    "duration": int(x.get("duration", 0)),
                    "_raw": x
                } for x in results_old]

                full_songs = [m for m in mapped if int(m.get("duration", 0)) >= 90]
                pool = full_songs if full_songs else mapped

                s = _find_best_match(pool, query)
                if s:
                    raw = s.get("_raw") or results_old[0]
                    dur = int(raw.get("duration", 0))
                    if dur > 0 and dur < 90:
                        print(f"[saavn_old] ⚠️ Only short clip ({dur}s): {raw.get('name')} — will try yt-dlp")
                        return None
                    dl_urls = raw.get("downloadUrl", [])
                    dl_url = _get_best_download_url(dl_urls, quality, "link")
                    if dl_url:
                        album_raw = raw.get("album", {})
                        album_str = album_raw.get("name", "Unknown") if isinstance(album_raw, dict) else str(album_raw or "Unknown")
                        print(f"[saavn_old] ✅ {raw.get('name')} ({dur}s)")
                        return {
                            "source": "jiosaavn",
                            "name": raw.get("name", "Unknown"),
                            "artist": raw.get("primaryArtists", "Unknown"),
                            "album": album_str,
                            "year": str(raw.get("year", "Unknown")),
                            "duration": dur,
                            "language": raw.get("language", "hindi").capitalize(),
                            "download_url": dl_url,
                            "id": raw.get("id", ""),
                            "quality": f"{quality}kbps",
                        }
    except Exception as e:
        print(f"[saavn_old quality] {e}")

    print(f"[saavn_quality] No full song found for: {query} — yt-dlp will handle")
    return None

# ==================== DEEZER ====================

def _deezer_search(query, limit=10):
    """Deezer — free, no auth, all languages"""
    try:
        r = requests.get(
            "https://api.deezer.com/search",
            params={"q": query, "limit": limit},
            headers=HEADERS, timeout=TIMEOUT
        )
        if r.status_code != 200:
            return []
        results = r.json().get("data", [])
        out = []
        for s in results:
            preview = s.get("preview", "")
            out.append({
                "source": "deezer",
                "name": s.get("title", "Unknown"),
                "artist": s.get("artist", {}).get("name", "Unknown"),
                "album": s.get("album", {}).get("title", "Unknown"),
                "year": "Unknown",
                "duration": int(s.get("duration", 0)),
                "language": "Unknown",
                "download_url": preview,
                "id": str(s.get("id", "")),
                "quality": "preview",
            })
        return out
    except Exception as e:
        print(f"[deezer] {e}")
        return []

# ==================== ITUNES ====================

def _itunes_search(query, limit=10, country="IN"):
    """iTunes — completely free, official, all languages"""
    try:
        r = requests.get(
            "https://itunes.apple.com/search",
            params={"term": query, "media": "music", "entity": "song", "limit": limit, "country": country},
            headers=HEADERS, timeout=TIMEOUT
        )
        if r.status_code != 200:
            return []
        results = r.json().get("results", [])
        out = []
        for s in results:
            preview = s.get("previewUrl", "")
            duration_ms = s.get("trackTimeMillis", 0)
            out.append({
                "source": "itunes",
                "name": s.get("trackName", "Unknown"),
                "artist": s.get("artistName", "Unknown"),
                "album": s.get("collectionName", "Unknown"),
                "year": s.get("releaseDate", "")[:4] if s.get("releaseDate") else "Unknown",
                "duration": duration_ms // 1000,
                "language": s.get("primaryGenreName", "Unknown"),
                "download_url": preview,
                "id": str(s.get("trackId", "")),
                "quality": "preview",
                "genre": s.get("primaryGenreName", ""),
            })
        return out
    except Exception as e:
        print(f"[itunes] {e}")
        return []

# ==================== LASTFM ====================

def _lastfm_request(params):
    """Make LastFM API request"""
    try:
        params.update({"api_key": LASTFM_KEY, "format": "json"})
        r = requests.get("https://ws.audioscrobbler.com/2.0/", params=params, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[lastfm] {e}")
    return {}

def _lastfm_similar(artist, track, limit=10):
    data = _lastfm_request({"method": "track.getSimilar", "artist": artist, "track": track, "limit": limit})
    tracks = data.get("similartracks", {}).get("track", [])
    return [{"name": t["name"], "artist": t["artist"]["name"]} for t in tracks]

def _lastfm_artist_info(artist):
    data = _lastfm_request({"method": "artist.getInfo", "artist": artist})
    a = data.get("artist", {})
    if not a:
        return {}
    return {
        "name": a.get("name", artist),
        "listeners": a.get("stats", {}).get("listeners", "Unknown"),
        "playcount": a.get("stats", {}).get("playcount", "Unknown"),
        "bio": a.get("bio", {}).get("summary", "").split("<a")[0].strip()[:300],
        "similar": [x["name"] for x in a.get("similar", {}).get("artist", [])[:5]],
        "tags": [t["name"] for t in a.get("tags", {}).get("tag", [])[:5]],
    }

def _lastfm_top_tracks(artist, limit=10):
    data = _lastfm_request({"method": "artist.getTopTracks", "artist": artist, "limit": limit})
    tracks = data.get("toptracks", {}).get("track", [])
    return [{"name": t["name"], "playcount": t.get("playcount", 0)} for t in tracks]

def _lastfm_trending(country="india", limit=10):
    data = _lastfm_request({"method": "geo.getTopTracks", "country": country, "limit": limit})
    tracks = data.get("tracks", {}).get("track", [])
    return [{"name": t["name"], "artist": t["artist"]["name"]} for t in tracks]

def _lastfm_similar_artists(artist, limit=8):
    data = _lastfm_request({"method": "artist.getSimilar", "artist": artist, "limit": limit})
    artists = data.get("similarartists", {}).get("artist", [])
    return [a["name"] for a in artists]

# ==================== LANGUAGE DETECTION ====================

def detect_language(query):
    """Detect if query is Hindi/Indian or English/International"""
    hindi_chars = set("अआइईउऊएऐओऔकखगघचछजझटठडढणतथदधनपफबभमयरलवशषसह")
    if any(c in hindi_chars for c in query):
        return "hindi"
    hindi_words = {
        # Common Hindi words
        "tum", "dil", "pyar", "ishq", "tera", "mera", "yaar", "aaj", "kal",
        "raat", "din", "phir", "kuch", "main", "hum", "hai", "ho", "kar",
        "mere", "teri", "mohabbat", "zindagi", "duniya", "woh", "aur",
        "mujhe", "tujhe", "koi", "nahi", "bhi", "kya", "kyun", "ab",
        "aa", "ja", "le", "de", "sun", "bol", "chal", "reh", "jaa",
        # More common Hindi song words
        "pal", "naina", "sajna", "sajana", "saath", "mann", "dard", "yaad",
        "judaa", "wafa", "bewafa", "intezaar", "tanha", "akela", "dono",
        "piya", "sajan", "mehboob", "jaana", "jaane", "aana", "aane",
        "peene", "pine", "pee", "shraab", "daaru", "nashaa",
        "chain", "sukoon", "khushi", "gham", "aansu", "hasna", "rona",
        "zara", "thoda", "bahut", "bohot", "kaafi", "bilkul",
        "ek", "do", "teen", "char", "paanch",
        "jaan", "dost", "bhai", "yaar", "saathi",
        "dheere", "धीरे", "nazar", "aankhein", "aankhon", "baahon",
        "o", "oh", "oo", "re", "ri",
        # Common Bollywood/Punjabi song title words
        "lambiyan", "raataan", "kesariya", "shayad", "hawayein",
        "channa", "tujhse", "tumse", "humse", "unse",
        "lag", "lage", "lagi", "lagta", "lagti",
        "chhod", "chhodu", "chodna",
        "milna", "milenge", "mile", "milo",
        "baat", "baatein", "bolo", "batao",
        "hua", "hui", "hue", "hona", "hogi", "hoga",
        # Popular artist names that are Indian
        "arijit", "atif", "jubin", "darshan", "armaan", "shreya",
        "talwiinder", "talwinder", "b praak", "praak", "Jordan",
        "imran", "rahat", "sonu", "kumar", "udit", "lata", "asha",
        "kishore", "rafi", "mukesh", "hemant", "manna",
        "badshah", "honey", "diljit", "ap dhillon", "karan",
        "neha", "guru", "harshdeep", "sunidhi", "kavita",
    }
    q_words = set(query.lower().split())
    if q_words & hindi_words:
        return "hindi"
    # Also check if any word is a substring of known Hindi patterns
    q_lower = query.lower()
    hindi_substrings = ["peene", "pilao", "pila", "naino", "naina", "sajna", "dilbar"]
    if any(s in q_lower for s in hindi_substrings):
        return "hindi"
    return "international"

# ==================== UNIFIED SEARCH ====================

def _score_all(results, query):
    """Score and sort all results by best match — used for options list display"""
    if not results:
        return results
    query_clean = query.lower().strip()
    for prefix in ["download ", "song ", "full song ", "audio "]:
        query_clean = query_clean.replace(prefix, "")
    query_words = set(query_clean.split())

    # Detect artist words
    all_artist_words = set()
    for song in results:
        artist_str = song.get("primaryArtists", song.get("artist", "")).lower()
        for part in artist_str.split(","):
            for w in part.strip().split():
                if len(w) > 2:
                    all_artist_words.add(re.sub(r'[^a-z0-9]', '', w))

    artist_query_words = set()
    for w in query_words:
        w_clean = re.sub(r'[^a-z0-9]', '', w)
        if w_clean in all_artist_words:
            artist_query_words.add(w)

    song_title_words = query_words - artist_query_words

    scored = []
    for song in results:
        name = song.get("name", "").lower().strip()
        artist = song.get("primaryArtists", song.get("artist", "")).lower()
        name_words = set(name.split())
        score = 0

        # Exact name match
        if name == query_clean or (song_title_words and name == " ".join(sorted(song_title_words))):
            score += 100

        # Word match
        ref_words = song_title_words if artist_query_words else query_words
        matched = ref_words & name_words
        score += len(matched) * 10

        # Penalize penalty words
        extra = name_words - query_words
        for word in extra:
            word_clean = re.sub(r'[^a-z0-9]', '', word)
            if word_clean in [re.sub(r'[^a-z0-9]', '', p) for p in PENALTY_WORDS]:
                score -= 20

        # Extra heavy penalty for "2.0", "3.0" etc version numbers
        if re.search(r'\b\d+\.\d+\b', name) and not re.search(r'\b\d+\.\d+\b', query_clean):
            score -= 40

        # Penalize year in name
        if not re.search(r'\b(19|20)\d{2}\b', query_clean):
            if re.search(r'\b(19|20)\d{2}\b', name):
                score -= 10

        # Artist match bonus
        artist_words_song = set(re.sub(r'[^a-z0-9 ]', '', artist.split(",")[0].strip()).split())
        if artist_query_words:
            artist_q_clean = set(re.sub(r'[^a-z0-9]', '', w) for w in artist_query_words)
            artist_s_clean = set(re.sub(r'[^a-z0-9]', '', w) for w in artist_words_song)
            if artist_q_clean & artist_s_clean:
                score += 30
        else:
            if artist_words_song & query_words:
                score += 5

        # Shorter name bonus
        name_extra_len = len(name) - len(" ".join(ref_words))
        if name_extra_len > 10:
            score -= min(name_extra_len // 5, 10)

        # Prefer longer duration (avoid clips) — songs under 90s get penalized
        duration = int(song.get("duration", 0))
        if 0 < duration < 90:
            score -= 50
        elif duration > 150:
            score += 3

        scored.append((score, song))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored]


def search_songs(query, limit=10):
    """Smart multi-API search with best match sorting"""
    lang = detect_language(query)
    results = []

    if lang == "hindi":
        results = _saavn_dev(query, limit)
        if not results:
            results = _saavn_old(query, limit)
        if not results:
            results = _deezer_search(query, limit)
        if not results:
            print(f"[search_songs] All APIs failed, trying yt-dlp for: {query}")
            results = _ytdlp_search_multiple(query, limit)
    else:
        # For international, also try JioSaavn — many Indian songs have English titles
        saavn = _saavn_dev(query, limit) or _saavn_old(query, limit)
        deezer = _deezer_search(query, limit)
        itunes = _itunes_search(query, limit) if len(deezer) < 3 else []
        results = saavn + deezer + itunes

        # Deduplicate by name
        seen, deduped = set(), []
        for r in results:
            key = r.get("name", "").lower().strip()
            if key and key not in seen:
                seen.add(key)
                deduped.append(r)
        results = deduped

        if len(results) < 3:
            print(f"[search_songs] Too few results, adding yt-dlp for: {query}")
            results = results + _ytdlp_search_multiple(query, limit)

    # Sort ALL results by score — best first
    results = _score_all(results, query)

    return results[:limit]

def search_song_download(query, quality="320"):
    """
    Get best downloadable song.
    Flow: JioSaavn (320kbps full) → yt-dlp YouTube Music (if JioSaavn short/missing) → preview fallback
    """
    # 1. Try JioSaavn — fast, 320kbps
    song = _saavn_quality(query, quality)
    saavn_duration = int(song.get("duration", 0)) if song else 0

    if song and saavn_duration >= 90:
        print(f"[saavn] ✅ {song.get('name')} ({saavn_duration}s)")
        return song

    # JioSaavn returned a clip (<90s) or nothing — try yt-dlp
    if song:
        print(f"[saavn] ⚠️ Short clip {saavn_duration}s for '{query}' — trying yt-dlp")
    else:
        print(f"[saavn] ❌ Not found '{query}' — trying yt-dlp")

    # 2. yt-dlp: search YouTube Music, download to temp file directly
    yt_song = _ytdlp_download(query)
    if yt_song:
        print(f"[yt-dlp] ✅ {yt_song.get('name')} ({yt_song.get('duration')}s)")
        return yt_song

    # 3. JioSaavn short clip better than nothing
    if song:
        print(f"[saavn] ⚠️ Returning short clip as last resort")
        return song

    # 4. Deezer/iTunes 30sec preview
    for results in [_deezer_search(query, 5), _itunes_search(query, 5)]:
        if results:
            best = _find_best_match(results, query)
            if best and best.get("download_url"):
                best["quality"] = "preview (30sec)"
                return best

    return None


# ==================== YOUTUBE MUSIC via yt-dlp ====================

def _ytdlp_download(query):
    """
    Search YouTube Music and directly download audio to a temp file.
    Returns a song dict with '_local_path' pointing to the downloaded file.
    """
    try:
        import yt_dlp
    except ImportError:
        print("[yt-dlp] Not installed — add yt-dlp to requirements.txt")
        return None

    import os, tempfile

    def _try_download(search_q):
        try:
            tmp_dir = "/tmp/beatnova_dl"
            os.makedirs(tmp_dir, exist_ok=True)
            safe = "".join(c for c in search_q if c.isalnum() or c in " -_")[:40].strip()
            out_template = os.path.join(tmp_dir, f"{safe}.%(ext)s")
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "format": "bestaudio/best",
                "outtmpl": out_template,
                "noplaylist": True,
                "match_filter": lambda info, **kw: None if (info.get("duration") or 0) >= 60 else "too short",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytmsearch1:{search_q}", download=True)
            if not info:
                return None
            entry = info["entries"][0] if "entries" in info and info["entries"] else info
            duration = int(entry.get("duration") or 0)
            if duration < 60:
                print(f"[yt-dlp] Too short ({duration}s) for: {search_q}")
                return None
            title    = entry.get("title", search_q)
            artist   = (entry.get("artist") or entry.get("creator") or entry.get("uploader", "Unknown"))
            album    = entry.get("album") or title
            year     = str(entry.get("release_year") or (entry.get("upload_date", "") or "")[:4] or "Unknown")
            video_id = entry.get("id", "")
            local_path = os.path.join(tmp_dir, f"{safe}.mp3")
            if not os.path.exists(local_path):
                for ext in ["mp3", "m4a", "webm", "opus", "ogg"]:
                    candidate = os.path.join(tmp_dir, f"{safe}.{ext}")
                    if os.path.exists(candidate):
                        local_path = candidate
                        break
            if not os.path.exists(local_path):
                print(f"[yt-dlp] File not found after download for: {search_q}")
                return None
            if os.path.getsize(local_path) < 10000:
                os.remove(local_path)
                return None
            return {
                "source": "youtube",
                "name": title,
                "artist": artist,
                "primaryArtists": artist,
                "album": album,
                "year": year,
                "duration": duration,
                "language": "Unknown",
                "download_url": local_path,
                "_local_path": local_path,
                "id": video_id,
                "quality": "192kbps",
            }
        except Exception as e:
            print(f"[yt-dlp] Error for '{search_q}': {e}")
            return None

    # Try original query first
    result = _try_download(query)
    if result:
        print(f"[yt-dlp] ✅ {result.get('name')} ({result.get('duration')}s)")
        return result

    # If query has artist words, try without them — e.g. "pal pal talwiinder" → "pal pal"
    words = query.strip().split()
    if len(words) > 2:
        shorter = " ".join(words[:2])
        print(f"[yt-dlp] Retrying with shorter query: {shorter}")
        result2 = _try_download(shorter)
        if result2 and int(result2.get("duration", 0)) >= 90:
            print(f"[yt-dlp] ✅ (shorter) {result2.get('name')} ({result2.get('duration')}s)")
            return result2

    return None


def _ytdlp_search_multiple(query, limit=6):
    """
    Search YouTube Music, return multiple results for options list.
    Does NOT download — just metadata. Download happens on user's pick.
    """
    try:
        import yt_dlp
    except ImportError:
        return []

    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "noplaylist": True,
        }
        search_query = f"ytmsearch{limit}:{query}"
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_query, download=False)

        entries = info.get("entries", []) if info else []
        out = []
        for e in entries:
            if not e:
                continue
            dur = int(e.get("duration") or 0)
            if 0 < dur < 55:   # skip obvious clips
                continue
            artist = e.get("artist") or e.get("uploader", "Unknown")
            out.append({
                "source": "youtube",
                "name": e.get("title", "Unknown"),
                "artist": artist,
                "primaryArtists": artist,
                "album": e.get("album", "Unknown"),
                "year": str(e.get("release_year") or "Unknown"),
                "duration": dur,
                "language": "Unknown",
                "download_url": "",  # fetched when user picks
                "id": e.get("id", ""),
                "quality": "192kbps",
            })
        return out

    except Exception as e:
        print(f"[yt-dlp multi] {e}")
        return []

def get_similar_tracks(artist, track, query_fallback=""):
    """Get similar tracks via LastFM"""
    similar = _lastfm_similar(artist, track, 10)
    if similar:
        return similar
    results = search_songs(f"{artist} songs", 8)
    return [{"name": r["name"], "artist": r["artist"]} for r in results]

def get_trending(country="india"):
    """Get trending tracks for a country"""
    tracks = _lastfm_trending(country, 10)
    if tracks:
        return tracks
    query = "trending hindi bollywood 2025" if country.lower() in ["india", "hindi"] else f"trending {country} 2025"
    results = search_songs(query, 10)
    return [{"name": r["name"], "artist": r["artist"]} for r in results]

def get_artist_info(artist_name):
    return _lastfm_artist_info(artist_name)

def get_artist_top_tracks(artist_name, limit=10):
    tracks = _lastfm_top_tracks(artist_name, limit)
    if tracks:
        return [{"name": t["name"], "artist": artist_name, "playcount": t["playcount"]} for t in tracks]
    results = search_songs(f"best of {artist_name}", limit)
    return [{"name": r["name"], "artist": r["artist"], "playcount": 0} for r in results]

def get_similar_artists(artist_name):
    similar = _lastfm_similar_artists(artist_name, 8)
    if similar:
        return similar
    results = search_songs(f"artists like {artist_name}", 8)
    seen, artists = set(), []
    for r in results:
        a = r["artist"].split(",")[0].strip()
        if a not in seen and a.lower() != artist_name.lower():
            seen.add(a)
            artists.append(a)
    return artists[:6]

def search_by_language(language, limit=10):
    """Search songs by specific language"""
    lang_queries = {
        # Indian languages
        "hindi": "hindi popular songs 2024",
        "punjabi": "punjabi top hits 2024",
        "tamil": "tamil top songs 2024",
        "telugu": "telugu hits 2024",
        "marathi": "marathi songs popular",
        "bengali": "bengali songs popular",
        "gujarati": "gujarati songs popular",
        "bhojpuri": "bhojpuri songs hits",
        "kannada": "kannada songs popular",
        "malayalam": "malayalam songs hits",
        "rajasthani": "rajasthani folk songs",
        "odia": "odia songs popular",
        "assamese": "assamese songs popular",
        "urdu": "urdu ghazal songs",
        "nepali": "nepali songs popular",
        # International
        "english": "top english hits 2024",
        "spanish": "top spanish songs 2024",
        "french": "top french songs 2024",
        "korean": "kpop hits 2024",
        "japanese": "jpop anime songs 2024",
        "arabic": "arabic songs popular",
        "portuguese": "portuguese brazilian songs",
        "italian": "italian songs popular",
        "german": "german songs popular",
        "turkish": "turkish songs popular",
        "russian": "russian songs popular",
        "persian": "persian iranian songs",
        "sinhala": "sinhala songs popular",
        "thai": "thai songs popular",
        "indonesian": "indonesian songs popular",
        "malay": "malay songs popular",
    }
    query = lang_queries.get(language.lower(), f"{language} songs popular")
    return search_songs(query, limit)

def search_genre(genre, limit=10):
    """Search by music genre"""
    genre_queries = {
        "rock": "rock songs hits",
        "pop": "pop hits 2024",
        "jazz": "jazz music classic",
        "classical": "classical music instrumental",
        "rap": "rap hip hop hits",
        "indie": "indie songs 2024",
        "sufi": "sufi songs qawwali",
        "folk": "folk music traditional",
        "electronic": "electronic edm music",
        "blues": "blues music classic",
        "reggae": "reggae songs popular",
        "country": "country music hits",
        "metal": "metal rock songs",
        "rnb": "r&b soul music hits",
        "lofi": "lofi hip hop chill",
        "kpop": "kpop bts blackpink hits",
        "ghazal": "ghazal urdu hindi songs",
        "devotional": "bhajan aarti devotional songs",
        "qawwali": "qawwali nusrat fateh songs",
        "classical_indian": "raag classical indian music",
        "jazz": "jazz music",
        "trap": "trap music hits",
        "drill": "drill music",
        "afrobeats": "afrobeats popular",
        "latin": "latin music hits",
        "disco": "disco songs classic",
        "funk": "funk music hits",
    }
    query = genre_queries.get(genre.lower(), f"{genre} songs")
    return search_songs(query, limit)
