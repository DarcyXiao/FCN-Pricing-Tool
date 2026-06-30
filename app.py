"""
FCN Pricing Simulation Tool — Flask Backend

Fetches historical stock prices from Yahoo Finance v8 chart API.
"""
import logging
import os
import sys
import time
import threading
import webbrowser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# Handle PyInstaller bundled paths
if getattr(sys, "frozen", False):
    _BASE_DIR = sys._MEIPASS
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=_BASE_DIR)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rate-limit guard — serialise + space out requests
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_last_call = 0.0
_MIN_SPACING = 2.0  # seconds between requests


# ---------------------------------------------------------------------------
# Session factory — full browser impersonation
# ---------------------------------------------------------------------------

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
]

# Alternate Yahoo Finance query hosts to try on failure
_QUERY_HOSTS = [
    "https://query1.finance.yahoo.com",
    "https://query2.finance.yahoo.com",
]


def _build_session(attempt: int = 0) -> requests.Session:
    """Create a requests.Session that looks like a real browser."""
    s = requests.Session()

    # Browser-like headers
    ua = _USER_AGENTS[attempt % len(_USER_AGENTS)]
    s.headers.update({
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    })

    # Retry adapter for transient network errors
    retry_strategy = Retry(
        total=2,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=1,
        pool_maxsize=1,
    )
    s.mount("https://", adapter)

    return s


# ---------------------------------------------------------------------------
# Yahoo Finance fetch  (raw v8 chart API — same backend yfinance uses)
# ---------------------------------------------------------------------------

_RANGE_MAP = {
    "1mo": "1mo", "3mo": "3mo", "6mo": "6mo",
    "1y": "1y", "2y": "2y", "5y": "5y", "max": "max",
}


def _fetch_from_yahoo(
    ticker: str,
    period: str = "1y",
    max_retries: int = 4,
):
    """Fetch historical prices from Yahoo Finance v8 chart API.

    Returns dict with keys: ticker, prices, count.
    """
    global _last_call

    yf_range = _RANGE_MAP.get(period, "1y")

    last_error = None

    for attempt in range(max_retries):
        # ---- rate-limit gate (per attempt, not strictly needed but safe) ----
        with _lock:
            elapsed = time.time() - _last_call
            if elapsed < _MIN_SPACING:
                wait = _MIN_SPACING - elapsed
                logger.info("Throttling: %.1fs", wait)
                time.sleep(wait)
            _last_call = time.time()

        # Rotate hosts on retry
        host = _QUERY_HOSTS[attempt % len(_QUERY_HOSTS)]
        url = f"{host}/v8/finance/chart/{ticker}"
        params = {
            "range": yf_range,
            "interval": "1d",
            "includePrePost": "false",
            "events": "div,splits",
        }

        session = _build_session(attempt)

        try:
            logger.info("Fetching %s (%s) via %s, attempt %d/%d",
                        ticker, yf_range, host, attempt + 1, max_retries)

            resp = session.get(url, params=params, timeout=(10, 30))
            logger.info("HTTP %d for %s", resp.status_code, ticker)

            if resp.status_code == 429:
                raise RuntimeError("Rate limited by Yahoo Finance (HTTP 429)")

            if resp.status_code == 404:
                raise ValueError(f"Ticker '{ticker}' not found on Yahoo Finance")

            if resp.status_code != 200:
                raise RuntimeError(
                    f"Yahoo Finance returned HTTP {resp.status_code}"
                )

            data = resp.json()

            # Parse the nested chart response
            result = data.get("chart", {}).get("result", [])
            if not result:
                raise ValueError(f"No chart data for '{ticker}'")

            meta = result[0]
            timestamps = meta.get("timestamp", [])
            indicators = meta.get("indicators", {})

            # Try adjclose first, fall back to close
            adjclose_data = indicators.get("adjclose", [{}])[0]
            if adjclose_data:
                adjclose = adjclose_data.get("adjclose", [])
            else:
                quote_data = indicators.get("quote", [{}])[0]
                adjclose = quote_data.get("close", [])

            if not timestamps or not adjclose:
                raise ValueError(f"Empty price series for '{ticker}'")

            prices = [float(p) for p in adjclose if p is not None]

            if len(prices) < 5:
                raise ValueError(f"Only {len(prices)} data points for '{ticker}'")

            logger.info("Returned %d price points for %s", len(prices), ticker)
            return {"ticker": ticker, "prices": prices, "count": len(prices)}

        except (requests.ConnectionError, ConnectionError, OSError) as e:
            # Network-level errors: connection reset, timeout, DNS, etc.
            last_error = e
            wait = (2 ** attempt) * 4  # 4, 8, 16, 32 s
            logger.warning(
                "Attempt %d network error for %s: %s — retrying in %ds",
                attempt + 1, ticker, e, wait,
            )
            time.sleep(wait)

        except Exception as e:
            last_error = e
            msg = str(e).lower()
            is_rate_limit = any(
                phrase in msg for phrase in [
                    "rate limit", "too many", "429", "try after",
                ]
            )
            if attempt < max_retries - 1:
                wait = (2 ** attempt) * 3
                if is_rate_limit:
                    wait = max(wait, 15)
                logger.warning(
                    "Attempt %d failed for %s: %s — retrying in %ds",
                    attempt + 1, ticker, e, wait,
                )
                time.sleep(wait)
            else:
                logger.error("All %d attempts failed for %s", max_retries, ticker)

    raise last_error


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(_BASE_DIR, "index.html")


@app.route("/docs")
def docs():
    return send_from_directory(_BASE_DIR, "docs.html")


@app.route("/api/history")
def history():
    ticker = request.args.get("ticker", "").strip()
    period = request.args.get("period", "1y").strip()

    if not ticker:
        return jsonify({"error": "Missing ticker parameter"}), 400

    try:
        result = _fetch_from_yahoo(ticker, period)
        return jsonify(result)
    except Exception as e:
        logger.exception("Error fetching %s", ticker)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    host = "127.0.0.1"
    port = 5000
    url = f"http://{host}:{port}"
    print(f"Starting FCN Pricing Simulation Tool server...")
    print(f"Open {url} in your browser")
    # Auto-open browser (after a short delay so Flask is ready)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=host, port=port, debug=False)
