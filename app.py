import time
import requests
from flask import Flask, request, redirect, abort, jsonify
from cachetools import TTLCache
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from threading import Lock

app = Flask(__name__)

# --------------------- Configuration ---------------------
TOKEN_CACHE = TTLCache(maxsize=1, ttl=300)          # 5 minutes
URL_CACHE = TTLCache(maxsize=1, ttl=80)             # 80 seconds

CATCHUP_PARAMS_CACHE = TTLCache(maxsize=1, ttl=86400)   # 24 hours
CATCHUP_URL_CACHE = TTLCache(maxsize=10, ttl=80)        # 80 seconds

TOKEN_URL = "https://raw.githubusercontent.com/Ayush8481Lab/Sar/refs/heads/main/app/data/access.json"
DATA_URL = "https://ayushdatademo.onrender.com/app/live.php?id=173"

ORIGINAL_DOMAIN = "jiotvbpkmob.cdn.jio.com"
NEW_DOMAIN = "jiotvmblive.cdn.jio.com"

CATCHUP_API_URL = "https://ayushdatademo.onrender.com/app/catchup/cppapi.php"

# Rate limiting
RATE_LIMIT = 20
RATE_WINDOW = 86400  # 24 hours in seconds
rate_lock = Lock()
token_requests = defaultdict(list)   # token -> list of timestamps

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
        return TOKEN_CACHE.get('tokens', [])

def get_cached_url():
    """Return cached live stream URL if still valid."""
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
            raise ValueError("Missing 'original_url' in response")
        new_url = original_url.replace(ORIGINAL_DOMAIN, NEW_DOMAIN)
        URL_CACHE['url_data'] = {
            'original_url': new_url,
            'fetched_at': time.time()
        }
        return new_url
    except Exception as e:
        print(f"Upstream fetch error: {e}")
        raise

def check_rate_limit(token):
    """Check and record a request for the given token."""
    now = time.time()
    with rate_lock:
        # Remove timestamps older than 24h
        token_requests[token] = [t for t in token_requests[token] if now - t < RATE_WINDOW]
        if len(token_requests[token]) >= RATE_LIMIT:
            return False
        token_requests[token].append(now)
        return True

def get_catchup_params():
    """
    Generate catchup parameters based on current IST date.
    - srno: YYMMDD (current IST date) + "173000"
    - begin: YYYYMMDD (previous day) + "T183000"
    - end:   YYYYMMDD (previous day) + "T184000"
    Cached for 24 hours.
    """
    if 'params' in CATCHUP_PARAMS_CACHE:
        return CATCHUP_PARAMS_CACHE['params']

    # Get current UTC time
    now_utc = datetime.now(timezone.utc)
    # Convert to IST (UTC+5:30)
    now_ist = now_utc + timedelta(hours=5, minutes=30)

    # Current IST date
    curr_date = now_ist.date()
    # Previous day (for begin/end)
    prev_date = curr_date - timedelta(days=1)

    # Format srno: YYMMDD + "173000"
    srno = curr_date.strftime("%y%m%d") + "173000"

    # Format begin and end: YYYYMMDD + fixed times
    begin = prev_date.strftime("%Y%m%d") + "T183000"
    end   = prev_date.strftime("%Y%m%d") + "T184000"

    params = {
        'srno': srno,
        'begin': begin,
        'end': end
    }

    CATCHUP_PARAMS_CACHE['params'] = params
    return params

def get_catchup_url(params):
    """Get cached catchup URL for given params or fetch from API."""
    cache_key = f"{params['srno']}_{params['begin']}_{params['end']}"
    if cache_key in CATCHUP_URL_CACHE:
        return CATCHUP_URL_CACHE[cache_key]

    try:
        url = f"{CATCHUP_API_URL}?id=173&srno={params['srno']}&begin={params['begin']}&end={params['end']}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        catchup_url = resp.text.strip()
        if not catchup_url:
            raise ValueError("Empty catchup URL received")
        CATCHUP_URL_CACHE[cache_key] = catchup_url
        return catchup_url
    except Exception as e:
        print(f"Catchup API fetch error: {e}")
        raise

# --------------------- Routes ---------------------
@app.route('/authorization/<token>', methods=['GET'])
def handle_request(token):
    # 1. Validate token
    valid_tokens = get_valid_tokens()
    if token not in valid_tokens:
        abort(403, description="Invalid token")

    # 2. Rate limit check
    if not check_rate_limit(token):
        abort(429, description="Rate limit exceeded (20/day)")

    # 3. Get streaming URL (cached or fresh)
    try:
        video_url = get_cached_url()
        if not video_url:
            print("Cache miss, fetching from upstream...")
            video_url = fetch_and_cache_url()
    except Exception as e:
        abort(503, description=f"Unable to obtain stream URL: {str(e)}")

    # 4. Redirect
    response = redirect(video_url, code=302)
    response.headers['Cache-Control'] = 'public, max-age=80'
    return response

@app.route('/Catchupauth/<token>', methods=['GET'])
def handle_catchup(token):
    # 1. Validate token
    valid_tokens = get_valid_tokens()
    if token not in valid_tokens:
        abort(403, description="Invalid token")

    # 2. Rate limit check
    if not check_rate_limit(token):
        abort(429, description="Rate limit exceeded (20/day)")

    # 3. Get catchup parameters (automatically generated)
    params = get_catchup_params()

    # 4. Get final catchup URL
    try:
        catchup_url = get_catchup_url(params)
    except Exception as e:
        abort(503, description=f"Unable to obtain catchup URL: {str(e)}")

    # 5. Redirect
    response = redirect(catchup_url, code=302)
    response.headers['Cache-Control'] = 'public, max-age=80'
    return response

# --------------------- Health check ---------------------
@app.route('/')
@app.route('/health')
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
