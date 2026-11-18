{{ config(severity='warn') }}
-- Signale les artistes de départ que MusicBrainz n'a pas trouvés ou a trouvés avec un score faible :
-- ils sont à vérifier à la main (nom différent, homonyme...).
select seed_name, matched_name, match_score
from {{ source('raw', 'seed_artists') }}
where mbid is null or match_score < 90
