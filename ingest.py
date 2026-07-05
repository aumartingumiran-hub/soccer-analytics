"""
Soccer data ingestion: Highlightly Football API -> Supabase (Postgres)

Pulls fixtures, team stats, and player stats for a given league/season and
upserts them into the schema defined in soccer_schema.sql.

Setup:
    pip install requests psycopg2-binary python-dotenv

Environment variables (.env or exported):
    HIGHLIGHTLY_API_KEY -> your Highlightly API key from
                           https://highlightly.net/dashboard
    SUPABASE_DB_URL     -> full Postgres connection string from
                            Supabase > Project Settings > Database
                            (use the "Session pooler" URI, port 5432 or 6543)

Usage:
    python ingest.py --league 33973 --season 2025          # find league IDs via /leagues
    python ingest.py --league 33973 --season 2025 --match 489389   # single match
"""

import os
import argparse
import time
import requests
import psycopg2
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["HIGHLIGHTLY_API_KEY"]
DB_URL = os.environ["SUPABASE_DB_URL"]

BASE_URL = "https://soccer.highlightly.net"
HEADERS = {"x-rapidapi-key": API_KEY}

REQUEST_DELAY = 0.9  # free tier: 100 requests/day, no per-second cap, but be polite


def api_get(endpoint, params=None):
    resp = requests.get(f"{BASE_URL}/{endpoint}", headers=HEADERS, params=params or {})
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY)
    data = resp.json()
    # Highlightly wraps list endpoints in {"data": [...], "pagination": ..., "plan": ...}
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


# ---------------------------------------------------------------------------
# Upsert helpers (same schema as soccer_schema.sql)
# ---------------------------------------------------------------------------

def upsert_team(cur, team):
    cur.execute(
        """
        insert into teams (id, name, short_name, league, country, logo_url)
        values (%(id)s, %(name)s, %(short_name)s, %(league)s, %(country)s, %(logo_url)s)
        on conflict (id) do update set
            name = excluded.name,
            logo_url = excluded.logo_url
        """,
        team,
    )


def upsert_player(cur, player):
    cur.execute(
        """
        insert into players (id, full_name, team_id, position, shirt_number, nationality)
        values (%(id)s, %(full_name)s, %(team_id)s, %(position)s, %(shirt_number)s, %(nationality)s)
        on conflict (id) do update set
            team_id = excluded.team_id,
            position = excluded.position,
            shirt_number = excluded.shirt_number
        """,
        player,
    )


def upsert_match(cur, match):
    cur.execute(
        """
        insert into matches (id, competition, season, matchday, home_team_id, away_team_id,
                              home_score, away_score, kickoff, venue, status)
        values (%(id)s, %(competition)s, %(season)s, %(matchday)s, %(home_team_id)s, %(away_team_id)s,
                %(home_score)s, %(away_score)s, %(kickoff)s, %(venue)s, %(status)s)
        on conflict (id) do update set
            home_score = excluded.home_score,
            away_score = excluded.away_score,
            status = excluded.status
        """,
        match,
    )


def upsert_match_team_stats(cur, row):
    cur.execute(
        """
        insert into match_team_stats (match_id, team_id, possession_pct, shots, shots_on_target,
                                       xg, corners, fouls, yellow_cards, red_cards, passes,
                                       pass_accuracy_pct, offsides)
        values (%(match_id)s, %(team_id)s, %(possession_pct)s, %(shots)s, %(shots_on_target)s,
                %(xg)s, %(corners)s, %(fouls)s, %(yellow_cards)s, %(red_cards)s, %(passes)s,
                %(pass_accuracy_pct)s, %(offsides)s)
        on conflict (match_id, team_id) do update set
            possession_pct = excluded.possession_pct,
            shots = excluded.shots,
            shots_on_target = excluded.shots_on_target,
            xg = excluded.xg,
            corners = excluded.corners,
            fouls = excluded.fouls,
            yellow_cards = excluded.yellow_cards,
            red_cards = excluded.red_cards,
            passes = excluded.passes,
            pass_accuracy_pct = excluded.pass_accuracy_pct,
            offsides = excluded.offsides
        """,
        row,
    )


