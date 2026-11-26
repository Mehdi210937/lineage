-- Rapproche le nom écrit par le LLM (« other_artist ») d'un MBID connu.
--
-- Candidats, par ordre de priorité :
--   1. les artistes crawlés (stg_musicbrainz__artists) ;
--   2. les artistes cités dans les relations MusicBrainz mais pas crawlés (MBID connu, hors périmètre).
-- On prend le premier niveau qui a au moins un candidat. Plusieurs MBID distincts au même niveau
-- = homonymes : on ne choisit pas au hasard, l'affirmation part en revue manuelle.
--
-- match_status : matched | ambiguous | no_match
with claims as (
    select * from {{ ref('stg_llm__influence_claims') }}
),

candidates as (
    select distinct
        {{ normalize_artist_name('artist_name') }} as name_key,
        artist_mbid as candidate_mbid,
        1 as priority
    from {{ ref('stg_musicbrainz__artists') }}

    union

    select distinct
        {{ normalize_artist_name('target_name') }} as name_key,
        target_id as candidate_mbid,
        2 as priority
    from {{ ref('stg_musicbrainz__relations') }}
    where target_type = 'artist' and target_id is not null
),

best_level as (
    select name_key, min(priority) as priority
    from candidates
    where name_key is not null
    group by name_key
),

resolution as (
    select
        c.name_key,
        count(distinct c.candidate_mbid) as n_candidates,
        min(c.candidate_mbid)            as candidate_mbid,
        string_agg(distinct c.candidate_mbid, ', ') as candidate_mbids
    from candidates c
    join best_level b using (name_key, priority)
    group by c.name_key
)

select
    cl.artist_mbid,
    cl.text_source,
    cl.model,
    cl.other_artist,
    cl.other_artist_key,
    cl.relation,
    cl.evidence_quote,
    cl.confidence,
    cl.extracted_at,
    case when r.n_candidates = 1 then r.candidate_mbid end as other_artist_mbid,
    coalesce(r.n_candidates, 0)                              as n_candidates,
    r.candidate_mbids,
    case
        when r.n_candidates = 1 then 'matched'
        when r.n_candidates > 1 then 'ambiguous'
        else 'no_match'
    end as match_status
from claims cl
left join resolution r on r.name_key = cl.other_artist_key
