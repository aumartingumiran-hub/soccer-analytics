-- ===================================================================
-- SOCCER ANALYTICS SCHEMA (Supabase / Postgres) — v2, bigint IDs
-- Run this to DROP the old uuid-based tables and recreate with bigint
-- IDs matching Highlightly's integer team/player/match IDs.
-- ===================================================================

drop view if exists team_form;
drop view if exists player_season_stats;
drop table if exists match_events;
drop table if exists player_match_stats;
drop table if exists match_team_stats;
drop table if exists matches;
drop table if exists players;
drop table if exists teams;

-- ---------- Core entities ----------

create table teams (
    id bigint primary key,              -- Highlightly team id
    name text not null,
    short_name text,
    league text,
    country text,
    logo_url text,
    created_at timestamptz default now()
);

create table players (
    id bigint primary key,              -- Highlightly player id
    full_name text not null,
    team_id bigint references teams(id) on delete set null,
    position text,
    shirt_number int,
    date_of_birth date,
    nationality text,
    height_cm int,
    preferred_foot text,
    created_at timestamptz default now()
);

create table matches (
    id bigint primary key,              -- Highlightly match id
    competition text,
    season text,
    matchday text,
    home_team_id bigint references teams(id),
    away_team_id bigint references teams(id),
    home_score int,
    away_score int,
    kickoff timestamptz,
    venue text,
    status text default 'scheduled',
    created_at timestamptz default now()
);

-- ---------- Match-level team stats ----------

create table match_team_stats (
    id uuid primary key default gen_random_uuid(),
    match_id bigint references matches(id) on delete cascade,
    team_id bigint references teams(id),
    possession_pct numeric(5,2),
    shots int,
    shots_on_target int,
    xg numeric(5,2),
    corners int,
    fouls int,
    yellow_cards int,
    red_cards int,
    passes int,
    pass_accuracy_pct numeric(5,2),
    offsides int,
    unique(match_id, team_id)
);

-- ---------- Player match performance ----------

create table player_match_stats (
    id uuid primary key default gen_random_uuid(),
    match_id bigint references matches(id) on delete cascade,
    player_id bigint references players(id) on delete cascade,
    team_id bigint references teams(id),
    minutes_played int,
    goals int default 0,
    assists int default 0,
    shots int default 0,
    shots_on_target int default 0,
    xg numeric(5,2) default 0,
    xa numeric(5,2) default 0,
    key_passes int default 0,
    passes_completed int default 0,
    passes_attempted int default 0,
    dribbles_completed int default 0,
    tackles int default 0,
    interceptions int default 0,
    duels_won int default 0,
    duels_lost int default 0,
    fouls_committed int default 0,
    fouls_suffered int default 0,
    yellow_cards int default 0,
    red_cards int default 0,
    rating numeric(3,1),
    unique(match_id, player_id)
);

-- ---------- Event log ----------

create table match_events (
    id uuid primary key default gen_random_uuid(),
    match_id bigint references matches(id) on delete cascade,
    team_id bigint references teams(id),
    player_id bigint references players(id),
    minute int,
    event_type text,
    detail text,
    x numeric(5,2),
    y numeric(5,2),
    created_at timestamptz default now()
);

-- ---------- Derived view: season player aggregates ----------

create view player_season_stats as
select
    p.id as player_id,
    p.full_name,
    p.team_id,
    m.season,
    count(distinct pms.match_id) as appearances,
    sum(pms.minutes_played) as total_minutes,
    sum(pms.goals) as goals,
    sum(pms.assists) as assists,
    sum(pms.xg) as total_xg,
    sum(pms.xa) as total_xa,
    round(avg(pms.rating), 2) as avg_rating,
    round(sum(pms.passes_completed)::numeric / nullif(sum(pms.passes_attempted), 0) * 100, 1) as pass_accuracy_pct
from player_match_stats pms
join players p on p.id = pms.player_id
join matches m on m.id = pms.match_id
group by p.id, p.full_name, p.team_id, m.season;

-- ---------- Derived view: team form ----------

create view team_form as
select
    t.id as team_id,
    t.name,
    m.id as match_id,
    m.kickoff,
    case
        when m.home_team_id = t.id and m.home_score > m.away_score then 'W'
        when m.away_team_id = t.id and m.away_score > m.home_score then 'W'
        when m.home_score = m.away_score then 'D'
        else 'L'
    end as result
from teams t
join matches m on m.home_team_id = t.id or m.away_team_id = t.id
where m.status = 'finished';

-- ---------- Indexes ----------

create index idx_pms_player on player_match_stats(player_id);
create index idx_pms_match on player_match_stats(match_id);
create index idx_mts_match on match_team_stats(match_id);
create index idx_matches_season on matches(season);
create index idx_events_match on match_events(match_id);
