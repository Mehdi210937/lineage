-- Une ligne par relation d'un artiste (vers un artiste, un label ou une URL).
with source as (
    select entity_id as artist_mbid, payload
    from {{ source('raw', 'api_responses') }}
    where source = 'musicbrainz'
      and entity_type = 'artist'
      and payload is not null
),

exploded as (
    select artist_mbid, unnest(json_extract(payload, '$.relations[*]')) as rel
    from source
)

select
    artist_mbid,
    rel ->> '$.type'                    as relation_type,
    rel ->> '$.direction'               as direction,
    rel ->> '$.target-type'             as target_type,
    coalesce(rel ->> '$.artist.id', rel ->> '$.label.id')     as target_id,
    coalesce(rel ->> '$.artist.name', rel ->> '$.label.name') as target_name,
    rel ->> '$.url.resource'            as target_url,
    try_cast(left(rel ->> '$.begin', 4) as integer) as begin_year,
    try_cast(left(rel ->> '$.end', 4) as integer)   as end_year
from exploded
