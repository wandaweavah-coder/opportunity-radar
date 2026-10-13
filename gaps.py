"""Digital-product and service gap miner. Free sources: Hacker News (Algolia), Reddit, Google Autocomplete.
Finds 'is there a tool for...', 'I wish there was...', 'looking for a freelancer who...' and clusters them."""
import env  # loads settings.env
import os, re, json, math, time, sqlite3, requests, datetime as dt
from radar import autocomplete, alert, UA

DB = os.getenv("RADAR_DB", "radar.db")
ALERT_SCORE = float(os.getenv("GAP_ALERT_SCORE", 0.6))
SUBS = ["SomebodyMakeThis", "AppIdeas", "Entrepreneur", "smallbusiness", "SaaS", "Notion", "excel", "freelance"]
HN_QUERIES = ["is there a tool", "i wish there was", "alternative to", "someone should build", "looking for software"]
PROFESSIONS = ["dentists", "plumbers", "realtors", "photographers", "teachers", "landscapers", "accountants",
               "etsy sellers", "podcasters", "freelancers", "personal trainers", "restaurants"]
TRIGGER = re.compile(r"is there (an? )?(app|tool|software|service|template|plugin|extension|website|way)|i wish there|"
                     r"someone should (make|build)|i'?d pay for|willing to pay|looking for (an? )?(app|tool|software|"
                     r"service|freelancer|developer|designer)|alternative to|does anyone (know|make|offer)|"
                     r"need (an? )?(app|tool|template|developer|designer|accountant|freelancer|tutor)|hire (an? )?", re.I)
SERVICE = re.compile(r"\b(freelancer|designer|developer|accountant|lawyer|plumber|cleaner|tutor|coach|consultant|agency|"
                     r"hire|contractor|repair|installer|virtual assistant|bookkeeper|editor|service)\b", re.I)
DIGITAL = re.compile(r"\b(app|tool|software|template|plugin|extension|course|website|bot|saas|notion|excel|spreadsheet|ai)\b", re.I)
STOP = set("a an the is there any for to of and or in on i we you my me with that this it be can do does anyone know looking "
           "wish was were someone should make build pay need app tool software service template plugin extension website "
           "way alternative hire freelancer".split())

STRONG = re.compile(r"\b(app|tool|software|template|plugin|extension|website|bot|saas|course|spreadsheet)s?\b", re.I)

def kind_of(text):
    if STRONG.search(text): return "digital"          # "a tool for freelancers" is digital, not a service
    if SERVICE.search(text): return "service"
    return "digital" if DIGITAL.search(text) else None

def item(source, text, url, points=0, comments=0, created=None):
    k = kind_of(text)
    return dict(source=source, kind=k, text=text.strip()[:200], url=url, points=points, comments=comments,
                created=created or dt.date.today().isoformat()) if k else None

def from_hn():
    since, out = int(time.time()) - 30 * 86400, []
    for q in HN_QUERIES:
        try:
            r = requests.get("https://hn.algolia.com/api/v1/search_by_date", timeout=15, params={
                "query": q, "tags": "story", "numericFilters": f"created_at_i>{since}", "hitsPerPage": 50})
            for h in r.json()["hits"]:
                t = h.get("title") or ""
                if TRIGGER.search(t):
                    out.append(item("hackernews", t, f"https://news.ycombinator.com/item?id={h['objectID']}",
                                    h.get("points") or 0, h.get("num_comments") or 0, (h.get("created_at") or "")[:10]))
        except Exception: pass
        time.sleep(0.5)
    return out

def from_reddit():
    out = []
    for s in SUBS:
        try:
            r = requests.get(f"https://www.reddit.com/r/{s}/top.json", headers=UA, timeout=15, params={"t": "month", "limit": 100})
            for c in r.json()["data"]["children"]:
                d = c["data"]
                if TRIGGER.search(d["title"]):
                    out.append(item(f"r/{s}", d["title"], "https://reddit.com" + d["permalink"], d["score"], d["num_comments"],
                                    dt.datetime.utcfromtimestamp(d["created_utc"]).date().isoformat()))
        except Exception: pass
        time.sleep(1.5)
    return out

