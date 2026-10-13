"""Opportunity Radar - product demand/supply scanner (free sources only)."""
import env  # loads settings.env
import os, re, time, math, sqlite3, requests, datetime as dt

DB = os.getenv("RADAR_DB", "radar.db")
UA = {"User-Agent": "opportunity-radar/0.1 (personal research)"}
# Seeded from Sept 2026 research (Shopify, Glimpse/Google Trends, TikTok Shop and Amazon-seller reports)
SEEDS = [
    # Health & wellness (growing, mostly non-supplement to limit compliance risk)
    "sleep aid", "blue light therapy", "hair repair", "massage gun", "air purifier",
    # Home & tech
    "portable power station", "cordless vacuum", "desk mat", "LED strip lights", "bluetooth tracker",
    # Pets, kids, gifts
    "dog harness", "pet health", "baby sunscreen", "screen free toys", "personalized gift",
    # Seasonal / niche
    "press on nails", "cushion cover",
]
PATTERNS = ["best {} for", "where to buy {}", "{} alternative", "{} that doesn't", "cheap {}", "{} for small"]
REQUEST_RE = re.compile(r"where (can|do) i (buy|find|get)|does anyone (make|sell)|i'?d pay for|"
                        r"why (doesn'?t|isn'?t) .* exist|looking for a|sold out|out of stock", re.I)
MAX_TRENDS = int(os.getenv("MAX_TRENDS", 30))   # Trends is rate-limited; keep low
ALERT_SCORE = float(os.getenv("ALERT_SCORE", 0.6))
DEEP_N = int(os.getenv("DEEP_N", 12))   # how many top candidates get the slower deep checks

def autocomplete(q):
    try:
        r = requests.get("https://suggestqueries.google.com/complete/search",
                         params={"client": "firefox", "q": q}, headers=UA, timeout=10)
        return r.json()[1]
    except Exception:
        return []

def candidates(seed, top=12):
    """Mine autocomplete for what people type. Frequent across patterns = stronger intent."""
    counts = {}
    for p in PATTERNS:
        for s in autocomplete(p.format(seed)):
            counts[s] = counts.get(s, 0) + 1
        time.sleep(0.4)
    return sorted(counts.items(), key=lambda x: -x[1])[:top]

_pt = None
def trend_growth(kw):
    """Recent 4 weeks vs prior 8 weeks, from Google Trends (unofficial, may fail)."""
    global _pt
    try:
        from pytrends.request import TrendReq
        _pt = _pt or TrendReq(hl="en-US", tz=0)
        _pt.build_payload([kw], timeframe="today 3-m")
        s = _pt.interest_over_time()[kw]
        recent, prior = s[-4:].mean(), s[:-4].mean()
        time.sleep(2)
        return max(-1.0, min(2.0, (recent - prior) / (prior + 1)))
    except Exception:
        return 0.0

def reddit_requests(kw):
    """Count recent posts that look like buying requests / unmet demand."""
    try:
        r = requests.get("https://www.reddit.com/search.json", headers=UA, timeout=10,
                         params={"q": kw, "sort": "new", "t": "month", "limit": 50})
        titles = [c["data"]["title"] for c in r.json()["data"]["children"]]
        time.sleep(1.5)
        return sum(bool(REQUEST_RE.search(t)) for t in titles)
    except Exception:
        return 0

def supply_saturation(kw):
    """0 = few sellers, 1 = crowded. Uses Etsy listing count if ETSY_API_KEY is set, else neutral."""
    key = os.getenv("ETSY_API_KEY")
    if not key:
        return 0.5
    try:
        r = requests.get("https://openapi.etsy.com/v3/application/listings/active",
                         params={"keywords": kw, "limit": 1}, headers={"x-api-key": key}, timeout=10)
        return min(1.0, math.log10(r.json().get("count", 0) + 1) / 5)
    except Exception:
        return 0.5

def score(growth, requests_n, intent, supply):
    g = (growth + 1) / 3                    # -1..2 -> 0..1
    demand = 0.45 * g + 0.30 * min(requests_n / 5, 1) + 0.25 * min(intent / 4, 1)
    return round(demand * (1 - 0.7 * supply), 3)

def classify(s):
    """Label a weekly 5-year Trends curve: seasonal / fading / peaking / emerging / stable."""
    if len(s) < 120: return None
    recent, base, peak = sum(s[-12:]) / 12, sum(s[-64:-12]) / 52, max(s)
    a, b = s[-52:], s[-104:-52]
    ma, mb = sum(a) / 52, sum(b) / 52
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    var = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    if var and cov / var > 0.6: return "seasonal"
    if recent < 0.7 * base: return "fading"
    if recent > 1.2 * base and recent >= 0.85 * peak: return "peaking"
    if recent > 1.3 * base: return "emerging"
    return "stable"

