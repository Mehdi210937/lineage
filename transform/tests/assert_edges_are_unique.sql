-- Une arête ne doit apparaître qu'une fois par (from, to, type, source).
select from_artist_mbid, to_artist_mbid, edge_type, source_system, count(*) as n
from {{ ref('edges_artists') }}
group by 1, 2, 3, 4
having count(*) > 1
