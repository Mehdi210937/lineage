-- Fiches artistes Discogs. Le champ `profile` (bio libre) alimente l'extraction LLM.
select
    entity_id                         as discogs_artist_id,
    payload ->> '$.name'              as artist_name,
    nullif(trim(payload ->> '$.profile'), '') as profile,
    payload ->> '$.data_quality'      as data_quality,
    fetched_at
from {{ source('raw', 'api_responses') }}
where source = 'discogs'
  and entity_type = 'artist'
  and payload is not null
