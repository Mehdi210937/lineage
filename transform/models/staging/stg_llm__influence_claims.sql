-- Relations extraites des bios Discogs par le LLM (python -m lineage.extract.run).
-- Chaque affirmation porte sa citation ; la vérification « la citation est dans la bio » est faite
-- à l'extraction (validate_against_source) et revérifiée par le test assert_llm_evidence_in_bio.
--
-- Si l'extraction n'a encore jamais tourné, la table brute n'existe pas : on renvoie un résultat
-- vide du bon type plutôt que de faire échouer tout le build.
{%- set src = source('raw', 'llm_influence_claims') %}
{%- set src_exists = load_relation(src) is not none %}

{% if src_exists %}
select
    artist_mbid,
    model,
    trim(other_artist)                as other_artist,
    {{ normalize_artist_name('other_artist') }} as other_artist_key,
    relation,
    trim(evidence_quote)              as evidence_quote,
    confidence,
    extracted_at
from {{ src }}
-- les lignes sans other_artist marquent seulement un artiste traité sans relation trouvée
where other_artist is not null
  and trim(other_artist) <> ''
{% else %}
select
    cast(null as varchar)   as artist_mbid,
    cast(null as varchar)   as model,
    cast(null as varchar)   as other_artist,
    cast(null as varchar)   as other_artist_key,
    cast(null as varchar)   as relation,
    cast(null as varchar)   as evidence_quote,
    cast(null as double)    as confidence,
    cast(null as timestamp) as extracted_at
where false
{% endif %}
