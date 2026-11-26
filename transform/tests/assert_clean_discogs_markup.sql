-- Le nettoyage du balisage Discogs donne bien le texte attendu (cas tirés des vraies bios).
with cases(raw, expected) as (
    values
        ('Former bassist of [a=Kïll Cheerleadër], one half of [a=Crystal Castles].',
         'Former bassist of Kïll Cheerleadër, one half of Crystal Castles.'),
        ('formed by [a=Greg Dulli] and drummer [a=Steve Earle (2)]',
         'formed by Greg Dulli and drummer Steve Earle'),
        ('[a1069428] served as the vocalist until 2014', 'served as the vocalist until 2014'),
        ('Vocals by [a1069428], drums.', 'Vocals by, drums.'),
        ('A [i]soul-influenced[/i] band on [l=Sub Pop].', 'A soul-influenced band on Sub Pop.'),
        ('See [url=https://example.com]their site[/url] and [b]more[/b].', 'See their site and more.'),
        ('[b]Members:[/b]
[a1081004] – Instruments', 'Members:
 – Instruments'),
        ('Pas de balisage ici.', 'Pas de balisage ici.')
)

select raw, expected, {{ clean_discogs_markup('raw') }} as got
from cases
where {{ clean_discogs_markup('raw') }} is distinct from expected
