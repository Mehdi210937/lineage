-- Un label par ligne, avec des indicateurs utiles pour « à quel label envoyer ma démo ».
select
    label_mbid,
    any_value(label_name)                 as label_name,
    count(distinct artist_mbid)           as n_artists_in_scope,
    sum(n_releases)                       as n_releases_in_scope,
    min(first_release_year)               as first_release_year,
    max(last_release_year)                as last_release_year
from {{ ref('bridge_artist_labels') }}
group by label_mbid
