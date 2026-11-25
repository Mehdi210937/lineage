-- Taille réelle des labels explorés par le crawler (expansion par labels de niche).
-- release-count est le total MusicBrainz, même pour un gros label dont le payload est tronqué.
select
    entity_id                                        as label_mbid,
    try_cast(payload ->> '$."release-count"' as integer) as n_releases_total,
    coalesce(cast(payload ->> '$.complete' as boolean), true) as is_fully_crawled,
    fetched_at
from {{ source('raw', 'api_responses') }}
where source = 'musicbrainz'
  and entity_type = 'label_releases'
  and payload is not null
