# Reddit EV Laadpaal Klachten Tool

Deze tool scrapt publieke Reddit-posts en comments over klachten rond EV-laadpalen, classificeert wat er stuk gaat, en maakt automatisch:

- `items.csv` met alle gevonden klachtrecords
- `issue_counts.csv` en `primary_issue_counts.csv`
- `subreddit_counts.csv`, `scope_counts.csv`, `monthly_counts.csv`
- `analysis.json`
- `report.md`
- `report.html` met tabellen en SVG-grafieken
- losse grafieken: `primary_issue_chart.svg`, `issue_counts_chart.svg`, `monthly_counts_chart.svg`
- `ai_handoff.md` en `codex_handoff.zip` om de data makkelijk aan Codex/ChatGPT te geven

De tool gebruikt alleen de Python standard library. Er zijn geen Reddit API-keys nodig.

## Snel testen met voorbeelddata

```powershell
python .\reddit_ev_charger_tool\ev_charger_reddit_tool.py run --sample --scope all --output .\outputs\ev_charger_reddit_sample
```

Open daarna:

```text
outputs\ev_charger_reddit_sample\report.html
```

## Echte Reddit scrape

Kleine test:

```powershell
python .\reddit_ev_charger_tool\ev_charger_reddit_tool.py run --scope public --output .\outputs\ev_charger_reddit_live --limit-per-query 2 --max-posts 8 --comments-per-post 80 --delay 1.5
```

Grotere scrape:

```powershell
python .\reddit_ev_charger_tool\ev_charger_reddit_tool.py run --scope public --output .\outputs\ev_charger_reddit_full --limit-per-query 8 --max-posts 60 --comments-per-post 160 --delay 1.5
```

Daarna kan je dit bestand aan Codex/ChatGPT geven:

```text
outputs\ev_charger_reddit_full\codex_handoff.zip
```

Of, als je liever tekst plakt:

```text
outputs\ev_charger_reddit_full\ai_handoff.md
```

Alle scopes, inclusief thuisladers/Wallbox:

```powershell
python .\reddit_ev_charger_tool\ev_charger_reddit_tool.py run --scope all --output .\outputs\ev_charger_reddit_all --limit-per-query 8 --max-posts 60 --comments-per-post 160 --delay 1.5
```

## Eigen Reddit-posts toevoegen

```powershell
python .\reddit_ev_charger_tool\ev_charger_reddit_tool.py run --scope public --output .\outputs\ev_charger_reddit_custom --seed-url "https://www.reddit.com/r/EVMobiliteit/comments/1qq1bmi/publieke_laadpaal_al_maanden_buiten_werking/"
```

## Categorieen

De classifier is transparant en keyword-based. Een record kan meerdere labels krijgen:

- Connectiviteit/backend
- Software/firmware
- Betaling/authenticatie
- Kabel/connector
- Scherm/reader
- Vandalisme/diefstal/schade
- Onderhoud/operator
- Bezet/wachtrij
- Parkeren/toegang
- Snelheid/net/vermogen
- Thuis/Wallbox/installatie

De `primary_issue` is het sterkste label per record. De multi-label tabellen tellen alle genoemde thema's, dus percentages kunnen samen boven 100% uitkomen.

## Belangrijke nuance

Dit meet Reddit-klachtvolume, niet de objectieve faalkans van laadpalen. De uitkomst is nuttig om te zien waar mensen het vaakst over klagen, welke patronen terugkomen, en welke voorbeelden je verder handmatig wilt onderzoeken.
