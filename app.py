import time
import requests
from flask import Flask, request, redirect, abort, jsonify
from cachetools import TTLCache

app = Flask(__name__)

# --------------------- Configuration ---------------------
TOKEN_CACHE = TTLCache(maxsize=1, ttl=300)          # 5 minutes
URL_CACHE = TTLCache(maxsize=1, ttl=80)             # 80 seconds

TOKEN_URL = "https://raw.githubusercontent.com/Ayush8481Lab/Sar/refs/heads/main/app/data/access.json"
DATA_URL = "https://ayushdatademo.onrender.com/app/live.php?id=173"

ORIGINAL_DOMAIN = "jiotvbpkmob.cdn.jio.com"
NEW_DOMAIN = "jiotvmblive.cdn.jio.com"

# --------------------- Helper functions ---------------------
def get_valid_tokens():
    """Fetch token list from remote and cache it."""
    if 'tokens' in TOKEN_CACHE:
        return TOKEN_CACHE['tokens']

    try:
        resp = requests.get(TOKEN_URL, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        tokens = data.get('tokens', [])
        TOKEN_CACHE['tokens'] = tokens
        return tokens
    except Exception as e:
        print(f"Token fetch error: {e}")
        # Return stale cache if available
        return TOKEN_CACHE.get('tokens', [])

def get_cached_url():
    """Return cached URL if still valid."""
    if 'url_data' in URL_CACHE:
        return URL_CACHE['url_data'].get('original_url')
    return None

def fetch_and_cache_url():
    """Call upstream API, replace domain, and cache result."""
    try:
        resp = requests.get(DATA_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        original_url = data.get('original_url')
        if not original_url:
            print("Missing 'original_url' in upstream response")
            return None

        new_url = original_url.replace(ORIGINAL_DOMAIN, NEW_DOMAIN)
        URL_CACHE['url_data'] = {
            'original_url': new_url,
            'fetched_at': time.time()
        }
        return new_url
    except Exception as e:
        print(f"Upstream fetch error: {e}")
        return None

# --------------------- Routes ---------------------
@app.route('/authorization/<token>', methods=['GET'])
def handle_request(token):
    # 1. Validate token from URL path
    valid_tokens = get_valid_tokens()
    if token not in valid_tokens:
        abort(403, description="Invalid token")

    # 2. Get streaming URL (cached or fresh)
    video_url = get_cached_url()
    if not video_url:
        print("Cache miss, fetching from upstream...")
        video_url = fetch_and_cache_url()

    if not video_url:
        abort(503, description="Unable to obtain stream URL")

    # 3. Redirect with proper caching headers for Render's edge cache
    response = redirect(video_url, code=302)
    # Override Render's default 20‑minute cache for 302 responses
    response.headers['Cache-Control'] = 'public, max-age=80'
    return response

# --------------------- Health check (for keep-alive) ---------------------
@app.route('/')
@app.route('/health')
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
