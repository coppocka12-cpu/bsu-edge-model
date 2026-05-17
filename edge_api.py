#!/usr/bin/env python3
"""
BSU EDGE Model — Schedule API Server
Scrapes NCAA softball schedules and RPI rankings for portal player lookups.

Run once in Terminal:
    cd "/Users/anthonycoppock/Documents/Bronco Pro Edge"
    pip3 install flask requests beautifulsoup4
    python3 edge_api.py

API at http://localhost:7424
Endpoints:
  /api/schedule?school=Oregon+State   → {totalGames, t100Games, t100Opponents}
  /api/health                          → {status: 'ok'}
"""
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

# Common D1 softball abbreviations → normalized name
ABBREV = {
    'lsu':          'louisiana state',
    'ole miss':     'mississippi',
    'unlv':         'nevada las vegas',
    'uconn':        'connecticut',
    'ucf':          'central florida',
    'usf':          'south florida',
    'fiu':          'florida international',
    'fau':          'florida atlantic',
    'utsa':         'texas san antonio',
    'utep':         'texas el paso',
    'uab':          'alabama birmingham',
    'smu':          'southern methodist',
    'tcu':          'texas christian',
    'vcu':          'virginia commonwealth',
    'unc':          'north carolina',
    'ncsu':         'north carolina state',
    'nc state':     'north carolina state',
    'psu':          'penn state',
    'fsu':          'florida state',
    'wku':          'western kentucky',
    'eku':          'eastern kentucky',
    'wvu':          'west virginia',
    'vt':           'virginia tech',
    'usc':          'southern california',
    'ucla':         'california los angeles',
    'ucsb':         'california santa barbara',
    'cal poly':     'california polytechnic san luis obispo',
    'cal poly slo': 'california polytechnic san luis obispo',
    'cal poly pomona': 'california polytechnic pomona',
    'slo':          'california polytechnic san luis obispo',
    'usd':          'san diego',
    'sdsu':         'san diego state',
    'sjsu':         'san jose state',
    'csun':         'cal state northridge',
    'csuf':         'cal state fullerton',
    'csulb':        'cal state long beach',
    'lbsu':         'cal state long beach',
    'tamu':         'texas a&m',
    'a&m':          'texas a&m',
}

STOP_WORDS = {'university', 'college', 'of', 'the', 'at', 'a&m', 'am', '&'}


def normalize(name):
    """Lower-case, strip punctuation, remove stop-words for fuzzy matching."""
    name = name.lower().strip()
    name = re.sub(r'\bat\b', '', name)         # "at Oregon State" → "Oregon State"
    name = re.sub(r'[^\w\s]', ' ', name)       # punctuation → space
    name = re.sub(r'\s+', ' ', name).strip()
    for abbr, full in ABBREV.items():
        if name == abbr or name.startswith(abbr + ' '):
            name = name.replace(abbr, full, 1)
            break
    # Strip stop words for comparison only
    parts = [w for w in name.split() if w not in STOP_WORDS]
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
    # Word overlap (at least 2 shared meaningful words, covering ≥60% of shorter name)
    wa = set(na.split()) - {'', 'st'}
    wb = set(nb.split()) - {'', 'st'}
    if wa and wb:
        overlap = wa & wb
        shorter = min(len(wa), len(wb))
        if len(overlap) >= 2 and len(overlap) >= shorter * 0.6:
            return True
    return False


def fetch_top100():
    """Return list of Top-100 RPI team names from NCAA.com. Cached for the session."""
    if 'top100' in _cache:
        return _cache['top100']

    teams = []
    url = 'https://www.ncaa.com/rankings/softball/d1/ncaa-womens-softball-rpi'

    try:
        r = HTTP.get(url, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')

        table = soup.find('table')
        if not table:
            print('[RPI] No table found on NCAA.com rankings page')
        else:
            rows = table.find_all('tr')
            for row in rows[1:]:           # skip header row
                if len(teams) >= 100:
                    break
                cells = row.find_all('td')
                if len(cells) >= 2:
                    # col0 = rank (digit), col1 = school name
                    name = cells[1].get_text(strip=True)
                    if name:
                        teams.append(name)

        print(f'[RPI] NCAA.com: {len(teams)} Top-100 teams loaded')
    except Exception as e:
        print(f'[RPI] NCAA.com error: {e}')

    _cache['top100'] = teams
    return teams


def find_ncaa_team_id(school):
    """Return NCAA team ID string for a D1 softball school, or None."""
    key = f'tid_{school}'
    if key in _cache:
        return _cache[key]

    try:
        r = HTTP.get(
            'https://stats.ncaa.org/teams/search',
            params={'q': school, 'sport_code': 'WSB', 'division': '1'},
            timeout=15
        )
        soup = BeautifulSoup(r.text, 'html.parser')
        for link in soup.find_all('a', href=re.compile(r'/teams/\d+')):
            m = re.search(r'/teams/(\d+)', link['href'])
            if m:
                tid = m.group(1)
                print(f'[Team] "{school}" → ID {tid}')
                _cache[key] = tid
                return tid
    except Exception as e:
        print(f'[Team] Search error: {e}')

    return None


def fetch_schedule(team_id):
    """Return (opponents:list[str], total_played:int) for a team ID."""
    key = f'sched_{team_id}'
    if key in _cache:
        return _cache[key]

    opponents = []
    total = 0

    try:
        r = HTTP.get(f'https://stats.ncaa.org/teams/{team_id}/schedule', timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')

        for row in soup.select('table tbody tr'):
            cells = row.find_all('td')
            if len(cells) < 2:
                continue

            # A completed game has a W/L result somewhere in the row
            result = ''
            for cell in cells:
                t = cell.get_text(strip=True)
                if re.match(r'^[WwLl]\s*\d', t) or t in ('W', 'L', 'w', 'l'):
                    result = t
                    break
            if not result:
                continue

            # Find opponent name — skip date-shaped cells, skip result cell
            opp = ''
            for cell in cells:
                t = cell.get_text(strip=True)
                if re.match(r'^\d{1,2}/\d{1,2}', t):   # date cell like "03/14"
                    continue
                if re.match(r'^[WwLl]\s*[\d-]', t):     # result cell
                    continue
                if t and len(t) > 2:
                    link = cell.find('a')
                    opp = link.get_text(strip=True) if link else t
                    opp = re.sub(r'^[@#*\s]+', '', opp).strip()
                    if len(opp) > 2:
                        break

            if opp:
                opponents.append(opp)
                total += 1

        print(f'[Schedule] Team {team_id}: {total} completed games')
    except Exception as e:
        print(f'[Schedule] Error: {e}')

    result = (opponents, total)
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
        return jsonify({'error': 'Could not load RPI rankings from Warren Nolan. Check your internet connection.'}), 503

    team_id = find_ncaa_team_id(school)
    if not team_id:
        return jsonify({'error': f'"{school}" not found in NCAA D1 softball. Try the full official school name (e.g. "Oregon State", "Arizona State").'}), 404

    opponents, total = fetch_schedule(team_id)
    if total == 0:
        return jsonify({'error': f'No completed 2026 games found for "{school}". Schedule data may not be available yet.'}), 404

    t100_games = 0
    t100_opps = []
    for opp in opponents:
        for t100 in top100:
            if teams_match(opp, t100):
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
    import os
    port = int(os.environ.get('PORT', 7424))
    print('=' * 52)
    print(f'  BSU EDGE Schedule API  →  port {port}')
    print('  /api/schedule?school=Oregon+State')
    print('  /api/health')
    print('=' * 52)
    app.run(host='0.0.0.0', port=port)
