-- Un label par ligne, avec des indicateurs utiles pour « à quel label envoyer ma démo ».
-- *_in_scope = vu depuis les artistes crawlés ; n_releases_total = taille réelle dans MusicBrainz
-- (null si le crawler n'a pas exploré ce label).
with in_scope as (
    select
        label_mbid,
        any_value(label_name)                 as label_name,
        count(distinct artist_mbid)           as n_artists_in_scope,
        sum(n_releases)                       as n_releases_in_scope,
        min(first_release_year)               as first_release_year,
        max(last_release_year)                as last_release_year
    from {{ ref('bridge_artist_labels') }}
    group by label_mbid
)

select
    i.*,
    l.n_releases_total
from in_scope i
left join {{ ref('stg_musicbrainz__labels') }} l using (label_mbid)
