-- Une ligne par couple (sortie, label). Les sorties sans label gardent label_mbid = null.
-- is_placeholder_label signale les faux labels de MusicBrainz comme [no label] (autoproduction) :
-- on les garde ici pour calculer is_self_released, et on les exclut des marts de labels.
with source as (
    select entity_id as artist_mbid, payload
    from {{ source('raw', 'api_responses') }}
    where source = 'musicbrainz'
      and entity_type = 'artist_releases'
      and payload is not null
),

releases as (
    select artist_mbid, unnest(json_extract(payload, '$.releases[*]')) as rel
    from source
),

with_labels as (
    select
        artist_mbid,
        rel ->> '$.id'      as release_mbid,
        rel ->> '$.title'   as release_title,
        rel ->> '$.status'  as release_status,
        try_cast(left(rel ->> '$.date', 4) as integer) as release_year,
        json_extract(rel, '$.label-info[*]') as label_infos
    from releases
)

select
    artist_mbid,
    release_mbid,
    release_title,
    release_status,
    release_year,
    li ->> '$.label.id'        as label_mbid,
    li ->> '$.label.name'      as label_name,
    li ->> '$.catalog-number'  as catalog_number,
    coalesce(li ->> '$.label.id' in {{ mb_placeholder_labels() }}, false) as is_placeholder_label
from with_labels
left join lateral (select unnest(label_infos) as li) on true