def lifecycle(kw):
    global _pt
    try:
        from pytrends.request import TrendReq
        _pt = _pt or TrendReq(hl="en-US", tz=0)
        _pt.build_payload([kw], timeframe="today 5-y")
        s = list(_pt.interest_over_time()[kw]); time.sleep(2)
        return classify(s)
    except Exception:
        return None

COMPLAINT_RE = re.compile(r"broke|broken|disappointed|worst|waste of money|doesn'?t work|stopped working|wish it|"
                          r"too small|too big|cheap(ly)? made|falls apart|not worth|returned", re.I)

def complaints(kw):
    """Reddit posts complaining about products like this = a chance to make a better one."""
    try:
        r = requests.get("https://www.reddit.com/search.json", headers=UA, timeout=10,
                         params={"q": kw, "sort": "new", "t": "year", "limit": 50})
        posts = [c["data"] for c in r.json()["data"]["children"]]
        time.sleep(1.5)
        return sum(bool(COMPLAINT_RE.search(p["title"] + " " + p.get("selftext", "")[:300])) for p in posts)
    except Exception:
        return 0

RISKS = [(r"baby|infant|kid|child|toy", "Children's product: needs safety testing (CPSIA/CPSC rules)."),
         (r"supplement|vitamin|gummy|collagen|probiotic", "Supplement: FDA rules and strict limits on health claims."),
         (r"skin|cream|serum|sunscreen|lip|cosmetic|hair|nail", "Skincare/cosmetic: labeling and ingredient rules apply."),
         (r"battery|power station|charger|power bank|led|electric|vacuum", "Electronics/batteries: FCC/CE marks and lithium shipping rules."),
         (r"air purifier|massage|therapy|medical|sleep|mouth tape", "Health-style product: do not make medical claims."),
         (r"dog|cat|pet", "Pet product: check pet-safety rules.")]

def risk_flags(kw):
    return [m for p, m in RISKS if re.search(p, kw, re.I)]

def check_links(kw):
    from urllib.parse import quote_plus as q
    return {"Trademark search (USPTO)": "https://tmsearch.uspto.gov/",
            "Patent search (Google Patents)": f"https://patents.google.com/?q={q(kw)}"}

def supplier_links(kw):
    from urllib.parse import quote_plus as q
    k = q(kw)
    return {"Alibaba": f"https://www.alibaba.com/trade/search?SearchText={k}",
            "AliExpress": f"https://www.aliexpress.com/wholesale?SearchText={k}",
            "Faire": f"https://www.faire.com/search?q={k}",
            "ThomasNet": f"https://www.thomasnet.com/search/?q={k}",
            "Amazon": f"https://www.amazon.com/s?k={k}"}

def alert(text):
    tok, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT")
    if tok and chat:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data={"chat_id": chat, "text": text}, timeout=10)

def main():
    db = sqlite3.connect(DB)
    db.execute("""CREATE TABLE IF NOT EXISTS opps(ts TEXT, seed TEXT, keyword TEXT, growth REAL,
                  requests INT, intent INT, supply REAL, score REAL)""")
    for col, typ in (("lifecycle", "TEXT"), ("complaints", "INT")):
        try: db.execute(f"ALTER TABLE opps ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError: pass
    ts, found, pool = dt.datetime.utcnow().isoformat(timespec="minutes"), [], []
    for seed in SEEDS:                       # pass 1: cheap signals for every candidate
        for kw, intent in candidates(seed):
            pool.append(dict(seed=seed, kw=kw, intent=intent, req=reddit_requests(kw), sup=supply_saturation(kw)))
    pool.sort(key=lambda p: -score(0.0, p["req"], p["intent"], p["sup"]))
    for i, p in enumerate(pool):             # pass 2: spend the limited Trends budget on the most promising
        g = trend_growth(p["kw"]) if i < MAX_TRENDS else None
        sc = score(g or 0.0, p["req"], p["intent"], p["sup"])
        life, comp = (lifecycle(p["kw"]), complaints(p["kw"])) if i < DEEP_N else (None, None)
        sc = round(min(1.0, sc * {"emerging": 1.15, "peaking": 0.9, "fading": 0.6}.get(life, 1.0)), 3)
        db.execute("INSERT INTO opps(ts,seed,keyword,growth,requests,intent,supply,score,lifecycle,complaints) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (ts, p["seed"], p["kw"], g, p["req"], p["intent"], p["sup"], sc, life, comp))
        found.append((sc, p["kw"]))
    db.commit()
    found.sort(reverse=True)
    hot = [f"{s:.2f}  {k}" for s, k in found[:5] if s >= ALERT_SCORE]
    if hot:
        alert("Opportunity Radar - top finds:\n" + "\n".join(hot))
    print(f"Saved {len(found)} candidates. Top: {found[:5]}")

if __name__ == "__main__":
    main()
