"""Channel Opportunity Finder - YouTube first (free Data API, quota-aware).
Quota per niche: search=100 + videos=1 + channels=1 + up to 3 comment calls = ~105 units.
Free quota is 10,000/day, so 6 niches x 4 runs/day = ~2,500 units."""
import env  # loads settings.env
import os, re, time, math, sqlite3, statistics, requests, datetime as dt

DB = os.getenv("RADAR_DB", "radar.db")
KEY = os.getenv("YT_API_KEY")
API = "https://www.googleapis.com/youtube/v3"
PER_RUN = int(os.getenv("NICHES_PER_RUN", 6))
ALERT_SCORE = float(os.getenv("CHANNEL_ALERT_SCORE", 0.5))
NICHES = [  # topics a channel could be built around; edit freely
    "sleep tips", "hair repair routine", "portable power station review", "pet health tips",
    "screen free activities for kids", "small apartment organization", "budget home office setup",
    "camping gear for beginners", "AI tools tutorial", "personal finance for beginners",
    "beginner woodworking", "home recovery and stretching", "used car buying tips", "cheap healthy meal prep",
]
REQ_RE = re.compile(r"(make|do) a (video|part|tutorial)|part 2|please (cover|do|make)|"
                    r"nobody (covers|talks)|can you (cover|explain|review)|more (videos|content) (on|like)", re.I)

def yt(endpoint, **p):
    r = requests.get(f"{API}/{endpoint}", params={**p, "key": KEY}, timeout=15)
    r.raise_for_status()
    return r.json()

def seconds(d):
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    h, mi, sec = (int(x or 0) for x in m.groups()) if m else (0, 0, 0)
    return h * 3600 + mi * 60 + sec

def monetization(niche):
    """Rough guess only (real ad rates need paid data)."""
    n = niche.lower()
    rate = "higher" if re.search(r"financ|invest|money|ai |software|business|tax|insurance|real estate", n) else \
           "medium" if re.search(r"health|fitness|tech|review|home|diy|car|meal|gear|tools|pet", n) else "lower"
    face = "faceless-friendly" if re.search(r"tips|tutorial|review|explained|facts|guide|how to|list|routine", n) else "may need a face/voice"
    return f"{rate} ad rate (guess), {face}"

def fetch(niche):
    """Top viewed videos of the last 45 days for a niche, joined with channel stats."""
    since = (dt.datetime.utcnow() - dt.timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = yt("search", part="snippet", q=niche, type="video", order="viewCount",
               publishedAfter=since, maxResults=50, relevanceLanguage=os.getenv("YT_LANG", "en"))["items"]
    ids = [i["id"]["videoId"] for i in items]
    if not ids:
        return []
    vids = yt("videos", part="statistics,snippet,contentDetails", id=",".join(ids))["items"]
    chans = {c["id"]: c for c in yt("channels", part="statistics,snippet",
             id=",".join({v["snippet"]["channelId"] for v in vids}))["items"]}
    rows = []
    for v in vids:
        c = chans.get(v["snippet"]["channelId"])
        if not c or c["statistics"].get("hiddenSubscriberCount"):
            continue
        rows.append(dict(video_id=v["id"], title=v["snippet"]["title"], channel_id=c["id"],
                         channel=c["snippet"]["title"], subs=int(c["statistics"].get("subscriberCount", 0)),
                         views=int(v["statistics"].get("viewCount", 0)), chan_created=c["snippet"]["publishedAt"][:10],
                         short=seconds(v["contentDetails"].get("duration")) <= 60))
    return rows

def comment_requests(video_id):
    """Count comments asking for more content (1 quota unit)."""
    try:
        cs = yt("commentThreads", part="snippet", videoId=video_id, maxResults=50, order="relevance")["items"]
        return sum(bool(REQ_RE.search(c["snippet"]["topLevelComment"]["snippet"]["textDisplay"])) for c in cs)
    except Exception:
        return 0

def outliers_of(rows):
    for r in rows:
        r["ratio"] = r["views"] / max(r["subs"], 100)
    return sorted([r for r in rows if r["subs"] <= 100_000 and r["views"] >= 10_000 and r["ratio"] >= 5],
                  key=lambda r: -r["ratio"])

def analyze(rows, requests_n=0):
    """Score = outlier strength + breadth + new-channel share + comment requests, discounted by big-channel dominance."""
    out = outliers_of(rows)
    chans = {r["channel_id"]: r for r in out}
    year_ago = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    med = statistics.median(r["ratio"] for r in out) if out else 0
    new_share = sum(r["chan_created"] >= year_ago for r in chans.values()) / len(chans) if chans else 0
    sat = sum(r["subs"] >= 500_000 for r in rows) / len(rows) if rows else 0
    strength = min(math.log10(med) / 2, 1) if med > 1 else 0
    breadth = min(len(chans) / 8, 1)
    score = (0.35 * strength + 0.30 * breadth + 0.15 * new_share + 0.20 * min(requests_n / 10, 1)) * (1 - 0.6 * sat)
    return dict(outliers=out, n_channels=len(chans), median_ratio=round(med, 1), new_share=round(new_share, 2),
                saturation=round(sat, 2), requests=requests_n, score=round(score, 3),
                short_share=round(sum(bool(r.get("short")) for r in out) / len(out), 2) if out else 0.0)

def brief(niche, evidence):
    """Channel plan. Uses Gemini free tier if GEMINI_API_KEY is set, else a plain template."""
    lines = "\n".join(f"- {e['title']} ({e['views']:,} views, {e['subs']:,} subs)" for e in evidence[:8])
    template = f"Niche: {niche}\nSmall channels are getting outsized views:\n{lines}\nCopy the angles of these videos, then differentiate on format or depth."
    k = os.getenv("GEMINI_API_KEY")
    if not k:
        return template
    prompt = (f"A YouTube niche '{niche}' shows small channels with outlier videos:\n{lines}\n"
              "Write a short channel brief: 1) positioning angle, 2) audience, 3) ten first video ideas, "
              "4) monetization paths (ads, affiliate, products), 5) whether it can be faceless. Be concrete and concise.")
    try:
        m = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
                          params={"key": k}, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=60)
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return template

