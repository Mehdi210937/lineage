-- fct_artist_releases : une seule ligne par (artiste, sortie).
select artist_mbid, release_mbid, count(*) as n
from {{ ref('fct_artist_releases') }}
group by 1, 2
having count(*) > 1
