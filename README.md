# Scraper APEC — offres commerciales CDD / stage / alternance

Collecte quotidienne des offres d'emploi commerciales sur APEC, France entière,
via l'endpoint JSON public du site. Pas de Selenium, pas de LLM, pas de clé API.

---

## A. Carte du site

Reconnaissance effectuée le **2026-09-16**.

| Type de page | Pattern d'URL | Rendu | Exemple |
|---|---|---|---|
| Recherche (UI) | `/candidat/recherche-emploi.html/emploi?motsCles=…` | **SPA Angular** — 0 offre dans le HTML initial | `…/emploi?motsCles=business%20developer` |
| **API de recherche** | `POST /cms/webservices/rechercheOffre` | **JSON** — accès libre, sans auth | payload ci-dessous |
| Fiche détail (UI) | `/candidat/recherche-emploi.html/emploi/detail-offre/{numeroOffre}` | SPA Angular | `…/detail-offre/179407255W` |
| API détail | `GET /cms/webservices/offre/public/{id}` | **403 DataDome (captcha)** — inexploitable | — |
| Sitemaps | `/sitemap_offres_search_engine.xml.gz` | XML statique | listé dans `robots.txt` |

**`robots.txt` :** `User-agent: *` **sans aucune règle `Disallow`**. Les 4 sitemaps sont
publiés. Aucune restriction sur les chemins utilisés ici. Aucun `Crawl-delay` déclaré —
on applique quand même un délai aléatoire par correction.

**Protections :** aucune sur `rechercheOffre` (600 offres en 6 requêtes enchaînées sans
délai : 6× HTTP 200). DataDome est actif sur `offre/public` uniquement.

### Payload de l'API

```jsonc
POST https://www.apec.fr/cms/webservices/rechercheOffre
Content-Type: application/json
{
  "motsCles": "business developer",
  "typeClient": "CADRE",
  "typesContrat": [101887, 597171, 20053],   // CDD, STAGE, ALTERNANCE
  "sorts": [{"type": "DATE", "direction": "DESCENDING"}],
  "pagination": {"range": 100, "startIndex": 0},
  "activeFiltre": true
}
```

> **Piège vérifié :** `range` > 100 ne renvoie pas d'erreur — l'API **retombe
> silencieusement à 20 résultats**. Le script plafonne à 100 et loggue un warning.

Le DTO serveur accepte 34 critères (révélés par un message d'erreur 500 sur un champ
inconnu) : `lieux`, `fonctions`, `salaireMinimum`, `niveauEtude`, `dureeStage`,
`anciennetePublication`, `typesTeletravail`, etc.

---

## B. Carte des champs

L'API renvoie du JSON : les « sélecteurs » sont des clés, **stables par construction**
— aucune classe CSS générée dynamiquement, donc aucune fragilité de type `css-1x2y3z`.

| Champ de sortie | Clé JSON (principale) | Fallback | Exemple réel |
|---|---|---|---|
| `id_offre` | `id` | `numeroOffre` sans le `W` | `179407255` |
| `numero_offre` | `numeroOffre` | — (**clé de déduplication**) | `179407255W` |
| `url` | reconstruit depuis `numeroOffre` | — | `…/detail-offre/179407255W` |
| `titre` | `intitule` | `intituleSurbrillance` (contient des `<em>`) | `Business Developer F/H` |
| `entreprise` | `nomCommercial` | vide si `offreConfidentielle: true` | `FED BUSINESS` |
| `localisation` | `lieuTexte` | `latitude`/`longitude` | `Lille - 59` |
| `departement` | parsé de `lieuTexte` (après ` - `) | — | `59` |
| `type_contrat` | `typeContrat` (code) → table | — | `101887` → `CDD` |
| `duree_contrat_mois` | `contractDuration` | — | `6` |
| `salaire` | `salaireTexte` | — | `50 - 55 k€ brut annuel` |
| `teletravail` | `idNomTeletravail` | — | `20949` → `Non`, sinon `Oui` |
| `date_publication` | `datePublication` | `dateValidation` | `2026-09-11` |
| `description` | `texteOffre` | — | ⚠️ **tronqué à 283 car.** |
| `secteur_activite` | `secteurActivite` | `secteurActiviteParent` | `101514` |
| `latitude` / `longitude` | `latitude` / `longitude` | vides si `localisable: false` | `50.6318417` |

### Référentiel `typeContrat`

