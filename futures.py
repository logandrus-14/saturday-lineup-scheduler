#!/usr/bin/env python3
"""Futures for Fumbling — the national champion, conference winners, the
Heisman, the Super Bowl, divisions, MVP.

Logan, Oct 4 2026: "I would also like futures on the board so it isn't
empty every week." Pretend bets only, like the rest of Fumbling.

SOURCE: ESPN's core API carries DraftKings futures for both leagues, free
and keyless — the same unofficial family of endpoints as nfl.py, so the
same rule: fail quietly, never cost the college cache anything.

Each market's options name a TEAM or an ATHLETE by reference. Team names
come from one teams listing per league; athletes are fetched one by one,
but only the ones not already named in the last cached copy, so a run
after the first costs a handful of requests.

Written to cache/futures_ncaaf and cache/futures_nfl as one JSON string:
    {"season": 2026, "markets": [{"id", "name", "kind",
                                  "options": [{"id", "name", "price"}]}]}
"""
import json
import re
import urllib.request

CORE = "https://sports.core.api.espn.com/v2/sports/{sport}/leagues"
SITE = "https://site.api.espn.com/apis/site/v2/sports/{sport}"
LEAGUES = {"ncaaf": "college-football", "nfl": "nfl", "nba": "nba"}
# The NBA (Oct 5 2026) is ESPN's basketball, not football.
SPORTS = {"ncaaf": "football", "nfl": "football", "nba": "basketball"}


def _core(league):
    return CORE.format(sport=SPORTS[league])


def _site(league):
    return SITE.format(sport=SPORTS[league])


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "saturday-lineup"})
    return json.load(urllib.request.urlopen(req, timeout=20))


def _price(v):
    s = str(v or "").strip().upper()
    if s in ("EVEN", "EV"):
        return 100
    try:
        return int(float(s.replace("+", "")))
    except ValueError:
        return None


def _ref_id(ref):
    m = re.search(r"/(teams|athletes)/(\d+)", ref or "")
    return (m.group(1), m.group(2)) if m else (None, None)


# The book's names read like a ticket printer ("Pro Football (A) South
# Division - Winner"). These are what a person would call them.
_CONFERENCES = {
    "Atlantic Coast Conference": "ACC",
    "American Athletic Conference": "American",
    "Big 12 Conference": "Big 12",
    "Big Ten Conference": "Big Ten",
    "Conference USA": "Conference USA",
    "Mid-American Conference": "MAC",
    "Mountain West Conference": "Mountain West",
    "Southeastern Conference": "SEC",
    "Sun Belt Conference": "Sun Belt",
    "Pac-12 Conference": "Pac-12",
}


def market_name(raw, league):
    """A readable name, or None for a market Fumbling leaves out."""
    n = " ".join((raw or "").split())
    if "FCS" in n:
        return None  # the app is FBS only
    if league == "ncaaf":
        if re.search(r"Championship$", n) and "Conference" not in n:
            return "National Champion"
        if "Semifinal" in n:
            return "Reach the Playoff Semifinals"
        if "Heisman" in n:
            return "Heisman Trophy"
        for long, short in _CONFERENCES.items():
            if long in n:
                return f"{short} Champion"
        return None
    if league == "nba":
        return _nba_market(n)
    if "Super Bowl" in n:
        return "Super Bowl Champion"
    if "MVP" in n:
        return "NFL MVP"
    conf = "AFC" if "(A)" in n else "NFC" if "(N)" in n else None
    if conf is None:
        return None
    for d in ("North", "South", "East", "West"):
        if f"{d} Division" in n:
            return f"{conf} {d}"
    if "Conference" in n:
        return f"{conf} Champion"
    return None


def _nba_market(n):
    """NBA markets worth a board: the title, each conference, the two
    awards people argue about, and the divisions. The in-season tournament
    groups and the smaller awards are left out."""
    if n == "NBA - Winner":
        return "NBA Champion"
    if "In-Season" in n:
        return None
    if "Eastern Conference - Winner" in n:
        return "East Champion"
    if "Western Conference - Winner" in n:
        return "West Champion"
    if "Regular Season MVP" in n:
        return "NBA MVP"
    if "Rookie of the Year" in n:
        return "Rookie of the Year"
    m = re.search(r"(Atlantic|Central|Southeast|Northwest|Pacific|Southwest) "
                  r"Division", n)
    if m:
        return f"{m.group(1)} Division"
    return None


# Order on the board: the big one first.
_RANK = ["National Champion", "Super Bowl Champion", "NBA Champion",
         "Reach the Playoff Semifinals", "Heisman Trophy", "NFL MVP",
         "AFC Champion", "NFC Champion", "NBA MVP", "East Champion",
         "West Champion", "Rookie of the Year"]


def _rank(name):
    return _RANK.index(name) if name in _RANK else len(_RANK)


def team_names(league):
    """team id -> the name the app uses: the SCHOOL for college ("Ohio
    State"), the full name for the NFL and NBA ("Buffalo Bills") — the names
    the games on the board carry."""
    data = _get(f"{_site(league)}/{LEAGUES[league]}/teams?limit=1000")
    out = {}
    for t in data["sports"][0]["leagues"][0]["teams"]:
        t = t["team"]
        out[str(t["id"])] = (t.get("location") if league == "ncaaf"
                             else t.get("displayName")) or t.get("displayName")
    return out


def fetch(league, season, prior=None):
    """Every market for [league], options named and priced, shortest odds
    first. [prior] is the last cached copy, whose athlete names are reused."""
    known = {}
    for m in (prior or {}).get("markets", []):
        for o in m.get("options", []):
            known[o["id"]] = o["name"]

    listing = _get(f"{_core(league)}/{LEAGUES[league]}/seasons/{season}/futures?limit=100")
    teams = None
    markets = []
    for item in listing.get("items", []):
        name = market_name(item.get("name"), league)
        if not name:
            continue
        books = ((item.get("futures") or [{}])[0]).get("books") or []
        options = []
        for b in books:
            price = _price(b.get("value"))
            ref = (b.get("team") or b.get("athlete") or {}).get("$ref")
            kind, rid = _ref_id(ref)
            if price is None or not rid:
                continue
            oid = f"{kind[0]}{rid}"  # t194 / a5079720
            if oid not in known:
                if kind == "teams":
                    teams = teams or team_names(league)
                    known[oid] = teams.get(rid)
                else:
                    try:
                        known[oid] = _get(ref).get("displayName")
                    except Exception:
                        known[oid] = None
            if not known.get(oid):
                continue
            options.append({"id": oid, "name": known[oid], "price": price})
        if not options:
            continue
        options.sort(key=lambda o: (-o["price"] if o["price"] < 0 else 100000 + o["price"]))
        markets.append({"id": str(item.get("id")), "name": name,
                        "kind": "athlete" if any(o["id"].startswith("a") for o in options)
                        else "team",
                        "options": options})
    markets.sort(key=lambda m: (_rank(m["name"]), m["name"]))
    return {"season": season, "markets": markets}
