"""Per-source guards for the data feeds that fail on this machine.

Diagnosed 2026-08-13/14, each verified through the app's own fetchers:

* Reddit — unauthenticated access is rate-limited per IP (best case 2/6 requests
  succeed regardless of spacing), and a swallowed 429 is indistinguishable from
  "nobody discussed this ticker", which is how six tickers all reported zero
  mentions. Served by the Cloudflare Worker's site-wide ``search.rss`` (6/6 at
  ~0.9s, no browser); ``search.json`` is 403 from the Worker's datacenter IP, so
  engagement counts are unavailable on that path. Fallback is ego-browser, whose
  ``browserFetch`` runs inside a real reddit.com page and therefore does reach
  ``search.json`` — slower, but it returns score and comment counts.
* StockTwits — Cloudflare 403 direct AND through the local proxy, also with a
  Chrome TLS fingerprint, and even inside a real browser session: blocked by exit
  IP. Revived by routing through the Worker, which restores the bull/bear ratio.
* Polymarket — connect timeout on every local path including a real browser.
  Also revived through the Worker; Manifold Markets (play-money) is the labelled
  substitute if the Worker is unavailable.
* FRED — reachable but drops connections under load (2 of 26 calls failed in one
  run, silently costing a report its CPI and unemployment figures), so its
  request helper gets a bounded retry.

Import ``net_bootstrap`` and call ``apply()`` before ``install()``.
"""
import base64
import json
import os
import random
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_OAUTH_SEARCH = "https://oauth.reddit.com/r/{sub}/search"
_UA = "python:tradingagents:v0.2 (by /u/tradingagents)"
_token_cache: dict = {}
_reddit_cache: dict = {}
_reddit_raw_counts: dict = {}
_dead_sources: dict = {}


_FINANCE_SUB = re.compile(
    r"invest|stock|trad|dividend|value|market|wallstreet|wsb|finance|ticker|"
    r"portfolio|securit|equit|option|bogle|fire|money|econom",
    re.I,
)


def _is_relevant(post: dict, ticker: str) -> bool:
    """Reddit search is fuzzy on short tokens (SCHW matched German 'Schw*'),
    so require a finance-ish subreddit AND the ticker as a standalone token."""
    if not _FINANCE_SUB.search(str(post.get("subreddit") or "")):
        return False
    text = f"{post.get('title') or ''} {post.get('selftext') or ''}"
    return re.search(rf"\${ticker}\b|\b{ticker}\b", text) is not None


_EGO_SCRIPT = """
const task = await useOrCreateTaskSpace('reddit sentiment fetch')
await openOrReuseTab('https://www.reddit.com/', {{ wait: true, timeout: 30 }})
const raw = await browserFetch('https://www.reddit.com/search.json?q={ticker}'
  + '&sort=new&t={window}&limit={limit}&raw_json=1')
cliLog('__JSON__' + (typeof raw === 'string' ? raw : JSON.stringify(raw)))
"""


def _ego_reddit(ticker: str, window: str = "week", limit: int = 25) -> list[dict] | None:
    """Reddit search through ego-browser — the fallback when the Worker is down.

    ``browserFetch`` issues the request from a real reddit.com page context, so it
    reaches ``search.json``, which the Worker cannot (403 from datacenter IPs) and
    this machine's IP throttles (429). That buys back the score and comment counts
    the Worker's RSS path cannot provide. ego uses its own isolated task space, so
    it does not disturb the user's browser windows.
    """
    if not shutil.which("ego-browser"):
        return None
    script = _EGO_SCRIPT.format(ticker=urllib.parse.quote(ticker),
                                window=window, limit=limit)
    try:
        proc = subprocess.run(["ego-browser", "nodejs"], input=script,
                              capture_output=True, text=True, timeout=180)
    except (subprocess.TimeoutExpired, OSError):
        return None

    # ego's cliLog writes to stderr, not stdout.
    combined = (proc.stdout or "") + (proc.stderr or "")
    marker = combined.find("__JSON__")
    if marker < 0:
        return None
    try:
        payload = json.loads(combined[marker + len("__JSON__"):].strip())
    except json.JSONDecodeError:
        return None

    posts = []
    for child in (payload.get("data", {}) or {}).get("children", []):
        d = child.get("data", {})
        posts.append({"title": d.get("title"), "selftext": d.get("selftext"),
                      "subreddit": f"r/{d.get('subreddit')}",
                      "score": d.get("score"), "num_comments": d.get("num_comments"),
                      "created_utc": d.get("created_utc"), "source": "ego-json"})
    kept = [p for p in posts if _is_relevant(p, ticker.upper())]
    _reddit_raw_counts[ticker.upper()] = (len(posts), len(kept))
    return kept