def upsert_player_match_stats(cur, row):
    cur.execute(
        """
        insert into player_match_stats (
            match_id, player_id, team_id, minutes_played, goals, assists, shots,
            shots_on_target, key_passes, passes_completed, passes_attempted,
            dribbles_completed, tackles, interceptions, duels_won, duels_lost,
            fouls_committed, fouls_suffered, yellow_cards, red_cards, rating
        ) values (
            %(match_id)s, %(player_id)s, %(team_id)s, %(minutes_played)s, %(goals)s, %(assists)s,
            %(shots)s, %(shots_on_target)s, %(key_passes)s, %(passes_completed)s, %(passes_attempted)s,
            %(dribbles_completed)s, %(tackles)s, %(interceptions)s, %(duels_won)s, %(duels_lost)s,
            %(fouls_committed)s, %(fouls_suffered)s, %(yellow_cards)s, %(red_cards)s, %(rating)s
        )
        on conflict (match_id, player_id) do update set
            minutes_played = excluded.minutes_played,
            goals = excluded.goals,
            assists = excluded.assists,
            shots = excluded.shots,
            shots_on_target = excluded.shots_on_target,
            rating = excluded.rating
        """,
        row,
    )


def upsert_match_event(cur, row):
    cur.execute(
        """
        insert into match_events (match_id, team_id, player_id, minute, event_type, detail)
        values (%(match_id)s, %(team_id)s, %(player_id)s, %(minute)s, %(event_type)s, %(detail)s)
        """,
        row,
    )


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

def sync_matches(cur, league_id, season):
    matches = api_get("matches", {"leagueId": league_id, "season": season, "limit": 100})
    print(f"Found {len(matches)} matches")

    finished_ids = []
    for m in matches:
        league = m["league"]
        home, away = m["homeTeam"], m["awayTeam"]
        state = m.get("state", {})
        score = state.get("score", {}).get("current")  # e.g. "3 - 1"
        home_score = away_score = None
        if score and "-" in score:
            parts = [p.strip() for p in score.split("-")]
            if len(parts) == 2 and all(p.isdigit() for p in parts):
                home_score, away_score = int(parts[0]), int(parts[1])

        for side in (home, away):
            upsert_team(cur, {
                "id": side["id"], "name": side["name"], "short_name": side["name"][:20],
                "league": league["name"], "country": m.get("country", {}).get("name"),
                "logo_url": side.get("logo"),
            })

        is_finished = "Finished" in state.get("description", "")
        upsert_match(cur, {
            "id": m["id"],
            "competition": league["name"],
            "season": str(league.get("season", season)),
            "matchday": m.get("round"),
            "home_team_id": home["id"],
            "away_team_id": away["id"],
            "home_score": home_score,
            "away_score": away_score,
            "kickoff": m["date"],
            "venue": None,
            "status": "finished" if is_finished else state.get("description", "scheduled"),
        })

        if is_finished:
            finished_ids.append(m["id"])

    return finished_ids


def sync_match_events(cur, match_id):
    try:
        events = api_get(f"events/{match_id}")
    except requests.HTTPError:
        return  # not available for this match (404)

    # Clear existing events for this match so re-runs don't duplicate rows
    cur.execute("delete from match_events where match_id = %s", (match_id,))

    for e in events:
        team_id = e.get("team", {}).get("id")
        player_id = e.get("playerId") or None
        player_name = e.get("player")

        # Ensure the player exists (FK constraint) even if box-score sync missed them
        if player_id:
            upsert_player(cur, {
                "id": player_id, "full_name": player_name or "Unknown",
                "team_id": team_id, "position": None,
                "shirt_number": None, "nationality": None,
            })

        # Parse minute from strings like "45+1", "90+3", "23"
        time_str = str(e.get("time", "")).strip()
        base = time_str.split("+")[0]
        try:
            minute = int(base)
        except ValueError:
            minute = None

        event_type = e.get("type")
        detail = e.get("assist") or e.get("substituted") or None

        upsert_match_event(cur, {
            "match_id": match_id,
            "team_id": team_id,
            "player_id": player_id,
            "minute": minute,
            "event_type": event_type,
            "detail": detail,
        })


