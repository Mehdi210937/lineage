-- Toutes les arêtes artiste → artiste, toutes sources confondues, orientées et dédoublonnées.
-- Convention : from_artist « fait l'action » sur to_artist
--   member of band : from = membre, to = groupe
--   influenced_by  : from = artiste influencé, to = artiste qui l'a influencé
--   compared_to, collaborated_with, sampled (LLM) : from = artiste dont la bio parle, to = l'autre

with mb as (
    select
        -- MusicBrainz stocke la relation des deux côtés ; on la remet dans le sens « forward »
        case when direction = 'backward' then target_id else artist_mbid end as from_artist_mbid,
        case when direction = 'backward' then artist_mbid else target_id end as to_artist_mbid,
        relation_type as edge_type,
        'musicbrainz' as source_system,
        cast(null as varchar) as evidence,
        cast(null as double) as confidence
    from {{ ref('stg_musicbrainz__relations') }}
    where target_type = 'artist'
),

wd as (
    select
        case when i.direction = 'influenced_by' then x.artist_mbid else i.other_mbid end as from_artist_mbid,
        case when i.direction = 'influenced_by' then i.other_mbid else x.artist_mbid end as to_artist_mbid,
        'influenced_by' as edge_type,
        'wikidata' as source_system,
        'Wikidata P737 sur ' || i.subject_qid as evidence,
        cast(null as double) as confidence
    from {{ ref('stg_wikidata__influences') }} i
    join {{ ref('int_artist_external_ids') }} x on x.wikidata_qid = i.subject_qid
    where i.other_mbid is not null  -- les artistes sans MBID ne peuvent pas entrer dans le graphe
),

llm as (
    -- seules les affirmations rapprochées d'un MBID unique entrent dans le graphe ;
    -- les autres sont dans llm_claims_to_review
    select
        case when relation = 'influenced' then other_artist_mbid else artist_mbid end as from_artist_mbid,
        case when relation = 'influenced' then artist_mbid else other_artist_mbid end as to_artist_mbid,
        case when relation = 'influenced' then 'influenced_by' else relation end      as edge_type,
        'llm' as source_system,
        evidence_quote as evidence,
        confidence
    from {{ ref('int_llm_claims__resolved') }}
    where match_status = 'matched'
),

unioned as (
    select * from mb
    union all
    select * from wd
    union all
    select * from llm
)

select
    from_artist_mbid,
    to_artist_mbid,
    edge_type,
    source_system,
    -- plusieurs citations pour une même arête : on garde la plus sûre
    first(evidence order by confidence desc nulls last, evidence nulls last) as evidence,
    max(confidence) as confidence
from unioned
where from_artist_mbid <> to_artist_mbid
group by 1, 2, 3, 4
