{# Clé de rapprochement d'un nom d'artiste : minuscules, sans accents, « & » → « and »,
   ponctuation retirée, espaces normalisés, sans « the » initial.
   « The Crypt-Sister » et « crypt sister » donnent la même clé « crypt sister ». #}
{% macro normalize_artist_name(col) -%}
    nullif(regexp_replace(
        trim(regexp_replace(
            regexp_replace(replace(strip_accents(lower({{ col }})), '&', ' and '), '[^a-z0-9]+', ' ', 'g'),
            '\s+', ' ', 'g'
        )),
        '^the ', ''
    ), '')
{%- endmacro %}
