-- Table de correspondance entre les identifiants des trois sources.
-- MusicBrainz est le pivot : ses relations URL pointent vers Discogs et Wikidata.
with urls as (
    select artist_mbid, target_url
    from {{ ref('stg_musicbrainz__relations') }}
    where target_type = 'url'
),

parsed as (
    select
        artist_mbid,
        nullif(regexp_extract(target_url, 'discogs\.com/artist/(\d+)', 1), '') as discogs_artist_id,
        nullif(regexp_extract(target_url, 'wikidata\.org/wiki/(Q\d+)', 1), '') as wikidata_qid
    from urls
)

select
    a.artist_mbid,
    -- si plusieurs liens existent (cas rare), on garde le plus petit identifiant, de façon déterministe
    min(p.discogs_artist_id) as discogs_artist_id,
    min(p.wikidata_qid)      as wikidata_qid,
    count(distinct p.discogs_artist_id) as n_discogs_links,
    count(distinct p.wikidata_qid)      as n_wikidata_links
from {{ ref('stg_musicbrainz__artists') }} a
left join parsed p using (artist_mbid)
group by a.artist_mbid
