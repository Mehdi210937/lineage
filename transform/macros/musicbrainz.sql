{# Liste SQL des MBID de labels « spéciaux » (ex. [no label]), à utiliser avec IN / NOT IN. #}
{% macro mb_placeholder_labels() -%}
    ({%- for mbid in var('mb_placeholder_label_mbids') -%}'{{ mbid }}'{{ ", " if not loop.last }}{%- endfor -%})
{%- endmacro %}
