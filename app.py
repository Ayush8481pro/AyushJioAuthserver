import time
import requests
from flask import Flask, request, redirect, abort, jsonify
from cachetools import TTLCache
from datetime import datetime, timezone
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

EPG_API_URL = "https://jiotvapi.cdn.jio.com/apis/v1.3/getepg/get?channel_id=173&offset=0"
CATCHUP_API_URL = "https://ayushdatademo.onrender.com/app/catchup/cppapi.php"

RATE_LIMIT = 20
RATE_WINDOW = 86400
rate_lock = Lock()
token_requests = defaultdict(list)

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
    if 'url_data' in URL_CACHE:
        return URL_CACHE['url_data'].get('original_url')
    return None

def fetch_and_cache_url():
    try:
        resp = requests.get(DATA_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        original_url = data.get('original_url')
        if not original_url:
            raise ValueError("Missing 'original_url' in response")
        new_url = original_url.replace(ORIGINAL_DOMAIN, NEW_DOMAIN)
        URL_CACHE['url_data'] = {'original_url': new_url, 'fetched_at': time.time()}
        return new_url
    except Exception as e:
        print(f"Upstream fetch error: {e}")
        raise  # re-raise so route can catch and show error

def check_rate_limit(token):
    now = time.time()
    with rate_lock:
        token_requests[token] = [t for t in token_requests[token] if now - t < RATE_WINDOW]
        if len(token_requests[token]) >= RATE_LIMIT:
            return False
        token_requests[token].append(now)
        return True

def epoch_ms_to_utc_str(epoch_ms):
    dt_utc = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    return dt_utc.strftime("%Y%m%dT%H%M%S")

def get_catchup_params():
    """Fetch EPG data and return params for FIRST entry. Raises exception on failure."""
    if 'params' in CATCHUP_PARAMS_CACHE:
        return CATCHUP_PARAMS_CACHE['params']

    try:
        resp = requests.get(EPG_API_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        epg_list = data.get('epg', [])
        if not epg_list:
            raise ValueError("EPG response contains no entries")
        entry = epg_list[0]
        # Validate required fields
        if 'srno' not in entry or 'startEpoch' not in entry or 'endEpoch' not in entry:
            raise ValueError(f"Missing srno/startEpoch/endEpoch in first EPG entry: {entry}")
        params = {
            'srno': entry['srno'],
            'begin': epoch_ms_to_utc_str(entry['startEpoch']),
            'end': epoch_ms_to_utc_str(entry['endEpoch'])
        }
        CATCHUP_PARAMS_CACHE['params'] = params
        return params
    except Exception as e:
        print(f"EPG fetch/parse error: {e}")
        raise  # re-raise to be caught in route

def get_catchup_url(params):
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
    valid_tokens = get_valid_tokens()
    if token not in valid_tokens:
        abort(403, description="Invalid token")

    if not check_rate_limit(token):
        abort(429, description="Rate limit exceeded (20/day)")

    try:
        video_url = get_cached_url()
        if not video_url:
            print("Cache miss, fetching from upstream...")
            video_url = fetch_and_cache_url()
    except Exception as e:
        abort(503, description=f"Unable to obtain stream URL: {str(e)}")

    response = redirect(video_url, code=302)
    response.headers['Cache-Control'] = 'public, max-age=80'
    return response

@app.route('/Catchupauth/<token>', methods=['GET'])
def handle_catchup(token):
    valid_tokens = get_valid_tokens()
    if token not in valid_tokens:
        abort(403, description="Invalid token")

    if not check_rate_limit(token):
        abort(429, description="Rate limit exceeded (20/day)")

    # Get catchup parameters with detailed error
    try:
        params = get_catchup_params()
    except Exception as e:
        abort(503, description=f"Unable to obtain catchup parameters: {str(e)}")

    # Get catchup URL with detailed error
    try:
        catchup_url = get_catchup_url(params)
    except Exception as e:
        abort(503, description=f"Unable to obtain catchup URL: {str(e)}")

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
