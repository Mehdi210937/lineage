{# Retire le balisage Discogs d'une bio, pour l'envoyer au LLM et y vérifier les citations :
   [a=Nom] / [l=Label] → Nom / Label (sans le suffixe d'homonymie « (2) ») ; [a123], [l45], [r6], [m7]
   (identifiants sans nom) → supprimés ; [url=…]texte[/url] → texte ; [b] [i] [u] [s] → supprimés ;
   espaces multiples (hors retours à la ligne) réduits à un seul. #}
{% macro clean_discogs_markup(col) -%}
    nullif(trim(regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(
        {{ col }},
        '\[url=[^\]]*\](.*?)\[/url\]', '\1', 'g'),
        '\[[alrm]=([^\]]+?)(?: \(\d+\))?\]', '\1', 'g'),
        '\[[alrm]\d+\]', '', 'g'),
        '\[/?(?:b|i|u|s|url)\]', '', 'g'),
        '[ \t]+([,.;:)])', '\1', 'g'),
        '[ \t]{2,}', ' ', 'g')), '')
{%- endmacro %}
