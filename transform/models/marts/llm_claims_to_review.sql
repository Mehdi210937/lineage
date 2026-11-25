-- Affirmations du LLM dont l'autre artiste n'a pas pu être rapproché d'un MBID unique.
-- À revoir à la main : soit l'artiste est hors du crawl (l'ajouter aux seeds ?), soit c'est un
-- homonyme (ambiguous, candidate_mbids liste les MBID possibles), soit le LLM a pris un genre ou
-- un lieu pour un artiste.
select
    r.artist_mbid,
    a.artist_name,
    r.other_artist,
    r.other_artist_key,
    r.relation,
    r.match_status,
    r.n_candidates,
    r.candidate_mbids,
    r.evidence_quote,
    r.confidence,
    r.model,
    r.extracted_at
from {{ ref('int_llm_claims__resolved') }} r
left join {{ ref('dim_artists') }} a using (artist_mbid)
where r.match_status <> 'matched'