def from_autocomplete():
    out = []
    for p in PROFESSIONS:
        for stem in ("software for", "app for", "template for"):
            for s in autocomplete(f"{stem} {p}")[:6]:
                out.append(item("autocomplete", s, "https://www.google.com/search?q=" + s.replace(" ", "+")))
            time.sleep(0.4)
    return out

def stem(w): return w[:-1] if w.endswith("s") and not w.endswith("ss") else w

def toks(t): return {stem(w) for w in re.findall(r"[a-z]{3,}", t.lower()) if w not in STOP}

def cluster(items):
    groups = []
    for it in sorted(items, key=lambda x: -(x["points"] + x["comments"])):
        t = toks(it["text"])
        if not t: continue
        for g in groups:
            if len(t & g["tokens"]) / min(len(t), len(g["tokens"])) >= 0.6:
                g["items"].append(it); g["tokens"] |= t; break
        else:
            groups.append({"tokens": set(t), "items": [it]})
    return groups

def score_group(g):
    its, cutoff = g["items"], (dt.date.today() - dt.timedelta(days=14)).isoformat()
    eng = sum(i["points"] + i["comments"] for i in its)
    recent = sum(i["created"] >= cutoff for i in its) / len(its)
    return round(0.40 * min(len(its) / 5, 1) + 0.25 * min(len({i["source"] for i in its}) / 3, 1)
                 + 0.20 * min(math.log10(1 + eng) / 3, 1) + 0.15 * recent, 3), eng

SUPPLY_N = int(os.getenv("SUPPLY_N", 12))

def supply_for(title):
    """How many open-source projects already exist for this idea (GitHub search, free). 0 = nothing, 1 = crowded."""
    q = " ".join(sorted(toks(title))[:4]) + " in:name,description"
    try:
        n = requests.get("https://api.github.com/search/repositories", params={"q": q, "per_page": 1},
                         headers=UA, timeout=15).json().get("total_count", 0)
    except Exception:
        return None
    return round(min(1.0, math.log10(n + 1) / 4), 2)

def main():
    items = [i for i in from_hn() + from_reddit() + from_autocomplete() if i]
    db = sqlite3.connect(DB)
    db.execute("""CREATE TABLE IF NOT EXISTS gaps(ts TEXT, kind TEXT, title TEXT, mentions INT, sources INT,
                  engagement INT, score REAL, evidence TEXT, supply REAL)""")
    try: db.execute("ALTER TABLE gaps ADD COLUMN supply REAL")
    except sqlite3.OperationalError: pass
    ts, rows = dt.datetime.utcnow().isoformat(timespec="minutes"), []
    for kind in ("digital", "service"):
        for g in cluster([i for i in items if i["kind"] == kind]):
            sc, eng = score_group(g)
            rows.append(dict(kind=kind, g=g, sc=sc, eng=eng, supply=None))
    rows.sort(key=lambda r: -r["sc"])
    for r in [r for r in rows if r["kind"] == "digital"][:SUPPLY_N]:   # GitHub allows ~10 searches a minute
        r["supply"] = supply_for(r["g"]["items"][0]["text"]); time.sleep(7)
        if r["supply"] is not None: r["sc"] = round(r["sc"] * (1 - 0.4 * r["supply"]), 3)
    hot = []
    for r in rows:
        g = r["g"]
        ev = [(i["text"], i["url"], i["source"]) for i in g["items"][:6]]
        db.execute("INSERT INTO gaps(ts,kind,title,mentions,sources,engagement,score,evidence,supply) VALUES (?,?,?,?,?,?,?,?,?)",
                   (ts, r["kind"], g["items"][0]["text"], len(g["items"]), len({i["source"] for i in g["items"]}),
                    r["eng"], r["sc"], json.dumps(ev), r["supply"]))
        if r["sc"] >= ALERT_SCORE: hot.append(f"{r['sc']:.2f} [{r['kind']}] {g['items'][0]['text'][:80]}")
    db.commit()
    print(f"{len(items)} raw requests collected, {len(rows)} clusters saved")
    if hot: alert("Gap Finder - new digital/service gaps:\n" + "\n".join(hot[:5]))

if __name__ == "__main__":
    main()
