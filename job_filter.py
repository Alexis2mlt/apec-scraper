"""
Filtre metier : classification des intitules d'offres commerciales.
Aucun appel LLM, aucune dependance externe : regex + normalisation.
"""
import re
import unicodedata

# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

# Mentions de genre a retirer : (H/F), F/H, M/F, H-F, (h/f/x)...
_GENDER = re.compile(r"[\(\[\-/\s](?:h[\s/\-.]*f|f[\s/\-.]*h|m[\s/\-.]*f|f[\s/\-.]*m)(?:[\s/\-.]*x)?[\)\]\s.\-/]*", re.I)
_NONWORD = re.compile(r"[^a-z0-9]+")

# Ecriture inclusive accolee a un mot : Charge(e), Conseiller(ere), Consultant.e
_INCLUSIF_PAR = re.compile(r"(?<=\w)\((?:ere|\u00e8re|trice|euse|es|ve|se|ne|le|s|e)\)", re.I)
_INCLUSIF_DOT = re.compile(r"(?<=\w)\.(?:trice|euse|ve|se|e)\b", re.I)

# Mojibake observe dans les donnees APEC : UTF-8 double-encode ("d\u00e2\u20ac\u2122affaires")
_MOJIBAKE_HINT = re.compile(r"[\u00c2\u00c3\u00e2][\u0080-\u00bf\u201a-\u2122]")


def fix_mojibake(text: str) -> str:
    """Repare un texte UTF-8 lu comme du latin-1. Sans effet si le texte est sain."""
    if not text or not _MOJIBAKE_HINT.search(text):
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def normalize(text: str) -> str:
    """mojibake repare + ecriture inclusive aplatie + minuscule + sans accents + sans H/F."""
    if not text:
        return ""
    text = fix_mojibake(text)
    text = _INCLUSIF_PAR.sub("", text)
    text = _INCLUSIF_DOT.sub("", text)
    t = unicodedata.normalize("NFD", text)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = t.lower()
    t = _GENDER.sub(" ", t)
    t = _NONWORD.sub(" ", t)
    return " ".join(t.split())


# --------------------------------------------------------------------------
# Patterns d'inclusion : (nom_canonique, regex)
# \b aux bornes pour eviter les faux positifs sur des sous-chaines.
# --------------------------------------------------------------------------
INCLUDE = [
    ("business_developer",   r"\bbusiness\s+develop(?:er|ment|peur)\b|\bbizdev\b|\bbiz\s+dev\b|\bdeveloppeur\s+(?:commercial|d\s?affaires|de\s+business)\b"),
    ("sdr",                  r"\bsdr\b|\bsales\s+development\s+representative\b"),
    ("bdr",                  r"\bbdr\b|\bbusiness\s+development\s+representative\b"),
    ("account_executive",    r"\baccount\s+executive\b|\bae\s+saas\b"),
    ("key_account_manager",  r"\bkey\s+account\s+manager\b|\bkam\b|\bresponsable\s+grands?\s+comptes?\b|\bchargee?\s+de\s+clientele\s+grands?\s+comptes?\b|\bgrands?\s+comptes?\b"),
    ("account_manager",      r"\baccount\s+manager\b|\bsales\s+account\b"),
    ("commercial_sedentaire", r"\bcommercia(?:l|ux|le)\s+sedentaires?\b|\bsedentaire\b|\bvendeur\s+sedentaire\b"),
    ("commercial_itinerant", r"\bcommercia(?:l|ux|le)\s+(?:itinerants?|terrain|route)\b|\bcommercia(?:l|ux|le)\s+exterieur\b"),
    ("charge_affaires",      r"\bcharge[e]?s?\s+d\s?affaires?\b|\bresponsable\s+d\s?affaires?\b"),
    ("ingenieur_commercial", r"\bingenieur[e]?s?\s+(?:commercia(?:l|ux|le)|d\s?affaires?|des\s+ventes)\b"),
    ("technico_commercial",  r"\btechnico\s+commercia(?:l|ux|le)\b|\bcommercia(?:l|ux|le)\s+technique\b"),
    ("inside_sales",         r"\binside\s+sales\b|\bsales\s+(?:representative|rep|executive|manager|advisor|developer|developpeur|consultant)\b|\bsales\b"),
    ("chef_secteur",         r"\bchef\s+de\s+secteur\b|\bresponsable\s+de\s+secteur\b"),
    ("attache_commercial",   r"\battache[e]?s?\s+commercia(?:l|ux|le)\b|\bdelegue[e]?s?\s+commercia(?:l|ux|le)\b"),
    ("commercial_generique", r"\bcommercia(?:l|ux|le)\b|\bconseiller\s+(?:commercial|de\s+vente|clientele)\b|\bchargee?\s+de\s+developpement\s+commercial\b|\bprospection\b"),
]
INCLUDE = [(name, re.compile(rx)) for name, rx in INCLUDE]