Extrait de l'enum `Jt` du bundle `main-WSTTYCDC.js` (13 codes, tous mappés) :

| Code | Libellé | | Code | Libellé |
|---|---|---|---|---|
| 101887 | CDD | | 597137 | Alternance – apprentissage (CDD) |
| 101888 | CDI | | 597138 | Alternance – professionnalisation (CDD) |
| 597171 | Stage | | 597139 | Alternance – apprentissage (CDI) |
| 101930 | Intérim | | 597140 | Alternance – professionnalisation (CDI) |
| 20053 | Alternance (**parent**) | | 597141 | CDI intérimaire |
| 101889 | Mission intérim | | 102458 / 102459 | Scolaire / Professionnel |

> `20053` est un **code parent** : le passer en filtre remonte aussi 597137/38/39/40.
> Vérifié empiriquement — c'est pourquoi 3 codes suffisent à couvrir CDD+stage+alternance.

### Deux limites honnêtes

1. **La description est tronquée à 283 caractères** par l'API de recherche (vérifié sur
   100 offres : toutes finissent par `...`). Le texte intégral n'existe que sur l'endpoint
   détail, **protégé par DataDome**. Le contourner sortirait du cadre légal : le scraper
   exporte donc le teaser. Il reste exploitable pour du tri et du matching de mots-clés.
2. **Les libellés fins de télétravail** (codes 20765/66/67) sont chargés via un référentiel
   dynamique absent du bundle. On applique la règle du site lui-même, lue dans son template
   Angular : `idNomTeletravail === 20949 ? "Non" : "Oui"`.

---

## C. Stratégie retenue : API JSON

Ordre de préférence demandé : **API JSON** > JSON-LD/state > requests+BS4 > Selenium.
On est sur le premier niveau, le moins coûteux.

La page de recherche est une SPA Angular (aucune offre dans le HTML initial), donc BS4
seul est inopérant et Selenium coûterait ~2 s/page. L'API sous-jacente est publique, non
authentifiée, renvoie 100 offres structurées par requête et n'applique aucun rate limiting.
**250 offres qualifiées en 54 s et 32 requêtes**, sans navigateur.

---

## D. Filtre métier

`job_filter.py` — regex + normalisation, aucun appel LLM.

**Normalisation** (dans l'ordre) : réparation du mojibake → aplatissement de l'écriture
inclusive → suppression des mentions H/F → minuscules → suppression des accents.

Les trois premières étapes ne sont pas cosmétiques, elles ont été ajoutées après avoir
mesuré leur impact sur 100 offres réelles :

- **Mojibake** — APEC renvoie de l'UTF-8 double-encodé sur ~1 % des titres
  (`Charge(e) dâ\x80\x99affaires`). Sans réparation, l'offre est perdue. Corrigé par
  aller-retour `latin-1` → `utf-8`, déclenché seulement si le motif est détecté.
- **Écriture inclusive** — 10 % des titres (`Conseiller(ère)`, `Consultant.e`,
  `Chargé(e)`). Sans aplatissement, `Conseiller(ère) Clientèle` ne matche rien.
- **Mentions de genre** — `F/H`, `(h/f)`, `H/F/X` sur la quasi-totalité des titres.

**15 familles retenues** : business developer, SDR, BDR, account executive, account
manager, key account manager, commercial sédentaire, commercial itinérant/terrain,
chargé d'affaires, ingénieur commercial, technico-commercial, inside sales, chef de
secteur, attaché commercial, + un filet générique `commercial`.

**8 familles exclues** (l'exclusion l'emporte toujours) : assistanat/ADV, immobilier,
développement logiciel (`développeur full stack` ≠ `business developer`), direction
commerciale, marketing/communication, chargé d'affaires non commercial (HSE, juridique,
RH), achats, retail/magasin.

Chaque offre exporte sa `categorie_metier`, ce qui rend le filtre auditable a posteriori.

---

## E. Utilisation

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp .env.example .env

