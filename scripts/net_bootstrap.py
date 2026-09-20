"""Network bootstrap for this machine's egress reality. Import FIRST, before tradingagents.

Two problems this fixes, both diagnosed 2026-08-13:

1. LightProxy (the corporate MITM proxy on 127.0.0.1:12888, set as the macOS
   *system* proxy) re-signs TLS with leaf certificates that carry no Authority
   Key Identifier. Python 3.13+ enables ``VERIFY_X509_STRICT`` in
   ``ssl.create_default_context()``, which rejects those certs outright
   ("Missing Authority Key Identifier") — so every httpx/requests/urllib call
   died while system ``curl`` (LibreSSL, no strict check) worked. We clear only
   that RFC-strictness flag; signature-chain and hostname verification stay on,
   and the LightProxy CA is already trusted at the system level.

2. Some hosts are only reachable direct (DashScope, Yahoo, FRED, AlphaVantage)
   and some only through the proxy (Reddit answers 429 direct, 200 proxied), so
   a single global choice cannot work. We route through the proxy by default and
   list the direct-only hosts in ``no_proxy``.

Hosts that no configuration can rescue (verified both paths): api.stocktwits.com
returns Cloudflare 403 direct *and* proxied, and gamma-api.polymarket.com times
out on both.
"""
import os
import socket
import ssl

import certifi

PROXY_HOST, PROXY_PORT = "127.0.0.1", 12888
PROXY = f"http://{PROXY_HOST}:{PROXY_PORT}"
LIGHTPROXY_CA = os.path.expanduser("~/.tradingagents/lightproxy-ca.pem")
CA_BUNDLE = os.path.expanduser("~/.tradingagents/ca-bundle.pem")

# Hosts that must NOT go through the proxy (they work direct and the proxy
# either blocks them or adds needless MITM surface).
DIRECT_HOSTS = [
    "dashscope.aliyuncs.com",
    "dashscope-intl.aliyuncs.com",
    ".yahoo.com",
    "api.stlouisfed.org",
    "www.alphavantage.co",
    # The egress Worker must be direct: LightProxy MITM does not forward the
    # X-Proxy-Key header, so proxied requests die with 403 forbidden while
    # direct requests are 200 (diagnosed 2026-08-19).
    "ta-egress-proxy.quasar-cymbal.workers.dev",
    "localhost",
    "127.0.0.1",
]

_original_create_default_context = ssl.create_default_context


def _lenient_create_default_context(*args, **kwargs):
    """Same as the stdlib default, minus RFC 5280 extension strictness."""
    if "cafile" not in kwargs and not args:
        kwargs["cafile"] = CA_BUNDLE if os.path.exists(CA_BUNDLE) else certifi.where()
    ctx = _original_create_default_context(*args, **kwargs)
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx


def _proxy_is_up(timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((PROXY_HOST, PROXY_PORT), timeout):
            return True
    except OSError:
        return False


def apply() -> dict:
    """Patch SSL + set proxy env vars. Returns a summary for logging."""
    ssl.create_default_context = _lenient_create_default_context
    ssl._create_default_https_context = _lenient_create_default_context
    urllib3_patched = _patch_urllib3()

    os.environ.setdefault("REQUESTS_CA_BUNDLE", CA_BUNDLE)
    os.environ.setdefault("SSL_CERT_FILE", CA_BUNDLE)

    if _proxy_is_up():
        os.environ["https_proxy"] = os.environ["HTTPS_PROXY"] = PROXY
        os.environ["http_proxy"] = os.environ["HTTP_PROXY"] = PROXY
        os.environ["no_proxy"] = os.environ["NO_PROXY"] = ",".join(DIRECT_HOSTS)
        mode, direct = "split (LightProxy up)", DIRECT_HOSTS
    else:
        for var in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY",
                    "all_proxy", "ALL_PROXY"):
            os.environ.pop(var, None)
        os.environ["no_proxy"] = os.environ["NO_PROXY"] = "*"
        mode, direct = "all-direct (LightProxy down)", ["*"]

    return {"mode": mode, "proxy": PROXY if _proxy_is_up() else None,
            "direct": direct, "ca_bundle": CA_BUNDLE,
            "strict_x509": False, "urllib3_patched": urllib3_patched}


def _patch_urllib3() -> bool:
    """urllib3 2.x builds its own context and ORs in VERIFY_X509_STRICT, so
    patching ``ssl.create_default_context`` does not reach ``requests``."""
    try:
        import urllib3.util.ssl_ as u3ssl
    except ImportError:
        return False

    original = u3ssl.create_urllib3_context

    def lenient(*args, **kwargs):
        ctx = original(*args, **kwargs)
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        return ctx

    u3ssl.create_urllib3_context = lenient
    for module_name in ("urllib3.connection", "urllib3.util"):
        try:
            module = __import__(module_name, fromlist=["create_urllib3_context"])
        except ImportError:
            continue
        if hasattr(module, "create_urllib3_context"):
            module.create_urllib3_context = lenient
    return True


if __name__ == "__main__":
    import json
    print(json.dumps(apply(), indent=2, ensure_ascii=False))
