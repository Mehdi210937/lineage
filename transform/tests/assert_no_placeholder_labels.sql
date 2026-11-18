-- Les faux labels de MusicBrainz ([no label] = autoproduction, cf. var mb_placeholder_label_mbids)
-- ne doivent jamais apparaître comme labels. L'autoproduction est portée par
-- fct_artist_releases.is_self_released.
-- On filtre sur le MBID, pas sur le nom : « [PIAS] » est un vrai label avec des crochets.
select 'dim_labels' as model, label_mbid, label_name
from {{ ref('dim_labels') }}
where label_mbid in {{ mb_placeholder_labels() }}

union all

select 'bridge_artist_labels' as model, label_mbid, label_name
from {{ ref('bridge_artist_labels') }}
where label_mbid in {{ mb_placeholder_labels() }}
