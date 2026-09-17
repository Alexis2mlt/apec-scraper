#!/usr/bin/env python3
"""
Scraper APEC - offres commerciales en CDD / stage / alternance, France entiere.

Source : endpoint JSON public https://www.apec.fr/cms/webservices/rechercheOffre
Conforme au robots.txt d'APEC (aucune regle Disallow, verifie le 2026-09-16).
Concu pour un run quotidien automatise (cron / launchd / GitHub Actions).
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from job_filter import classify, fix_mojibake

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_URL = "https://www.apec.fr/cms/webservices/rechercheOffre"

# Limite dure constatee : au-dela de 100, l'API retombe SILENCIEUSEMENT a 20.
PAGE_SIZE = 100
MAX_START_INDEX = 1_000  # garde-fou anti-boucle sur pagination profonde

MIN_DELAY = float(os.getenv("APEC_MIN_DELAY", 0.8))
MAX_DELAY = float(os.getenv("APEC_MAX_DELAY", 2.0))
MAX_RETRIES = int(os.getenv("APEC_MAX_RETRIES", 4))
TIMEOUT = int(os.getenv("APEC_TIMEOUT", 25))
OUTPUT_DIR = Path(os.getenv("APEC_OUTPUT_DIR", "./data"))
EMPTY_THRESHOLD = float(os.getenv("APEC_EMPTY_FIELD_THRESHOLD", 20))

STATE_FILE = Path(".state/seen_offers.json")

# Codes verifies dans le bundle Angular d'APEC (main-*.js, enum Jt)
TYPE_CONTRAT = {
    101887: "CDD",
    101888: "CDI",
    597171: "STAGE",
    101930: "INTERIM",
    20053: "ALTERNANCE",
    101889: "MISSION_INTERIM",
    597141: "CDI_INTERIMAIRE",
    597137: "ALTERNANCE_APPRENTISSAGE",       # CDD_ALTERNANCE_CONTRAT_APPRENTISSAGE
    597138: "ALTERNANCE_PROFESSIONNALISATION",  # CDD_ALTERNANCE_CONTRAT_PROFESSIONNALISATION
    597139: "ALTERNANCE_APPRENTISSAGE_CDI",    # CDI_ALTERNANCE_CONTRAT_APPRENTISSAGE
    597140: "ALTERNANCE_PROFESSIONNALISATION_CDI",  # CDI_ALTERNANCE_CONTRAT_PROFESSIONNALISATION
    102458: "SCOLAIRE",
    102459: "PROFESSIONNEL",
}
# Filtres envoyes a l'API. 20053 est un code PARENT : il remonte aussi
# 597137 / 597138 / 597139 (verifie empiriquement).
FILTRE_CONTRATS = [101887, 597171, 20053]

# Regle lue dans le template Angular : idNomTeletravail === 20949 ? "Non" : "Oui"
TELETRAVAIL_NON = 20949

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
]

MOTS_CLES = [
    "commercial sédentaire", "commercial itinérant", "commercial terrain",
    "business developer", "sales development representative",
    "business development representative", "account executive", "account manager",
    "chargé d'affaires", "ingénieur commercial", "technico-commercial",
    "key account manager", "inside sales", "chef de secteur",
    "attaché commercial", "développement commercial",
]

# Schema de sortie : ordre des colonnes CSV
CHAMPS = [
    "id_offre", "numero_offre", "url", "titre", "categorie_metier", "entreprise",
    "localisation", "departement", "type_contrat", "code_type_contrat",
    "duree_contrat_mois", "salaire", "teletravail", "date_publication",
    "date_validation", "description", "latitude", "longitude",
    "secteur_activite", "offre_confidentielle", "mot_cle_source", "date_collecte",
]
# Champs dont on surveille le taux de vide (livrable F)
CHAMPS_CRITIQUES = ["titre", "entreprise", "localisation", "type_contrat",
                    "date_publication", "description", "url"]

log = logging.getLogger("apec")


# ---------------------------------------------------------------------------
# Couche reseau
# ---------------------------------------------------------------------------
class ApecClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    def _headers(self) -> dict:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "Origin": "https://www.apec.fr",
            "Referer": "https://www.apec.fr/candidat/recherche-emploi.html/emploi",
        }

    def search(self, mot_cle: str, start_index: int, page_size: int = PAGE_SIZE) -> dict | None:
        """Une page de resultats. None si echec definitif apres retries."""
        if page_size > PAGE_SIZE:
            log.warning("page_size %d > %d : l'API retomberait a 20, plafonne.",
                        page_size, PAGE_SIZE)
            page_size = PAGE_SIZE

        payload = {
            "motsCles": mot_cle,
            "typeClient": "CADRE",
            "typesContrat": FILTRE_CONTRATS,
            "sorts": [{"type": "DATE", "direction": "DESCENDING"}],
            "pagination": {"range": page_size, "startIndex": start_index},
            "activeFiltre": True,
        }

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = self.session.post(API_URL, json=payload,
                                      headers=self._headers(), timeout=TIMEOUT)
            except requests.RequestException as e:
                wait = min(2 ** attempt + random.random(), 60)
                log.warning("[%s@%d] erreur reseau (%s) - retry %d/%d dans %.1fs",
                            mot_cle, start_index, type(e).__name__, attempt, MAX_RETRIES, wait)
                time.sleep(wait)
                continue

            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    log.error("[%s@%d] reponse 200 mais JSON invalide (%d octets)",
                              mot_cle, start_index, len(r.content))
                    return None

            if r.status_code in (403, 429):
                # 403 = DataDome (captcha), 429 = rate limit. Backoff long.
                wait = min(10 * attempt + random.uniform(0, 5), 120)
                body = r.text[:120].replace("\n", " ")
                log.warning("[%s@%d] HTTP %d - backoff %.0fs | %s",
                            mot_cle, start_index, r.status_code, wait, body)
                time.sleep(wait)
                continue

            if 500 <= r.status_code < 600:
                wait = min(2 ** attempt + random.random(), 60)
                log.warning("[%s@%d] HTTP %d - retry %d/%d dans %.1fs",
                            mot_cle, start_index, r.status_code, attempt, MAX_RETRIES, wait)
                time.sleep(wait)
                continue

            log.error("[%s@%d] HTTP %d non recuperable : %s",
                      mot_cle, start_index, r.status_code, r.text[:200])
            return None

        log.error("[%s@%d] abandon apres %d tentatives", mot_cle, start_index, MAX_RETRIES)
        return None


# ---------------------------------------------------------------------------
# Transformation
# ---------------------------------------------------------------------------
def _departement(lieu: str) -> str:
    """'Paris 17 - 75' -> '75'. Chaine vide si le motif n'est pas present."""
    if not lieu or " - " not in lieu:
        return ""
    tail = lieu.rsplit(" - ", 1)[-1].strip()
    return tail if tail.isdigit() or tail in ("2A", "2B") else ""


