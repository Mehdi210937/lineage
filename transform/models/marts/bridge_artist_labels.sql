-- Quels artistes sortent sur quels labels, et quand.
-- Combine les sorties (label-info) et les relations explicites (contrat, etc.).
with from_releases as (
    select
        artist_mbid,
        label_mbid,
        any_value(label_name)          as label_name,
        count(distinct release_mbid)   as n_releases,
        min(release_year)              as first_release_year,
        max(release_year)              as last_release_year
    from {{ ref('stg_musicbrainz__release_labels') }}
    where label_mbid is not null
    group by 1, 2
),

from_relations as (
    select
        artist_mbid,
        target_id                      as label_mbid,
        any_value(target_name)         as label_name,
        string_agg(distinct relation_type, ', ') as relation_types
    from {{ ref('stg_musicbrainz__relations') }}
    where target_type = 'label'
    group by 1, 2
)

select
    coalesce(r.artist_mbid, l.artist_mbid)  as artist_mbid,
    coalesce(r.label_mbid, l.label_mbid)    as label_mbid,
    coalesce(r.label_name, l.label_name)    as label_name,
    coalesce(r.n_releases, 0)               as n_releases,
    r.first_release_year,
    r.last_release_year,
    l.relation_types
from from_releases r
full outer join from_relations l
    on l.artist_mbid = r.artist_mbid and l.label_mbid = r.label_mbid
