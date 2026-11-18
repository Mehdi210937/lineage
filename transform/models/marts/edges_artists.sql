-- Arêtes du graphe d'artistes, prêtes pour l'app et pour l'agent.
-- in_scope = les deux extrémités ont été crawlées (sinon l'arête pointe hors du périmètre).
select
    e.from_artist_mbid,
    f.artist_name as from_artist_name,
    e.to_artist_mbid,
    t.artist_name as to_artist_name,
    e.edge_type,
    e.source_system,
    e.evidence,
    (f.artist_mbid is not null and t.artist_mbid is not null) as in_scope
from {{ ref('int_artist_edges__unioned') }} e
left join {{ ref('dim_artists') }} f on f.artist_mbid = e.from_artist_mbid
left join {{ ref('dim_artists') }} t on t.artist_mbid = e.to_artist_mbid