def main():
    if not KEY:
        raise SystemExit("Skipping YouTube: no YT_API_KEY in settings.env (free key, see START_HERE.txt).")
    db = sqlite3.connect(DB)
    db.execute("""CREATE TABLE IF NOT EXISTS channel_opps(ts TEXT, niche TEXT, n_channels INT, median_ratio REAL,
                  new_share REAL, saturation REAL, requests INT, score REAL)""")
    for col, typ in (("short_share", "REAL"), ("monetization", "TEXT")):
        try: db.execute(f"ALTER TABLE channel_opps ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError: pass
    db.execute("""CREATE TABLE IF NOT EXISTS outlier_videos(ts TEXT, niche TEXT, video_id TEXT, title TEXT,
                  channel TEXT, subs INT, views INT, ratio REAL, chan_created TEXT)""")
    db.execute("CREATE TABLE IF NOT EXISTS chan_snap(ts TEXT, channel_id TEXT, channel TEXT, subs INT)")
    start = (int(time.time() // 21600) * PER_RUN) % len(NICHES)   # rotate niches across runs
    batch = [NICHES[(start + i) % len(NICHES)] for i in range(min(PER_RUN, len(NICHES)))]
    ts, results = dt.datetime.utcnow().isoformat(timespec="minutes"), []
    for niche in batch:
        try:
            rows = fetch(niche)
            top = outliers_of(rows)[:3]
            a = analyze(rows, sum(comment_requests(r["video_id"]) for r in top))
        except requests.HTTPError as e:
            print(f"Stopping ({niche}): {e}"); break          # usually quota exhausted
        db.execute("""INSERT INTO channel_opps(ts,niche,n_channels,median_ratio,new_share,saturation,requests,score,
                      short_share,monetization) VALUES (?,?,?,?,?,?,?,?,?,?)""", (ts, niche, a["n_channels"],
                   a["median_ratio"], a["new_share"], a["saturation"], a["requests"], a["score"], a["short_share"], monetization(niche)))
        db.executemany("INSERT INTO outlier_videos VALUES (?,?,?,?,?,?,?,?,?)",
                       [(ts, niche, r["video_id"], r["title"], r["channel"], r["subs"], r["views"],
                         round(r["ratio"], 1), r["chan_created"]) for r in a["outliers"][:15]])
        db.executemany("INSERT INTO chan_snap VALUES (?,?,?,?)",
                       [(ts, r["channel_id"], r["channel"], r["subs"]) for r in {r["channel_id"]: r for r in a["outliers"]}.values()])
        db.commit()
        results.append((a["score"], niche))
        print(f"{niche}: score {a['score']} ({a['n_channels']} outlier channels)")
    hot = [f"{s:.2f}  {n}" for s, n in sorted(results, reverse=True) if s >= ALERT_SCORE]
    if hot:
        from radar import alert
        alert("Channel Finder - open niches:\n" + "\n".join(hot))

if __name__ == "__main__":
    main()
