-- Le LLM ne peut pas inventer : chaque citation doit se trouver dans le texte d'où elle a été
-- extraite (bio Discogs nettoyée de son balisage, ou article Wikipédia), avec la même
-- normalisation que validate_against_source (espaces et casse).
-- Couvre aussi les affirmations non rapprochées, qui ne sont pas (encore) dans le graphe.
with claims as (
    select
        r.artist_mbid,
        r.text_source,
        r.other_artist,
        r.evidence_quote,
        case r.text_source
            when 'wikipedia' then a.wikipedia_text
            else a.discogs_profile_clean
        end as source_text
    from {{ ref('int_llm_claims__resolved') }} r
    left join {{ ref('dim_artists') }} a using (artist_mbid)
)

select artist_mbid, text_source, other_artist, evidence_quote
from claims
where source_text is null
   or coalesce(trim(evidence_quote), '') = ''  -- une citation vide serait « trouvée » partout
   or strpos(
        lower(regexp_replace(trim(source_text), '\s+', ' ', 'g')),
        lower(regexp_replace(trim(evidence_quote), '\s+', ' ', 'g'))
      ) = 0
