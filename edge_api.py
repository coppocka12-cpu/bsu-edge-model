#!/usr/bin/env python3
"""
BSU EDGE Model — Schedule API Server
Uses Warren Nolan for Top-100 RPI list and team schedules.
"""
import os
import re
from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}

HTTP = requests.Session()
HTTP.headers.update(HEADERS)

_cache = {}


def slug_from_name(name):
    """Convert a school name to a Warren Nolan URL slug. e.g. 'Oregon State' → 'Oregon-State'"""
    return '-'.join(w.capitalize() for w in name.strip().split())


def name_from_slug(slug):
    """Convert a WN slug back to a display name. e.g. 'Oregon-State' → 'Oregon State'"""
    return slug.replace('-', ' ')


def normalize(name):
    """Lowercase, strip punctuation/stop-words for fuzzy matching."""
    name = name.lower().strip()
    name = re.sub(r'\bat\b', '', name)
    name = re.sub(r'[^\w\s]', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    stop = {'university', 'college', 'of', 'the', 'at', 'a'}
    parts = [w for w in name.split() if w not in stop]
    return ' '.join(parts)


def teams_match(a, b):
    """True if two school names are plausibly the same team."""
    if a.lower().strip() == b.lower().strip():
        return True
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return True
    if len(na) > 4 and na in nb:
        return True
    if len(nb) > 4 and nb in na:
        return True
    wa = set(na.split()) - {'', 'st'}
    wb = set(nb.split()) - {'', 'st'}
    if wa and wb:
        overlap = wa & wb
        shorter = min(len(wa), len(wb))
        if len(overlap) >= 2 and len(overlap) >= shorter * 0.6:
            return True
    return False


def fetch_top100():
    """Return list of (slug, display_name) for Top-100 RPI teams from Warren Nolan."""
    if 'top100' in _cache:
        return _cache['top100']

    teams = []
    url = 'https://www.warrennolan.com/softball/2026/rpi-live'
    try:
        r = HTTP.get(url, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        table = soup.find('table', class_='stats-table')
        if not table:
            print('[RPI] No stats-table found on Warren Nolan')
        else:
            rows = table.find_all('tr')
            for row in rows[1:]:
                if len(teams) >= 100:
                    break
                # rank is in first td
                cells = row.find_all('td')
                if not cells:
                    continue
                rank_text = cells[0].get_text(strip=True)
                if not rank_text.isdigit():
                    continue
                # find schedule link to get clean slug
                link = row.find('a', href=re.compile(r'/softball/2026/schedule/'))
                if link:
                    slug = link['href'].split('/')[-1]
                    display = name_from_slug(slug)
                    teams.append((slug, display))

        print(f'[RPI] Warren Nolan: {len(teams)} Top-100 teams loaded')
    except Exception as e:
        print(f'[RPI] Error: {e}')

    _cache['top100'] = teams
    return teams


def find_wn_slug(school):
    """
    Find the Warren Nolan schedule slug for a given school name.
    First tries the direct slug, then falls back to fuzzy-matching against the full team list.
    """
    key = f'slug_{school}'
    if key in _cache:
        return _cache[key]

    # Try direct conversion first
    direct_slug = slug_from_name(school)
    url = f'https://www.warrennolan.com/softball/2026/schedule/{direct_slug}'
    try:
        r = HTTP.get(url, timeout=15)
        if r.status_code == 200 and 'schedule' in r.text.lower():
            print(f'[Team] "{school}" → direct slug "{direct_slug}"')
            _cache[key] = direct_slug
            return direct_slug
    except Exception:
        pass

    # Fall back: search the teams-az page for a fuzzy match
    try:
        r = HTTP.get('https://www.warrennolan.com/softball/2026/teams-az', timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        links = soup.find_all('a', href=re.compile(r'/softball/2026/schedule/'))
        for link in links:
            slug = link['href'].split('/')[-1]
            candidate = name_from_slug(slug)
            if teams_match(school, candidate):
                print(f'[Team] "{school}" → fuzzy match "{slug}"')
                _cache[key] = slug
                return slug
    except Exception as e:
        print(f'[Team] Fuzzy search error: {e}')

    print(f'[Team] "{school}" not found')
    _cache[key] = None
    return None


def fetch_schedule(slug):
    """Return (opponents: list[str], total_played: int) for a Warren Nolan team slug."""
    key = f'sched_{slug}'
    if key in _cache:
        return _cache[key]

    opponents = []
    url = f'https://www.warrennolan.com/softball/2026/schedule/{slug}'
    try:
        r = HTTP.get(url, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')

        # Opponent links are schedule links to other teams
        seen = set()
        opp_links = soup.find_all('a', href=re.compile(r'/softball/2026/schedule/'))
        for link in opp_links:
            opp_slug = link['href'].split('/')[-1]
            if opp_slug.lower() == slug.lower():
                continue
            if opp_slug in seen:
                continue
            seen.add(opp_slug)
            opponents.append(name_from_slug(opp_slug))

        print(f'[Schedule] {slug}: {len(opponents)} opponents found')
    except Exception as e:
        print(f'[Schedule] Error for {slug}: {e}')

    result = (opponents, len(opponents))
    _cache[key] = result
    return result


# ── CORS ──────────────────────────────────────────────────────────────────────
@app.after_request
def add_cors(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return resp


# ── ROUTES ────────────────────────────────────────────────────────────────────
@app.route('/api/schedule')
def api_schedule():
    school = request.args.get('school', '').strip()
    if not school:
        return jsonify({'error': 'Provide ?school=School+Name'}), 400

    top100 = fetch_top100()
    if not top100:
        return jsonify({'error': 'Could not load RPI rankings from Warren Nolan.'}), 503

    slug = find_wn_slug(school)
    if not slug:
        return jsonify({'error': f'"{school}" not found. Try the full official school name (e.g. "Oregon State", "Arizona State").'}), 404

    opponents, total = fetch_schedule(slug)
    if total == 0:
        return jsonify({'error': f'No completed games found for "{school}". Schedule may not be available yet.'}), 404

    t100_games = 0
    t100_opps = []
    for opp in opponents:
        for t100_slug, t100_name in top100:
            if teams_match(opp, t100_name):
                t100_games += 1
                t100_opps.append(opp)
                break

    return jsonify({
        'school': school,
        'totalGames': total,
        't100Games': t100_games,
        't100Opponents': t100_opps,
        'rpiSourceCount': len(top100)
    })


@app.route('/api/health')
def health():
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 7424))
    print('=' * 52)
    print(f'  BSU EDGE Schedule API  →  port {port}')
    print('  /api/schedule?school=Oregon+State')
    print('  /api/health')
    print('=' * 52)
    app.run(host='0.0.0.0', port=port)
