-- Le LLM ne peut pas inventer : chaque citation doit se trouver dans la bio Discogs de l'artiste
-- dont elle a été extraite (même normalisation que validate_against_source : espaces et casse).
-- Couvre aussi les affirmations non rapprochées, qui ne sont pas (encore) dans le graphe.
select r.artist_mbid, r.other_artist, r.evidence_quote
from {{ ref('int_llm_claims__resolved') }} r
left join {{ ref('dim_artists') }} a using (artist_mbid)
where a.discogs_profile is null
   or coalesce(trim(r.evidence_quote), '') = ''  -- une citation vide serait « trouvée » partout
   or strpos(
        lower(regexp_replace(trim(a.discogs_profile), '\s+', ' ', 'g')),
        lower(regexp_replace(trim(r.evidence_quote), '\s+', ' ', 'g'))
      ) = 0
