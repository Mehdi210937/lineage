-- Un artiste par ligne, enrichi de ses identifiants externes, de sa bio Discogs
-- et de sa position dans le crawl.
--
-- perimeter (voir python -m lineage.crawl --rescope) :
--   core  = artistes de départ et artistes reliés à eux par MusicBrainz ou Wikidata ;
--   label = artistes d'un label de la scène (partagé par au moins 2 artistes du noyau) ;
--   out   = crawlé avec d'anciennes règles, hors de la scène (gardé en base, masqué dans l'app).
with seeds as (
    select distinct mbid from {{ source('raw', 'seed_artists') }} where mbid is not null
)

select
    a.artist_mbid,
    a.artist_name,
    a.artist_type,
    a.country_code,
    a.begin_year,
    a.end_year,
    a.has_ended,
    a.genres,
    a.tags,
    x.discogs_artist_id,
    x.wikidata_qid,
    d.profile                         as discogs_profile,
    q.depth                           as crawl_depth,
    q.discovered_via,
    coalesce(
        q.scope,
        case when q.discovered_via like 'musicbrainz:label:%' then 'label' else 'core' end
    )                                 as perimeter,
    q.scope_reason                    as perimeter_reason,
    coalesce(q.scope, '') <> 'out'    as in_perimeter,
    s.mbid is not null                as is_seed,
    a.fetched_at
from {{ ref('stg_musicbrainz__artists') }} a
left join {{ ref('int_artist_external_ids') }} x using (artist_mbid)
left join {{ ref('stg_discogs__artists') }} d on d.discogs_artist_id = x.discogs_artist_id
left join {{ source('raw', 'crawl_queue') }} q on q.mbid = a.artist_mbid
left join seeds s on s.mbid = a.artist_mbid