./.venv/bin/python apec_scraper.py --dry-run          # teste l'API, n'exporte rien
./.venv/bin/python apec_scraper.py                     # collecte complète
./.venv/bin/python apec_scraper.py --new-only          # quotidien : seulement les nouvelles
./.venv/bin/python apec_scraper.py --since-days 7      # fenêtre glissante
./.venv/bin/python apec_scraper.py --keywords "business developer" --max-pages 1
```

| Option | Effet |
|---|---|
| `--dry-run` | 1 requête : valide l'enveloppe JSON, la présence des champs, le classifieur et les codes contrat. **Sort en erreur si le contrat d'API a changé.** |
| `--new-only` | N'exporte que les offres absentes de `.state/seen_offers.json` |
| `--since-days N` | Ne garde que les offres publiées dans les N derniers jours |
| `--max-pages N` | Plafonne la pagination par mot-clé |
| `--keywords …` | Surcharge la liste des 16 mots-clés |
| `-v` | Logs DEBUG |

**Codes de sortie :** `0` OK · `1` échec de collecte · `2` collecte OK mais **alerte qualité**.

**Robustesse :** délai aléatoire 0,8–2 s, rotation de 4 user-agents, 4 retries avec
backoff exponentiel, backoff long (10–120 s) sur 403/429, écriture d'état atomique
(un cron tué en cours ne corrompt pas le fichier), déduplication par `numeroOffre`.

**Sorties :** `data/apec_commercial_<timestamp>.csv` (UTF-8 BOM, ouvrable dans Excel FR)
et `.json`, 22 colonnes.

### Automatisation quotidienne

```bash
# crontab -e — tous les jours à 7h05
5 7 * * * cd /chemin/vers/apec-scraper && ./.venv/bin/python apec_scraper.py --new-only --since-days 3 >> logs/cron.log 2>&1
```

`--new-only` combiné à `--since-days 3` donne un flux quotidien de nouveautés tout en
absorbant un run raté la veille. Le fichier d'état grossit d'environ 250 entrées par
collecte complète ; pense à le purger une fois par an.

---

## F. Points de fragilité

Classés par probabilité de casse, avec le signal de détection.

| # | Ce qui casse | Probabilité | Détection |
|---|---|---|---|
| 1 | **Codes `typeContrat` modifiés** — le référentiel APEC évolue | Moyenne | `type_contrat` = `INCONNU_<code>` → le contrôle qualité sort en **code 2**, et `--dry-run` l'affiche |
| 2 | **`range: 100` réduit** — l'API dégrade silencieusement, sans erreur | Moyenne | Chute brutale du volume collecté à nombre de requêtes constant |
| 3 | **DataDome étendu à `rechercheOffre`** | Moyenne | HTTP 403 + corps `geo.captcha-delivery.com` → loggué explicitement. **Si ça arrive, le scraper s'arrête : ne pas contourner.** |
| 4 | **Renommage de clés JSON** (`intitule`, `lieuTexte`…) | Faible | Taux de champs vides > 20 % → alerte par champ, code 2 |
| 5 | **Endpoint déplacé** (`/cms/webservices/…`) | Faible | HTTP 404 sur toutes les requêtes, 0 offre → code 1 |
| 6 | **Dérive du filtre métier** (nouveaux intitulés à la mode) | Certaine, lente | Surveiller la part de `commercial_generique` (~38 % aujourd'hui) et le ratio `rejets_filtre / brut` (~75 % aujourd'hui) |
| 7 | **Mojibake qui change de forme** | Faible | Titres avec `Ã`/`â` dans le CSV de sortie |

Le **contrôle qualité intégré** mesure à chaque run le taux de vide des 7 champs
critiques (`titre`, `entreprise`, `localisation`, `type_contrat`, `date_publication`,
`description`, `url`) contre le seuil `APEC_EMPTY_FIELD_THRESHOLD` (20 % par défaut)
et signale tout code contrat inconnu. En cron, surveille le code de sortie `2`.

**Référence de santé au 2026-09-16 :** 32 requêtes → 2090 offres brutes → 282 doublons
écartés → 1558 rejets métier → **250 offres uniques**, 0 % de champs vides, 0 échec, 54 s.
Un écart important sur ces ratios est le signal le plus fiable que quelque chose a bougé.

---

## Cadre légal

- `robots.txt` d'APEC : **aucune règle `Disallow`** (vérifié le 2026-09-16). Les chemins
  utilisés sont autorisés.
- Seul l'endpoint de recherche public est appelé. L'endpoint détail protégé par DataDome
  **n'est pas contourné** — c'est un choix assumé, qui coûte la description intégrale.
- Rythme volontairement inférieur à ce que l'infrastructure tolère.
- Les offres collectées contiennent des données d'entreprises, pas de données personnelles.
  Si tu recontactes les recruteurs, le RGPD s'applique à ton traitement en aval.
