/**
 * Minimal egress proxy on Cloudflare Workers.
 *
 * Why: api.stocktwits.com returns Cloudflare 403 and gamma-api.polymarket.com
 * times out from this machine's exit IP — verified both directly, through the
 * local LightProxy, and inside a real Chrome session. A Worker gives us a
 * different exit network for those two hosts only.
 *
 * This is deliberately NOT an open proxy: requests must carry the shared secret
 * and target one of the allow-listed hosts, otherwise an internet-wide open
 * relay would be published under this account.
 *
 * Usage:  GET https://<worker>.workers.dev/?url=<encoded>   header: X-Proxy-Key
 */
const ALLOWED_HOSTS = new Set([
  "api.stocktwits.com",
  "gamma-api.polymarket.com",
  "clob.polymarket.com",
  "data-api.polymarket.com",
  "api.elections.kalshi.com",
  "www.reddit.com",
  "oauth.reddit.com",
]);

export default {
  async fetch(request, env) {
    // A `--temporary` preview deploy cannot hold secrets, so the key is
    // enforced only once it exists. Without it the allow-list is the sole
    // guard — tolerable for a short-lived preview of five public read-only
    // market-data endpoints, NOT for a deployment you intend to keep.
    if (env.PROXY_KEY && request.headers.get("X-Proxy-Key") !== env.PROXY_KEY) {
      return new Response("forbidden\n", { status: 403 });
    }

    const requestUrl = new URL(request.url);
    let target = null;

    // Mode A — transparent prefix: /p/<host>/<path>?<query> keeps the caller's
    // own query string, so an existing client only repoints its base URL.
    if (requestUrl.pathname.startsWith("/p/")) {
      const rest = requestUrl.pathname.slice(3);
      const slash = rest.indexOf("/");
      const host = slash === -1 ? rest : rest.slice(0, slash);
      const path = slash === -1 ? "" : rest.slice(slash);
      target = `https://${host}${path}${requestUrl.search}`;
    } else {
      // Mode B — single-shot: /?url=<encoded>
      target = requestUrl.searchParams.get("url");
    }

    if (!target) {
      return new Response("usage: /p/<host>/<path> or /?url=<encoded>\n", { status: 400 });
    }

    let upstream;
    try {
      upstream = new URL(target);
    } catch {
      return new Response("bad url\n", { status: 400 });
    }
    if (upstream.protocol !== "https:" || !ALLOWED_HOSTS.has(upstream.hostname)) {
      return new Response(`host not allowed: ${upstream.hostname}\n`, { status: 403 });
    }

    const resp = await fetch(upstream.toString(), {
      headers: {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " +
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
      },
      cf: { cacheTtl: 60, cacheEverything: false },
    });

    return new Response(resp.body, {
      status: resp.status,
      headers: {
        "Content-Type": resp.headers.get("Content-Type") || "application/json",
        "X-Upstream-Status": String(resp.status),
      },
    });
  },
};