def _teletravail(code) -> str:
    if code is None:
        return "Non renseigné"
    return "Non" if code == TELETRAVAIL_NON else "Oui"


def _iso(dt: str | None) -> str:
    """'2026-09-11T11:46:18.000+0000' -> '2026-09-11'."""
    return dt[:10] if dt else ""


def normaliser(offre: dict, mot_cle: str) -> dict:
    num = offre.get("numeroOffre") or ""
    code_contrat = offre.get("typeContrat")
    duree = offre.get("contractDuration") or 0
    return {
        "id_offre": offre.get("id") or "",
        "numero_offre": num,
        # URL publique de la fiche (page Angular, non scrapee - voir README)
        "url": f"https://www.apec.fr/candidat/recherche-emploi.html/emploi/detail-offre/{num}" if num else "",
        "titre": fix_mojibake(offre.get("intitule") or "").strip(),
        "categorie_metier": "",  # rempli par le classifieur
        "entreprise": fix_mojibake(offre.get("nomCommercial") or "").strip(),
        "localisation": fix_mojibake(offre.get("lieuTexte") or "").strip(),
        "departement": _departement(offre.get("lieuTexte") or ""),
        "type_contrat": TYPE_CONTRAT.get(code_contrat, f"INCONNU_{code_contrat}"),
        "code_type_contrat": code_contrat if code_contrat is not None else "",
        "duree_contrat_mois": duree if duree else "",
        "salaire": fix_mojibake(offre.get("salaireTexte") or "").strip(),
        "teletravail": _teletravail(offre.get("idNomTeletravail")),
        "date_publication": _iso(offre.get("datePublication")),
        "date_validation": _iso(offre.get("dateValidation")),
        # ATTENTION : tronque a ~283 caracteres par l'API. Voir README.
        "description": fix_mojibake(offre.get("texteOffre") or "").strip(),
        "latitude": offre.get("latitude") or "",
        "longitude": offre.get("longitude") or "",
        "secteur_activite": offre.get("secteurActivite") or "",
        "offre_confidentielle": bool(offre.get("offreConfidentielle")),
        "mot_cle_source": mot_cle,
        "date_collecte": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


# ---------------------------------------------------------------------------
# Etat (pour les runs quotidiens incrementaux)
# ---------------------------------------------------------------------------
def charger_etat() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("etat illisible (%s), on repart de zero", e)
        return {}


def sauver_etat(etat: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(etat, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)  # ecriture atomique : un cron tue en cours ne corrompt rien


# ---------------------------------------------------------------------------
# Collecte
# ---------------------------------------------------------------------------
def collecter(client: ApecClient, mots_cles: list[str], since_days: int | None,
              max_pages: int | None = None) -> tuple[list[dict], dict]:
    offres: dict[str, dict] = {}   # numero_offre -> offre (deduplication)
    stats = {"requetes": 0, "brut": 0, "rejetes_filtre": 0, "rejetes_date": 0,
             "doublons": 0, "echecs": 0}

    date_min = None
    if since_days is not None:
        date_min = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%d")
        log.info("filtre date : offres publiees depuis %s", date_min)

    for mot_cle in mots_cles:
        start = 0
        page = 0
        total = None

        while start < MAX_START_INDEX:
            if max_pages is not None and page >= max_pages:
                break

            data = client.search(mot_cle, start)
            stats["requetes"] += 1
            if data is None:
                stats["echecs"] += 1
                break

            resultats = data.get("resultats") or []
            if total is None:
                total = data.get("totalCount", 0)
                log.info("« %s » : %d offres annoncees", mot_cle, total)

            if not resultats:
                break

            for o in resultats:
                stats["brut"] += 1
                num = o.get("numeroOffre")
                if not num:
                    continue
                if num in offres:
                    stats["doublons"] += 1
                    continue

                verdict = classify(o.get("intitule") or "")
                if not verdict["is_commercial"]:
                    stats["rejetes_filtre"] += 1
                    continue

                row = normaliser(o, mot_cle)
                if date_min and row["date_publication"] and row["date_publication"] < date_min:
                    stats["rejetes_date"] += 1
                    continue

                row["categorie_metier"] = verdict["categorie"]
                offres[num] = row

            page += 1
            start += PAGE_SIZE
            if total is not None and start >= total:
                break

            time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    return list(offres.values()), stats


# ---------------------------------------------------------------------------
# Controle qualite (livrable F)
# ---------------------------------------------------------------------------
def controle_qualite(offres: list[dict]) -> bool:
    """Retourne False si un champ critique depasse le seuil de vide."""
    if not offres:
        log.error("CONTROLE QUALITE : 0 offre collectee - le scraper est probablement casse")
        return False

    ok = True
    log.info("--- controle qualite (seuil %.0f%% de vide) ---", EMPTY_THRESHOLD)
    for champ in CHAMPS_CRITIQUES:
        vides = sum(1 for o in offres if not str(o.get(champ, "")).strip())
        taux = 100 * vides / len(offres)
        niveau = log.warning if taux > EMPTY_THRESHOLD else log.info
        niveau("  %-18s %5.1f%% vides (%d/%d)%s", champ, taux, vides, len(offres),
               "  <-- ALERTE" if taux > EMPTY_THRESHOLD else "")
        if taux > EMPTY_THRESHOLD:
            ok = False

    inconnus = sum(1 for o in offres if str(o.get("type_contrat", "")).startswith("INCONNU_"))
    if inconnus:
        log.warning("  %d offres avec un code type_contrat inconnu : le referentiel APEC a bouge", inconnus)
        ok = False
    return ok


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def exporter(offres: list[dict], prefixe: str) -> tuple[Path, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = OUTPUT_DIR / f"{prefixe}_{stamp}"

    csv_path = base.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:  # BOM : Excel FR
        w = csv.DictWriter(f, fieldnames=CHAMPS, extrasaction="ignore")
        w.writeheader()
        w.writerows(offres)

    json_path = base.with_suffix(".json")
    json_path.write_text(json.dumps(offres, ensure_ascii=False, indent=2), encoding="utf-8")
    return csv_path, json_path


# ---------------------------------------------------------------------------
# Dry-run (livrable E)
# ---------------------------------------------------------------------------
def dry_run(client: ApecClient) -> int:
    print("=" * 74)
    print("DRY-RUN : 1 requete, verification du contrat d'API et des champs")
    print("=" * 74)

    data = client.search("business developer", 0, page_size=20)
    if data is None:
        print("ECHEC : aucune reponse exploitable de l'API.")
        return 1

    attendus = {"resultats", "totalCount", "offreFilters"}
    manquants = attendus - set(data)
    print(f"\n[1] Enveloppe JSON : cles={sorted(data)} "
          f"{'OK' if not manquants else 'MANQUANT ' + str(manquants)}")
    if manquants:
        return 1

    resultats = data["resultats"]
    print(f"[2] totalCount={data['totalCount']}  offres recues={len(resultats)}")
    if not resultats:
        print("ECHEC : 0 offre - filtres trop restrictifs ou API modifiee.")
        return 1

    print("\n[3] Presence des champs source sur l'echantillon :")
    sources = ["id", "numeroOffre", "intitule", "nomCommercial", "lieuTexte",
               "salaireTexte", "texteOffre", "datePublication", "typeContrat",
               "idNomTeletravail", "latitude", "longitude"]
    for champ in sources:
        n = sum(1 for o in resultats if o.get(champ) not in (None, ""))
        flag = "OK " if n else "ABSENT"
        print(f"    {flag} {champ:<18} {n}/{len(resultats)}")

    print("\n[4] Classification metier sur l'echantillon :")
    retenus = 0
    for o in resultats:
        v = classify(o.get("intitule") or "")
        if v["is_commercial"]:
            retenus += 1
            if retenus <= 5:
                print(f"    RETENU  [{v['categorie']:<20}] {(o.get('intitule') or '')[:46]}")
    for o in resultats:
        v = classify(o.get("intitule") or "")
        if not v["is_commercial"]:
            print(f"    rejete  [{v['motif_exclusion']:<20}] {(o.get('intitule') or '')[:46]}")
            break
    print(f"    -> {retenus}/{len(resultats)} retenus par le filtre metier")

    print("\n[5] Exemple de ligne normalisee :")
    exemple = normaliser(resultats[0], "business developer")
    exemple["categorie_metier"] = classify(resultats[0].get("intitule") or "")["categorie"]
    for k, v in exemple.items():
        v = str(v)
        print(f"    {k:<22} = {v[:70]}{'...' if len(v) > 70 else ''}")

    print("\n[6] Codes type_contrat rencontres :")
    codes = {}
    for o in resultats:
        c = o.get("typeContrat")
        codes[c] = codes.get(c, 0) + 1
    inconnu = False
    for c, n in sorted(codes.items(), key=lambda x: -x[1]):
        lib = TYPE_CONTRAT.get(c)
        if lib is None:
            inconnu = True
        print(f"    {c} -> {lib or 'INCONNU (referentiel a mettre a jour)'} ({n})")

    print("\n" + "=" * 74)
    print("DRY-RUN OK" if not inconnu else "DRY-RUN OK avec avertissement (code contrat inconnu)")
    print("=" * 74)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(
        description="Scraper APEC - offres commerciales CDD/stage/alternance, France entiere.")
    p.add_argument("--dry-run", action="store_true",
                   help="teste l'API et les champs sur 1 page, sans export")
    p.add_argument("--since-days", type=int, default=None, metavar="N",
                   help="ne garder que les offres publiees dans les N derniers jours")
    p.add_argument("--new-only", action="store_true",
                   help="n'exporter que les offres jamais vues (mode quotidien)")
    p.add_argument("--max-pages", type=int, default=None, metavar="N",
                   help="limite le nombre de pages par mot-cle (tests)")
    p.add_argument("--keywords", nargs="+", default=None, metavar="KW",
                   help="surcharge la liste de mots-cles")
    p.add_argument("--output-prefix", default="apec_commercial")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    client = ApecClient()

    if args.dry_run:
        return dry_run(client)

    mots_cles = args.keywords or MOTS_CLES
    t0 = time.time()
    log.info("demarrage : %d mots-cles, contrats=%s",
             len(mots_cles), [TYPE_CONTRAT[c] for c in FILTRE_CONTRATS])

    offres, stats = collecter(client, mots_cles, args.since_days, args.max_pages)

    etat = charger_etat()
    aujourdhui = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if args.new_only:
        avant = len(offres)
        offres = [o for o in offres if o["numero_offre"] not in etat]
        log.info("mode --new-only : %d nouvelles sur %d collectees", len(offres), avant)

    for o in offres:
        etat.setdefault(o["numero_offre"], aujourdhui)
    sauver_etat(etat)

    log.info("--- statistiques ---")
    log.info("  requetes API        : %d", stats["requetes"])
    log.info("  offres brutes vues  : %d", stats["brut"])
    log.info("  rejets filtre metier: %d", stats["rejetes_filtre"])
    log.info("  rejets date         : %d", stats["rejetes_date"])
    log.info("  doublons ecartes    : %d", stats["doublons"])
    log.info("  echecs requete      : %d", stats["echecs"])
    log.info("  OFFRES RETENUES     : %d", len(offres))

    if not offres:
        log.warning("aucune offre a exporter")
        return 0 if args.new_only else 1

    qualite_ok = controle_qualite(offres)
    csv_path, json_path = exporter(offres, args.output_prefix)
    log.info("export CSV  : %s", csv_path)
    log.info("export JSON : %s", json_path)
    log.info("termine en %.1fs", time.time() - t0)

    return 0 if qualite_ok else 2


if __name__ == "__main__":
    sys.exit(main())