def sync_team_stats(cur, match_id):
    try:
        stats = api_get(f"statistics/{match_id}")
    except requests.HTTPError:
        return  # not available for this match (404)

    for team_block in stats:
        team_id = team_block["team"]["id"]
        s = {item["displayName"].strip().lower(): item["value"] for item in team_block.get("statistics", [])}

        def num(*keys):
            for k in keys:
                if k in s:
                    v = s[k]
                    if isinstance(v, str):
                        v = v.replace("%", "").strip()
                    try:
                        return float(v)
                    except (ValueError, TypeError):
                        return None
            return None

        upsert_match_team_stats(cur, {
            "match_id": match_id,
            "team_id": team_id,
            "possession_pct": num("ball possession", "possession"),
            "shots": num("total shots", "shots total"),
            "shots_on_target": num("shots on target"),
            "xg": num("expected goals", "xg"),
            "corners": num("corner kicks", "corners"),
            "fouls": num("fouls"),
            "yellow_cards": num("yellow cards"),
            "red_cards": num("red cards"),
            "passes": num("total passes", "passes total"),
            "pass_accuracy_pct": num("passes accuracy", "pass accuracy"),
            "offsides": num("offsides"),
        })


def sync_player_stats(cur, match_id):
    try:
        box_score = api_get(f"box-score/{match_id}")
    except requests.HTTPError:
        return  # not available for this match (404)

    for team_block in box_score:
        team_id = team_block["team"]["id"]
        for p in team_block.get("players", []):
            player_id = p["id"]
            raw_stats = p.get("statistics")
            if isinstance(raw_stats, list):
                st = raw_stats[0] if raw_stats else {}
            elif isinstance(raw_stats, dict):
                st = raw_stats
            else:
                st = {}

            upsert_player(cur, {
                "id": player_id, "full_name": p.get("fullName") or p.get("name"),
                "team_id": team_id, "position": p.get("position"),
                "shirt_number": p.get("shirtNumber"), "nationality": None,
            })

            upsert_player_match_stats(cur, {
                "match_id": match_id,
                "player_id": player_id,
                "team_id": team_id,
                "minutes_played": p.get("minutesPlayed") or 0,
                "goals": st.get("goalsScored") or 0,
                "assists": st.get("assists") or 0,
                "shots": st.get("shotsTotal") or 0,
                "shots_on_target": st.get("shotsOnTarget") or 0,
                "key_passes": st.get("passesKey") or 0,
                "passes_completed": st.get("passesSuccessful") or 0,
                "passes_attempted": st.get("passesTotal") or 0,
                "dribbles_completed": st.get("dribblesSuccessful") or 0,
                "tackles": st.get("tacklesTotal") or 0,
                "interceptions": st.get("interceptionsTotal") or 0,
                "duels_won": st.get("duelsWon") or 0,
                "duels_lost": st.get("duelsLost") or 0,
                "fouls_committed": st.get("fouledOthers") or 0,
                "fouls_suffered": st.get("fouledByOthers") or 0,
                "yellow_cards": st.get("cardsYellow") or 0,
                "red_cards": st.get("cardsRed") or 0,
                "rating": float(p["matchRating"]) if p.get("matchRating") else None,
            })


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", type=int, help="Highlightly league ID (look up via /leagues)")
    parser.add_argument("--season", type=int, help="Season year, e.g. 2025")
    parser.add_argument("--match", type=int, help="Sync a single match ID only")
    args = parser.parse_args()

    if not args.match and (not args.league or not args.season):
        parser.error("--league and --season are required unless --match is given")

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        if args.match:
            sync_team_stats(cur, args.match)
            sync_player_stats(cur, args.match)
            sync_match_events(cur, args.match)
            conn.commit()
        else:
            finished_ids = sync_matches(cur, args.league, args.season)
            conn.commit()
            print(f"Syncing stats for {len(finished_ids)} finished matches...")
            for mid in finished_ids:
                sync_team_stats(cur, mid)
                sync_player_stats(cur, mid)
                sync_match_events(cur, mid)
                conn.commit()
                print(f"  synced match {mid}")

        print("Done.")
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
