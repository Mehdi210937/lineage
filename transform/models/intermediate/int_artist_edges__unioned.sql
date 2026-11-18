-- Toutes les arêtes artiste → artiste, toutes sources confondues, orientées et dédoublonnées.
-- Convention : from_artist « fait l'action » sur to_artist
--   member of band : from = membre, to = groupe
--   influenced_by  : from = artiste influencé, to = artiste qui l'a influencé

with mb as (
    select
        -- MusicBrainz stocke la relation des deux côtés ; on la remet dans le sens « forward »
        case when direction = 'backward' then target_id else artist_mbid end as from_artist_mbid,
        case when direction = 'backward' then artist_mbid else target_id end as to_artist_mbid,
        relation_type as edge_type,
        'musicbrainz' as source_system,
        cast(null as varchar) as evidence
    from {{ ref('stg_musicbrainz__relations') }}
    where target_type = 'artist'
),

wd as (
    select
        case when i.direction = 'influenced_by' then x.artist_mbid else i.other_mbid end as from_artist_mbid,
        case when i.direction = 'influenced_by' then i.other_mbid else x.artist_mbid end as to_artist_mbid,
        'influenced_by' as edge_type,
        'wikidata' as source_system,
        'Wikidata P737 sur ' || i.subject_qid as evidence
    from {{ ref('stg_wikidata__influences') }} i
    join {{ ref('int_artist_external_ids') }} x on x.wikidata_qid = i.subject_qid
    where i.other_mbid is not null  -- les artistes sans MBID ne peuvent pas entrer dans le graphe
),

unioned as (
    select * from mb
    union all
    select * from wd
)

select
    from_artist_mbid,
    to_artist_mbid,
    edge_type,
    source_system,
    min(evidence) as evidence
from unioned
where from_artist_mbid <> to_artist_mbid
group by 1, 2, 3, 4
