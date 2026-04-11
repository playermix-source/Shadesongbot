# apis.py - BeatNova Multi-API Music System (Upgraded)
# APIs: saavn.dev, JioSaavn fallback, iTunes, Deezer, LastFM
import requests
import re

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
    Smart best match — finds exact or closest song, avoids unwanted versions
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

    scored = []
    for song in results:
        name = song.get("name", "").lower().strip()
        artist = song.get("primaryArtists", song.get("artist", "")).lower()
        name_words = set(name.split())
        score = 0

        # 1. Exact name match = perfect score
        if name == query_clean:
            return song

        # 2. Word match score
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

        # 5. Bonus if name starts with query words
        first_word = query_clean.split()[0] if query_clean.split() else ""
        if name.startswith(first_word):
            score += 8

        # 6. Bonus if artist name matches query
        artist_words = set(artist.split(",")[0].strip().split())
        if artist_words & query_words:
            score += 5

        # 7. Shorter name = closer to original (penalize long additions)
        name_extra_len = len(name) - len(query_clean)
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
    """Get best quality song download URL"""
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
                s = _find_best_match([{
                    "name": x.get("name", ""),
                    "artist": ", ".join(a["name"] for a in x.get("artists", {}).get("primary", [])),
                    "primaryArtists": ", ".join(a["name"] for a in x.get("artists", {}).get("primary", [])),
                    "_raw": x
                } for x in results], query)
                if s:
                    raw = s.get("_raw") or next((x for x in results if x.get("name") == s.get("name")), results[0])
                    dl_urls = raw.get("downloadUrl", [])
                    dl_url = _get_best_download_url(dl_urls, quality, "url")
                    if dl_url:
                        artists = raw.get("artists", {}).get("primary", [])
                        artist_str = ", ".join(a["name"] for a in artists) if artists else "Unknown"
                        album_raw = raw.get("album", {})
                        album_str = album_raw.get("name", "Unknown") if isinstance(album_raw, dict) else str(album_raw or "Unknown")
                        print(f"[saavn.dev] ✅ {raw.get('name')} | {dl_url[:50]}")
                        return {
                            "source": "jiosaavn",
                            "name": raw.get("name", "Unknown"),
                            "artist": artist_str,
                            "album": album_str,
                            "year": str(raw.get("year", "Unknown")),
                            "duration": int(raw.get("duration", 0)),
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
                mapped = [{"name": x.get("name",""), "artist": x.get("primaryArtists",""), "primaryArtists": x.get("primaryArtists",""), "_raw": x} for x in results_old]
                s = _find_best_match(mapped, query)
                if s:
                    raw = s.get("_raw") or results_old[0]
                    dl_urls = raw.get("downloadUrl", [])
                    dl_url = _get_best_download_url(dl_urls, quality, "link")
                    if dl_url:
                        album_raw = raw.get("album", {})
                        album_str = album_raw.get("name", "Unknown") if isinstance(album_raw, dict) else str(album_raw or "Unknown")
                        print(f"[saavn_old] ✅ {raw.get('name')} | {dl_url[:50]}")
                        return {
                            "source": "jiosaavn",
                            "name": raw.get("name", "Unknown"),
                            "artist": raw.get("primaryArtists", "Unknown"),
                            "album": album_str,
                            "year": str(raw.get("year", "Unknown")),
                            "duration": int(raw.get("duration", 0)),
                            "language": raw.get("language", "hindi").capitalize(),
                            "download_url": dl_url,
                            "id": raw.get("id", ""),
                            "quality": f"{quality}kbps",
                        }
    except Exception as e:
        print(f"[saavn_old quality] {e}")

    print(f"[saavn_quality] Both APIs failed for: {query}")
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
    hindi_words = {"tum", "dil", "pyar", "ishq", "tera", "mera", "yaar", "aaj", "kal",
                   "raat", "din", "phir", "kuch", "main", "hum", "hai", "ho", "kar",
                   "mere", "teri", "mohabbat", "zindagi", "duniya", "woh", "aur",
                   "mujhe", "tujhe", "koi", "nahi", "bhi", "kya", "kyun", "ab",
                   "aa", "ja", "le", "de", "sun", "bol", "chal", "reh", "jaa"}
    q_words = set(query.lower().split())
    if q_words & hindi_words:
        return "hindi"
    return "international"

# ==================== UNIFIED SEARCH ====================

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
    else:
        results = _deezer_search(query, limit)
        if len(results) < 3:
            results = results + _itunes_search(query, limit)
        if len(results) < 5:
            saavn = _saavn_dev(query, 5) or _saavn_old(query, 5)
            results = results + saavn

    # Sort — best match first
    if results:
        best = _find_best_match(results, query)
        if best and best in results:
            results.remove(best)
            results.insert(0, best)

    return results[:limit]

def search_song_download(query, quality="320"):
    """Get best downloadable song — full quality"""
    # Always try JioSaavn first for full quality
    song = _saavn_quality(query, quality)
    if song:
        return song

    # Fallback: Deezer preview
    deezer = _deezer_search(query, 5)
    if deezer:
        best = _find_best_match(deezer, query)
        if best and best.get("download_url"):
            best["quality"] = "preview (30sec)"
            return best

    # Fallback: iTunes preview
    itunes = _itunes_search(query, 5)
    if itunes:
        best = _find_best_match(itunes, query)
        if best and best.get("download_url"):
            best["quality"] = "preview (30sec)"
            return best

    return None

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
