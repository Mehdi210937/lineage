-- Règle d'or du projet : toute arête produite par le LLM doit citer la phrase qui la justifie.
select *
from {{ ref('edges_artists') }}
where source_system = 'llm'
  and (evidence is null or length(trim(evidence)) < 10)