def _format_reddit(ticker: str, posts: list[dict], window: str) -> str:
    by_sub: dict[str, list[dict]] = {}
    for p in posts:
        by_sub.setdefault(str(p.get("subreddit") or "?"), []).append(p)
    raw, kept = _reddit_raw_counts.get(ticker, (len(posts), len(posts)))
    transports = {
        "worker-rss": "Cloudflare Worker site-wide RSS; engagement counts unavailable",
        "ego-json": "ego-browser search.json (real page context, engagement counts included)",
        "oauth": "Reddit OAuth API",
        "rss": "anonymous RSS fallback",
    }
    transport = transports.get(posts[0].get("source") if posts else None,
                               "unknown transport")
    header = (f"Reddit search for {ticker} — {len(posts)} relevant posts from the past "
              f"{window}, newest first (via {transport}; {raw - kept} of {raw} "
              f"raw hits dropped as off-topic or non-finance subreddits):")
    blocks = [header]
    for sub, items in by_sub.items():
        lines = [f"\n{sub} — {len(items)} post(s):"]
        for p in items:
            created = p.get("created_utc") or 0
            day = time.strftime("%Y-%m-%d", time.gmtime(created)) if created else "?"
            score = p.get("score")
            comments = p.get("comments", p.get("num_comments"))
            meta = day if score is None else f"{day} · {score}↑ · {comments}c"
            body = (p.get("selftext") or "").replace("\n", " ").strip()[:240]
            lines.append(f"  [{meta}] {(p.get('title') or '').strip()}")
            if body:
                lines.append(f"    body excerpt: {body}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def _oauth_token() -> str | None:
    """App-only bearer token for a Reddit 'script' app. None when unconfigured."""
    cid, secret = os.getenv("REDDIT_CLIENT_ID"), os.getenv("REDDIT_CLIENT_SECRET")
    if not cid or not secret:
        return None
    cached = _token_cache.get("token")
    if cached and _token_cache.get("expires_at", 0) > time.time() + 60:
        return cached

    creds = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    body = {"grant_type": "client_credentials"}
    user, password = os.getenv("REDDIT_USERNAME"), os.getenv("REDDIT_PASSWORD")
    if user and password:  # script apps may use the password grant
        body = {"grant_type": "password", "username": user, "password": password}
    req = urllib.request.Request(
        _TOKEN_URL,
        data=urllib.parse.urlencode(body).encode(),
        headers={"Authorization": f"Basic {creds}", "User-Agent": _UA},
    )
    try:
        import json
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.load(resp)
    except Exception as exc:
        print(f"[guards] reddit oauth token failed: {type(exc).__name__} {exc}")
        return None
    token = payload.get("access_token")
    if token:
        _token_cache.update(token=token,
                            expires_at=time.time() + float(payload.get("expires_in", 3600)))
    return token


def _oauth_search(token: str, sub: str, ticker: str, limit: int) -> list[dict]:
    import json
    qs = urllib.parse.urlencode({"q": ticker, "restrict_sr": "on", "sort": "new",
                                 "t": "week", "limit": limit})
    req = urllib.request.Request(
        f"{_OAUTH_SEARCH.format(sub=sub)}?{qs}",
        headers={"Authorization": f"bearer {token}", "User-Agent": _UA},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.load(resp)
    out = []
    for child in payload.get("data", {}).get("children", []):
        d = child.get("data", {})
        out.append({"title": d.get("title"), "selftext": d.get("selftext"),
                    "score": d.get("score"), "num_comments": d.get("num_comments"),
                    "created_utc": d.get("created_utc"), "source": "oauth"})
    return out


def _worker_reddit(ticker: str, window: str = "week", limit: int = 25) -> list[dict] | None:
    """Site-wide Reddit search through the Cloudflare Worker — no browser needed.

    Reddit throttles this machine's IP hard (2/6 requests succeeded directly);
    through the Worker it is 6/6 at ~0.9s. Only the RSS endpoint is reachable —
    ``search.json`` returns 403 from a datacenter IP — so posts carry no score or
    comment count on this path.
    """
    if not _WORKER:
        return None
    qs = urllib.parse.urlencode({"q": ticker, "sort": "new", "t": window,
                                 "limit": limit})
    url = f"{_WORKER}/p/www.reddit.com/search.rss?{qs}"
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        **({"X-Proxy-Key": _WORKER_KEY} if _WORKER_KEY else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml = resp.read()
    except Exception:
        return None

    try:
        # defusedxml when present; stdlib ElementTree does not resolve external
        # entities, but a DTD is still refused outright rather than parsed.
        from defusedxml import ElementTree as ET  # type: ignore
    except ImportError:
        import xml.etree.ElementTree as ET
        if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
            return None
    ns = {"a": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(xml)
    except Exception:
        return None

    posts = []
    for entry in root.findall("a:entry", ns):
        category = entry.find("a:category", ns)
        updated = (entry.findtext("a:updated", default="", namespaces=ns) or "")[:19]
        try:
            created = time.mktime(time.strptime(updated, "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            created = None
        body = re.sub(r"<[^>]+>", " ",
                      entry.findtext("a:content", default="", namespaces=ns) or "")
        posts.append({
            "title": entry.findtext("a:title", default="", namespaces=ns),
            "selftext": re.sub(r"\s+", " ", body).strip(),
            "subreddit": (category.get("label") if category is not None
                          else category.get("term") if category is not None else "?"),
            "created_utc": created,
            "score": None, "num_comments": None, "source": "worker-rss",
        })

    kept = [p for p in posts if _is_relevant(p, ticker.upper())]
    _reddit_raw_counts[ticker.upper()] = (len(posts), len(kept))
    return kept


def _manifold_markets(topic: str, limit: int = 6) -> str | None:
    """Forward-looking probabilities from Manifold Markets.

    Substitute for Polymarket, which is unreachable from this network even in a
    real browser session. Manifold is play-money, so its probabilities are
    noisier and easier to move than a real-money book — the returned block says
    so, because an unlabelled probability would be read as harder evidence than
    it is.
    """
    qs = urllib.parse.urlencode({"term": topic, "filter": "open",
                                 "sort": "liquidity", "limit": limit,
                                 "contractType": "BINARY"})
    req = urllib.request.Request(
        f"https://api.manifold.markets/v0/search-markets?{qs}",
        headers={"User-Agent": _UA, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            markets = json.load(resp)
    except Exception:
        return None
    if not isinstance(markets, list) or not markets:
        return None

    lines = [f"## Prediction markets for '{topic}' — via Manifold Markets",
             "(Polymarket is unreachable from this network; Manifold is a "
             "PLAY-MONEY forecasting market. Treat these probabilities as crowd "
             "opinion with thin skin in the game, not as real-money odds.)",
             "",
             "| Market | Probability | Closes | Liquidity |",
             "|---|---|---|---|"]
    for m in markets:
        prob = m.get("probability")
        if prob is None:
            continue
        close_ms = m.get("closeTime")
        closes = (time.strftime("%Y-%m-%d", time.gmtime(close_ms / 1000))
                  if close_ms else "?")
        question = str(m.get("question") or "").replace("|", "/")[:90]
        lines.append(f"| {question} | {prob * 100:.1f}% | {closes} | "
                     f"{round(m.get('totalLiquidity') or 0)} |")
    return "\n".join(lines) if len(lines) > 5 else None


_WORKER = os.getenv("TA_EGRESS_PROXY",
                    "https://ta-egress-proxy.quasar-cymbal.workers.dev").rstrip("/")
_WORKER_KEY = os.getenv("TA_EGRESS_PROXY_KEY")


def _worker_alive() -> bool:
    """Is the egress Worker reachable? A --temporary deploy expires unclaimed."""
    if not _WORKER:
        return False
    try:
        import requests
        r = requests.get(f"{_WORKER}/p/api.stocktwits.com/api/2/streams/symbol/AAPL.json",
                         headers={"X-Proxy-Key": _WORKER_KEY} if _WORKER_KEY else {},
                         timeout=20)
        return r.status_code == 200
    except Exception:
        return False


def _route_via_worker(st_mod, pm_mod) -> bool:
    """Repoint the two IP-blocked sources at the Worker.

    Both modules build their own URLs from a base constant, so swapping the base
    for the Worker's ``/p/<host>`` prefix reuses every line of their existing
    parsing, sentiment tallying and error handling.
    """
    if not _worker_alive():
        return False

    st_mod._API = (f"{_WORKER}/p/api.stocktwits.com/api/2/streams/symbol/"
                   "{ticker}.json")
    pm_mod.GAMMA_BASE = f"{_WORKER}/p/gamma-api.polymarket.com"

    if _WORKER_KEY:
        original_urlopen = st_mod.urlopen

        def urlopen_with_key(req, *args, **kwargs):
            if hasattr(req, "add_header"):
                req.add_header("X-Proxy-Key", _WORKER_KEY)
            return original_urlopen(req, *args, **kwargs)

        st_mod.urlopen = urlopen_with_key
        original_get = pm_mod.requests.get

        def get_with_key(url, **kwargs):
            headers = dict(kwargs.pop("headers", None) or {})
            headers["X-Proxy-Key"] = _WORKER_KEY
            return original_get(url, headers=headers, **kwargs)

        pm_mod.requests = type("_ReqShim", (), {"get": staticmethod(get_with_key)})()
    return True


def _harden_fred(fred_mod) -> bool:
    """FRED is reachable but intermittently drops connections under load.

    In the 2026-08-14 rerun 2 of 26 macro calls failed with "Max retries
    exceeded"; since macro_data is an optional category it degrades silently, so
    one blip cost the CVS report its CPI and unemployment figures. A bounded
    retry converts a transient failure into a slightly slower success.
    """
    original_request = getattr(fred_mod, "_request", None)
    if original_request is None:
        return False

    def request_with_retry(path, params):
        last = None
        for attempt in range(3):
            try:
                return original_request(path, params)
            except Exception as exc:  # network-layer only; parse errors resurface
                last = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1) + random.uniform(0, 1.0))
        raise last

    fred_mod._request = request_with_retry
    return True


def install() -> dict:
    """Patch the three fetchers in place. Returns which paths are active."""
    from tradingagents.agents.analysts import sentiment_analyst as sa_mod
    from tradingagents.dataflows import (
        polymarket as pm_mod,
        reddit as reddit_mod,
        stocktwits as st_mod,
    )

    original_reddit = reddit_mod.fetch_reddit_posts
    original_rss = reddit_mod._fetch_subreddit_rss
    original_st = st_mod.fetch_stocktwits_messages
    original_pm = pm_mod.get_prediction_markets

    throttled = {"count": 0}

    def rss_with_retries(ticker, sub, limit, timeout, _retry=True):
        """Anonymous RSS: retry briefly, and remember when we were throttled.

        ``_retry=False`` is passed through: the original RSS fetcher's own
        429-retry re-enters ``reddit_mod._fetch_subreddit_rss`` by global name,
        which this patch has rebound to this wrapper — that made a 429 recurse
        forever (2026-09-16 gate hang). This wrapper owns the retry policy now.
        """
        for attempt in range(2):
            try:
                posts = original_rss(ticker, sub, limit, timeout, False)
            except Exception:
                posts = []
            if posts:
                return posts
            throttled["count"] += 1
            if attempt == 0:
                time.sleep(2.0 + random.uniform(0, 3.0))
        return []

    def reddit_patched(ticker, *args, **kwargs):
        key = ticker.upper()
        if key in _reddit_cache:
            return _reddit_cache[key]
        # Worker first: no browser, 6/6 reliability. ego second — it spins up a
        # browser task space but reaches search.json, so it carries score and
        # comment counts the Worker's RSS path cannot return.
        posts = _worker_reddit(key, "week")
        source = "worker"
        if posts is None:
            posts = _ego_reddit(key, "week")
            source = "ego"
        if posts is not None:
            if posts:
                out = _format_reddit(key, posts, "week")
            else:
                wider = (_worker_reddit(key, "month") if source == "worker"
                         else _ego_reddit(key, "month")) or []
                out = (_format_reddit(key, wider, "month") +
                       f"\n\n(No posts in the past 7 days; the block above widens the window "
                       f"to a month. Retail attention on {key} is low, which is a real "
                       f"finding here — the fetch itself succeeded.)"
                       if wider else
                       f"<no Reddit posts mentioning {key} in the past month "
                       f"(site-wide search succeeded — genuinely no discussion)>")
            _reddit_cache[key] = out
            return out
        token = _oauth_token()
        if token:
            blocks, total = [], 0
            for sub in reddit_mod.DEFAULT_SUBREDDITS:
                try:
                    posts = _oauth_search(token, sub, key, 5)
                except Exception as exc:
                    blocks.append(f"r/{sub}: <fetch failed: {type(exc).__name__}>")
                    continue
                total += len(posts)
                if not posts:
                    blocks.append(f"r/{sub}: <no posts mentioning {key} in the past 7 days>")
                    continue
                lines = [f"r/{sub} — {len(posts)} recent posts mentioning {key} (via OAuth API):"]
                for p in posts:
                    day = time.strftime("%Y-%m-%d", time.gmtime(p["created_utc"] or 0))
                    body = (p.get("selftext") or "").replace("\n", " ").strip()[:240]
                    lines.append(f"  [{day} · {p.get('score')}↑ · {p.get('num_comments')}c] "
                                 f"{(p.get('title') or '').strip()}")
                    if body:
                        lines.append(f"    body excerpt: {body}")
                blocks.append("\n".join(lines))
            out = ("\n\n".join(blocks) if total else
                   f"<no Reddit posts found mentioning {key} across "
                   f"{', '.join('r/' + s for s in reddit_mod.DEFAULT_SUBREDDITS)} in the past 7 days>")
        else:
            reddit_mod._fetch_subreddit_rss = rss_with_retries
            before = throttled["count"]
            out = original_reddit(ticker, *args, **kwargs)
            if throttled["count"] > before:
                out += ("\n\n(WARNING: Reddit rate-limited this fetch (HTTP 429) on "
                        f"{throttled['count'] - before} request(s) — unauthenticated access "
                        "is throttled on this network. Any \"no posts found\" line above is "
                        "MISSING DATA, not evidence of silence. Do not treat it as a "
                        "neutral or bearish retail-sentiment signal.)")
        _reddit_cache[key] = out
        return out

    def circuit_break(name, fn, sentinel_prefix):
        """Probe a host once per process; reuse the failure instead of re-waiting."""
        def wrapped(*args, **kwargs):
            if name in _dead_sources:
                return _dead_sources[name]
            result = fn(*args, **kwargs)
            text = str(result)
            if sentinel_prefix in text or "unavailable" in text.lower() or "error" in text.lower():
                _dead_sources[name] = (
                    f"{text}\n(NOTE: {name} is unreachable from this network — verified "
                    f"blocked both directly and via the local proxy. Treat as absent data, "
                    f"not as a neutral signal.)")
                return _dead_sources[name]
            return result
        return wrapped

    via_worker = _route_via_worker(st_mod, pm_mod)
    from tradingagents.dataflows import fred as fred_mod
    fred_hardened = _harden_fred(fred_mod)

    reddit_mod.fetch_reddit_posts = reddit_patched
    sa_mod.fetch_reddit_posts = reddit_patched

    if via_worker:
        # The Worker reaches both blocked hosts, so their original fetchers work
        # as written — no circuit breaker and no Manifold substitute needed.
        st_backend = "cloudflare worker (IP block bypassed)"
        pm_backend = "polymarket via cloudflare worker"
    else:
        st_patched = circuit_break("StockTwits", original_st, "<stocktwits unavailable")
        st_mod.fetch_stocktwits_messages = st_patched
        sa_mod.fetch_stocktwits_messages = st_patched

        pm_broken = circuit_break("Polymarket", original_pm, "")

        def prediction_markets(topic, limit=None):
            block = _manifold_markets(topic, limit or 6)
            return block if block else pm_broken(topic, limit)

        pm_mod.get_prediction_markets = prediction_markets
        from tradingagents.dataflows import interface as iface
        if "get_prediction_markets" in iface.VENDOR_METHODS:
            iface.VENDOR_METHODS["get_prediction_markets"]["polymarket"] = prediction_markets
        st_backend = "circuit-breaker (blocked)"
        pm_backend = "manifold (polymarket unreachable)"

    if via_worker:
        reddit_backend = "cloudflare worker (site-wide rss, no browser)"
    elif shutil.which("ego-browser"):
        reddit_backend = "ego-browser (isolated task space, search.json)"
    elif _oauth_token():
        reddit_backend = "oauth"
    else:
        reddit_backend = "rss+retry (rate-limited)"
    return {"reddit": reddit_backend,
            "stocktwits": st_backend,
            "prediction_markets": pm_backend,
            "fred": "retry x3" if fred_hardened else "no retry"}


if __name__ == "__main__":
    import net_bootstrap
    net_bootstrap.apply()
    print(install())
