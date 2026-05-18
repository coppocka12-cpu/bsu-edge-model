#!/usr/bin/env python3
"""
BSU EDGE Model — Schedule API Server
Uses Warren Nolan team sheets for accurate quadrant-based schedule data.
RPI rankings from 2026, schedule data from most recently completed season.
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


def normalize(name):
    """Lowercase, remove punctuation/stop-words for fuzzy matching."""
    name = name.lower().strip()
    name = re.sub(r'[^\w\s]', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    stop = {'university', 'college', 'of', 'the', 'at', 'a'}
    parts = [w for w in name.split() if w not in stop]
    return ' '.join(parts)


def teams_match(a, b):
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return True
    wa = set(na.split()) - {'', 'st'}
    wb = set(nb.split()) - {'', 'st'}
    if wa and wb:
        overlap = wa & wb
        shorter = min(len(wa), len(wb))
        longer = max(len(wa), len(wb))
        # Require ≥2 shared words covering most of both names (prevents Oregon vs Oregon State)
        if len(overlap) >= 2 and len(overlap) >= shorter * 0.8 and len(overlap) >= longer * 0.6:
            return True
    return False


def fetch_teamsheets(year):
    """Download and parse Warren Nolan team sheets for a given year.
    Returns dict: {normalized_name: {name, total, t100, q1, q2, q3, q4}}
    """
    key = f'teamsheets_{year}'
    if key in _cache:
        return _cache[key]

    url = f'https://www.warrennolan.com/softball/{year}/rpi-teamsheets'
    data = {}
    try:
        r = HTTP.get(url, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')

        # Each team's full data is in a div with id like "Oklahoma-full"
        full_divs = soup.find_all('div', id=re.compile(r'.+-full$'))
        print(f'[Teamsheets {year}] Found {len(full_divs)} team blocks')

        for div in full_divs:
            div_id = div.get('id', '')
            team_name = div_id[:-5].replace('-', ' ')  # "Oregon-State-full" → "Oregon State"

            text = div.get_text(separator='|', strip=True)

            def parse_record(pattern, t=text):
                m = re.search(pattern, t)
                if m:
                    return int(m.group(1)) + int(m.group(2))
                return 0

            # WN quadrant definitions: Q1=RPI 1-50, Q2=51-100, Q3=101-150, Q4=151+
            q1 = parse_record(r'QUADRANT 1\|Q1\|(\d+)-(\d+)')
            q2 = parse_record(r'QUADRANT 2\|Q2\|(\d+)-(\d+)')
            q3 = parse_record(r'QUADRANT 3\|Q3\|(\d+)-(\d+)')
            q4 = parse_record(r'QUADRANT 4\|Q4\|(\d+)-(\d+)')
            total = q1 + q2 + q3 + q4
            t100 = q1 + q2  # Top-100 RPI = Q1 (1-50) + Q2 (51-100)

            nkey = normalize(team_name)
            data[nkey] = {
                'name': team_name,
                'total': total,
                't100': t100,
                'q1': q1,
                'q2': q2,
                'q3': q3,
                'q4': q4,
            }

        print(f'[Teamsheets {year}] Parsed {len(data)} teams')
    except Exception as e:
        print(f'[Teamsheets {year}] Error: {e}')

    _cache[key] = data
    return data


def find_team_stats(school):
    """Find 2026 schedule stats for a school using Warren Nolan team sheets."""
    sheets = fetch_teamsheets(2026)
    nschool = normalize(school)
    # Exact normalized match first
    if nschool in sheets:
        entry = sheets[nschool].copy()
        entry['season'] = 2026
        return entry
    # Fuzzy match
    for nkey, entry in sheets.items():
        if teams_match(school, entry['name']):
            entry = entry.copy()
            entry['season'] = 2026
            return entry
    return None


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

    stats = find_team_stats(school)
    if not stats:
        return jsonify({'error': f'"{school}" not found. Try the full official school name (e.g. "Oregon State", "Oklahoma", "Arizona State").'}), 404

    if stats['total'] == 0:
        return jsonify({'error': f'No game data found for "{school}".'}), 404

    return jsonify({
        'school': school,
        'matchedName': stats['name'],
        'season': stats['season'],
        'totalGames': stats['total'],
        't100Games': stats['t100'],
        'q1Games': stats['q1'],
        'q2Games': stats['q2'],
        'q3Games': stats['q3'],
        'q4Games': stats['q4'],
    })


@app.route('/api/health')
def health():
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 7424))
    print('=' * 52)
    print(f'  BSU EDGE Schedule API  →  port {port}')
    print('  /api/schedule?school=Oklahoma')
    print('  /api/health')
    print('=' * 52)
    app.run(host='0.0.0.0', port=port)
