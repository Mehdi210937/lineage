-- Relations « influencé par » (P737) issues de Wikidata, dans les deux sens.
with source as (
    select entity_id as qid, payload
    from {{ source('raw', 'api_responses') }}
    where source = 'wikidata'
      and entity_type = 'influences'
      and payload is not null
),

bindings as (
    select qid, unnest(json_extract(payload, '$.bindings[*]')) as b
    from source
)

select
    qid                                           as subject_qid,
    b ->> '$.direction.value'                     as direction,   -- influenced_by | influenced
    regexp_extract(b ->> '$.other.value', 'Q\d+$') as other_qid,
    b ->> '$.otherLabel.value'                    as other_label,
    b ->> '$.otherMbid.value'                     as other_mbid
from bindings
