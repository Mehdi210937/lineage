-- Une ligne par artiste MusicBrainz téléchargé.
with source as (
    select entity_id, payload, fetched_at
    from {{ source('raw', 'api_responses') }}
    where source = 'musicbrainz'
      and entity_type = 'artist'
      and payload is not null
)

select
    entity_id                                                as artist_mbid,
    payload ->> '$.name'                                     as artist_name,
    payload ->> '$.sort-name'                                as sort_name,
    payload ->> '$.type'                                     as artist_type,
    payload ->> '$.country'                                  as country_code,
    payload ->> '$.disambiguation'                           as disambiguation,
    -- les dates MusicBrainz peuvent être partielles (« 2008 », « 2008-03 ») : on garde l'année
    try_cast(left(payload ->> '$.life-span.begin', 4) as integer) as begin_year,
    try_cast(left(payload ->> '$.life-span.end', 4) as integer)   as end_year,
    coalesce(cast(payload ->> '$.life-span.ended' as boolean), false) as has_ended,
    json_extract_string(payload, '$.genres[*].name')         as genres,
    json_extract_string(payload, '$.tags[*].name')           as tags,
    fetched_at
from source
