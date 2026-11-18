-- Une ligne par (artiste, sortie), avec ses vrais labels et un indicateur d'autoproduction.
--
-- is_self_released :
--   true  = MusicBrainz indique explicitement [no label] et aucun vrai label
--   false = au moins un vrai label
--   null  = aucune info de label : on ne sait pas (absence de donnée ≠ autoproduction)
with release_labels as (
    select * from {{ ref('stg_musicbrainz__release_labels') }}
),

per_release as (
    select
        artist_mbid,
        release_mbid,
        any_value(release_title)    as release_title,
        any_value(release_status)   as release_status,
        any_value(release_year)     as release_year,
        count(distinct label_mbid) filter (where label_mbid is not null and not is_placeholder_label)
                                    as n_labels,
        string_agg(distinct label_name, ', ') filter (where label_mbid is not null and not is_placeholder_label)
                                    as label_names,
        bool_or(is_placeholder_label) as has_placeholder_label
    from release_labels
    group by 1, 2
)

select
    artist_mbid,
    release_mbid,
    release_title,
    release_status,
    release_year,
    n_labels,
    label_names,
    case
        when n_labels > 0 then false
        when has_placeholder_label then true
    end as is_self_released
from per_release
