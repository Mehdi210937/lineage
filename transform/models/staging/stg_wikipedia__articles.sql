-- Articles Wikipédia anglais des artistes (texte brut, sections « == Titre == » conservées).
-- entity_id = QID Wikidata de l'artiste : la jointure passe par int_artist_external_ids.
select
    entity_id                          as wikidata_qid,
    payload ->> '$.title'              as title,
    try_cast(payload ->> '$.pageid' as bigint) as pageid,
    nullif(trim(payload ->> '$.extract'), '') as extract,
    fetched_at
from {{ source('raw', 'api_responses') }}
where source = 'wikipedia'
  and entity_type = 'article'
  and payload is not null