# --------------------------------------------------------------------------
# Patterns d'exclusion : appliques AVANT l'inclusion, ils gagnent toujours.
# --------------------------------------------------------------------------
EXCLUDE = [
    # support / back-office, pas de portefeuille client
    ("assistanat",      r"\bassistant[e]?s?\b|\bsecretaire\b|\badv\b|\badministration\s+des\s+ventes\b|\bgestionnaire\s+adv\b|\bsupport\s+commercial\b"),
    # secteurs explicitement hors cible
    ("immobilier",      r"\bimmobili(?:er|ere)\b|\bnegociateur\b|\btransaction\s+immobiliere\b|\bagent\s+immobilier\b"),
    # metiers techniques qui empruntent le mot "developpeur"
    ("dev_logiciel",    r"\bdeveloppeur\s+(?:web|full\s?stack|back|front|java|python|php|mobile|logiciel|informatique)\b|\bfull\s?stack\b|\bdevops\b"),
    # direction / management pur, hors liste demandee
    ("direction",       r"\bdirecteur\b|\bdirectrice\b|\bdirection\s+commerciale\b|\bvp\s+sales\b|\bhead\s+of\s+sales\b"),
    # fonctions marketing / communication
    ("marketing",       r"\bmarketing\b|\bcommunication\b|\bcommunity\s+manager\b|\btrafic\s+manager\b"),
    # metiers d'exploitation portant "charge d'affaires" sans dimension commerciale
    ("charge_af_tech",  r"\bcharge[e]?s?\s+d\s?affaires?\s+(?:reglementaires?|qualite|hse|juridiques?|sociales?|rh)\b"),
    # achats : miroir du commercial, hors cible
    ("achats",          r"\bacheteur\b|\bachats?\b|\bapprovisionn"),
    # caisse / vente au detail
    ("retail_vente",    r"\bhote[sse]*\s+de\s+caisse\b|\bemploye[e]?s?\s+(?:libre\s+service|commercia(?:l|ux|le))\b|\ben\s+magasin\b|\bboutique\b|\brayon\b|\bmerchandis"),
]
EXCLUDE = [(name, re.compile(rx)) for name, rx in EXCLUDE]


def classify(intitule: str):
    """
    Retourne un dict :
      is_commercial : bool
      categorie     : nom canonique du metier, ou None
      motif_exclusion : nom de la regle d'exclusion, ou None
      titre_normalise : la chaine normalisee (utile pour auditer)
    """
    norm = normalize(intitule)
    if not norm:
        return {"is_commercial": False, "categorie": None,
                "motif_exclusion": "titre_vide", "titre_normalise": ""}

    for name, rx in EXCLUDE:
        if rx.search(norm):
            return {"is_commercial": False, "categorie": None,
                    "motif_exclusion": name, "titre_normalise": norm}

    for name, rx in INCLUDE:
        if rx.search(norm):
            return {"is_commercial": True, "categorie": name,
                    "motif_exclusion": None, "titre_normalise": norm}

    return {"is_commercial": False, "categorie": None,
            "motif_exclusion": "aucun_match", "titre_normalise": norm}
