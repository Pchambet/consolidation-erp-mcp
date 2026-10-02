"""L'entrepôt de données : DuckDB, un fichier, reconstruit en entier à chaque actualisation.

Le fichier est construit à côté puis substitué d'un coup (`os.replace`) : un lecteur voit toujours
l'état d'avant ou l'état d'après, jamais un état à moitié construit. Reconstruire depuis les sources
plutôt que patcher rend l'actualisation idempotente : la relancer donne le même résultat.

Ce module contient aussi tout ce qui *lit* l'entrepôt pour un humain ou pour Claude : la liste des
écarts, l'explication d'un écart, les indicateurs, et l'exécution de requêtes en lecture seule.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from .monde import TAUX_CHF_EUR
from .regles import PAR_NOM, REGLES, Regle

SQL_MODELE = Path(__file__).parent / "sql" / "modele.sql"

SCHEMA_STAGING = """
CREATE TABLE stg_a_partner(id INTEGER, ref VARCHAR, name VARCHAR, vat VARCHAR, write_date VARCHAR);
CREATE TABLE stg_a_product(id INTEGER, sku VARCHAR, name VARCHAR, uom_achat VARCHAR);
CREATE TABLE stg_a_order(id INTEGER, name VARCHAR, partner_id INTEGER, date_order TIMESTAMP, currency VARCHAR,
                         amount_untaxed DOUBLE, state VARCHAR, write_date VARCHAR);
CREATE TABLE stg_a_order_line(id INTEGER, order_id INTEGER, sku VARCHAR, qty DOUBLE, uom VARCHAR,
                              facteur_base DOUBLE, price_unit DOUBLE);
CREATE TABLE stg_b_ecriture(ecriture_num INTEGER, journal VARCHAR, date DATE, compte VARCHAR, aux_num VARCHAR,
                            aux_lib VARCHAR, piece_ref VARCHAR, libelle VARCHAR, debit DOUBLE, credit DOUBLE,
                            montant_devise DOUBLE, idevise VARCHAR, ligne_fichier INTEGER);
CREATE TABLE stg_c_supplier(code VARCHAR, name VARCHAR, vat VARCHAR, country VARCHAR, updated_at VARCHAR);
CREATE TABLE stg_c_item(sku VARCHAR, name VARCHAR, base_unit VARCHAR);
CREATE TABLE stg_c_receipt(receipt_ref VARCHAR, po_ref VARCHAR, supplier_code VARCHAR, received_at DATE);
CREATE TABLE stg_c_receipt_line(receipt_ref VARCHAR, sku VARCHAR, qty DOUBLE, unit VARCHAR);
"""

SCHEMA_ECARTS = """
CREATE TABLE ecarts(
    ecart_id VARCHAR PRIMARY KEY, regle VARCHAR, libelle_regle VARCHAR, gravite VARCHAR, responsable VARCHAR,
    cle VARCHAR, po_ref VARCHAR, partner_key VARCHAR, montant_eur DOUBLE, resume VARCHAR, action VARCHAR,
    detail VARCHAR, premiere_detection DATE
);
CREATE TABLE sources_meta(systeme VARCHAR, mode VARCHAR, lignes INTEGER, detail VARCHAR, charge_le TIMESTAMP);
"""


@dataclass
class Sources:
    """Ce que la consolidation a lu, système par système, avec la manière dont chacun a été lu."""

    odoo: dict[str, Any]
    fec: list[dict]
    entrepot: dict[str, list[dict]]
    modes: dict[str, str]


def _charger(con: duckdb.DuckDBPyConnection, s: Sources) -> None:
    a, c = s.odoo, s.entrepot
    con.executemany(
        "INSERT INTO stg_a_partner VALUES (?,?,?,?,?)",
        [(p["id"], p["ref"], p["name"], p["vat"], p["write_date"]) for p in a["partners"]],
    )
    con.executemany(
        "INSERT INTO stg_a_product VALUES (?,?,?,?)", [(p["id"], p["sku"], p["name"], p["uom_achat"]) for p in a["products"]]
    )
    con.executemany(
        "INSERT INTO stg_a_order VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                o["id"],
                o["name"],
                o["partner_id"],
                o["date_order"],
                o["currency"],
                o["amount_untaxed"],
                o["state"],
                o["write_date"],
            )
            for o in a["orders"]
        ],
    )
    con.executemany(
        "INSERT INTO stg_a_order_line VALUES (?,?,?,?,?,?,?)",
        [
            (l["id"], o["id"], l["sku"], l["qty"], l["uom"], l["facteur_base"], l["price_unit"])
            for o in a["orders"]
            for l in o["lines"]
        ],
    )
    con.executemany(
        "INSERT INTO stg_b_ecriture VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                e["ecriture_num"],
                e["journal"],
                e["date"],
                e["compte"],
                e["aux_num"],
                e["aux_lib"],
                e["piece_ref"],
                e["libelle"],
                e["debit"],
                e["credit"],
                e["montant_devise"],
                e["idevise"],
                e["ligne_fichier"],
            )
            for e in s.fec
        ],
    )
    con.executemany(
        "INSERT INTO stg_c_supplier VALUES (?,?,?,?,?)",
        [(x["code"], x["name"], x["vat"], x["country"], x["updated_at"]) for x in c["suppliers"]],
    )
    con.executemany("INSERT INTO stg_c_item VALUES (?,?,?)", [(x["sku"], x["name"], x["base_unit"]) for x in c["items"]])
    con.executemany(
        "INSERT INTO stg_c_receipt VALUES (?,?,?,?)",
        [(r["receipt_ref"], r["po_ref"], r["supplier_code"], r["received_at"]) for r in c["receipts"]],
    )
    con.executemany(
        "INSERT INTO stg_c_receipt_line VALUES (?,?,?,?)",
        [(r["receipt_ref"], l["sku"], l["qty"], l["unit"]) for r in c["receipts"] for l in r["lines"]],
    )
    for l in [l for r in c["receipts"] for l in r["lines"]]:
        if l["unit"] != "pce":
            raise ValueError(f"l'entrepôt a livré une unité inconnue : {l['unit']!r} (attendu : pce)")
    maintenant = dt.datetime.now().replace(microsecond=0)
    meta = [
        (
            "A",
            s.modes["A"],
            len(a["orders"]) + len(a["partners"]) + len(a["products"]),
            f"{len(a['partners'])} fournisseurs, {len(a['products'])} produits, {len(a['orders'])} commandes",
        ),
        ("B", s.modes["B"], len(s.fec), f"{len(s.fec)} écritures du journal des achats"),
        (
            "C",
            s.modes["C"],
            len(c["receipts"]) + len(c["suppliers"]) + len(c["items"]),
            f"{len(c['suppliers'])} fournisseurs, {len(c['items'])} articles, {len(c['receipts'])} réceptions",
        ),
    ]
    con.executemany("INSERT INTO sources_meta VALUES (?,?,?,?,?)", [(*m, maintenant) for m in meta])


def _resume(regle: Regle, ligne: dict[str, Any]) -> str:
    valeurs = {k: (", ".join(map(str, v)) if isinstance(v, list) else v) for k, v in ligne.items()}
    valeurs["suite_facture"] = ", et pourtant déjà facturée" if ligne.get("facturee") else ""
    return regle.gabarit.format(**valeurs)


def _identifiant(regle: str, ligne: dict[str, Any]) -> str:
    sous_cle = ligne.get("sku") or ligne.get("piece_ref") or ""
    return "E" + hashlib.sha1(f"{regle}|{ligne['cle']}|{sous_cle}".encode()).hexdigest()[:8]


def _ecarts(con: duckdb.DuckDBPyConnection, as_of: dt.date, deja_vus: dict[str, dt.date]) -> None:
    for regle in REGLES:
        curseur = con.execute(regle.sql)
        colonnes = [d[0] for d in curseur.description]
        for valeurs in curseur.fetchall():
            ligne = dict(zip(colonnes, valeurs, strict=True))
            ecart_id = _identifiant(regle.nom, ligne)
            con.execute(
                "INSERT INTO ecarts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    ecart_id,
                    regle.nom,
                    regle.libelle,
                    ligne.get("gravite") or regle.gravite,
                    regle.responsable,
                    ligne["cle"],
                    ligne.get("po_ref"),
                    ligne.get("partner_key"),
                    ligne.get("montant_eur"),
                    _resume(regle, ligne),
                    regle.action,
                    ligne["detail"] if isinstance(ligne["detail"], str) else json.dumps(ligne["detail"]),
                    deja_vus.get(ecart_id, as_of),
                ],
            )


def _deja_vus(chemin: Path) -> dict[str, dt.date]:
    if not chemin.exists():
        return {}
    try:
        con = duckdb.connect(str(chemin), read_only=True)
        try:
            return {i: d for i, d in con.execute("SELECT ecart_id, premiere_detection FROM ecarts").fetchall()}
        finally:
            con.close()
    except duckdb.Error:
        return {}


def construire(chemin: Path, sources: Sources, as_of: dt.date) -> dict[str, Any]:
    """Reconstruit l'entrepôt depuis les sources et le substitue à l'ancien. Rend un compte rendu."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    suivant = chemin.with_suffix(".construction.duckdb")
    suivant.unlink(missing_ok=True)
    deja_vus = _deja_vus(chemin)
    avant = set(deja_vus)
    con = duckdb.connect(str(suivant))
    try:
        con.execute("CREATE TABLE parametres AS SELECT ?::DATE AS as_of", [as_of])
        con.execute(
            "CREATE TABLE dim_devise AS SELECT * FROM (VALUES ('EUR', 1.0), ('CHF', ?)) t(devise, taux_eur)", [TAUX_CHF_EUR]
        )
        con.execute(SCHEMA_STAGING)
        con.execute(SCHEMA_ECARTS)
        _charger(con, sources)
        con.execute(SQL_MODELE.read_text(encoding="utf-8"))
        _ecarts(con, as_of, deja_vus)
        apres = {i for (i,) in con.execute("SELECT ecart_id FROM ecarts").fetchall()}
        compte = con.execute("SELECT count(*) FROM ecarts").fetchone()[0]  # type: ignore[index]
        con.execute("CHECKPOINT")
    except Exception:
        con.close()
        suivant.unlink(missing_ok=True)
        raise
    con.close()
    os.replace(suivant, chemin)
    return {
        "ecarts": compte,
        "nouveaux": sorted(apres - avant),
        "resolus": sorted(avant - apres),
        "premiere_construction": not avant,
    }


# ------------------------------------------------------------------------------------------ lecture


def ouvrir_lecture(chemin: Path) -> duckdb.DuckDBPyConnection:
    """Une connexion en lecture seule, sans accès aux fichiers ni au réseau, configuration verrouillée."""
    return duckdb.connect(
        str(chemin),
        read_only=True,
        config={"enable_external_access": False, "lock_configuration": True},
    )


class RequeteRefusee(ValueError):
    """La requête est refusée ou a échoué. Le message dit pourquoi, pour que Claude corrige."""


INTERDITS = re.compile(
    r"\b(read_\w+|glob|getenv|current_setting|pragma|attach|detach|copy|install|load|export|import|call|set|"
    r"create|drop|alter|insert|update|delete|truncate|checkpoint|vacuum)\b",
    re.IGNORECASE,
)


def _serialisable(x: Any) -> Any:
    if isinstance(x, (dt.date, dt.datetime)):
        return x.isoformat()
    if isinstance(x, Decimal):
        return float(x)
    if isinstance(x, (list, tuple)):
        return [_serialisable(i) for i in x]
    return x


def requete_lecture_seule(chemin: Path, sql: str, limite: int = 100, delai_s: float = 10.0) -> dict[str, Any]:
    """Exécute un SELECT et rien d'autre. Trois barrières indépendantes : le fichier est ouvert en lecture
    seule et sans accès externe, une seule instruction de type SELECT est acceptée, et la durée est bornée."""
    if not 1 <= limite <= 1000:
        raise RequeteRefusee("limite entre 1 et 1000 lignes")
    trouve = INTERDITS.search(sql)
    if trouve:
        raise RequeteRefusee(f"mot interdit dans une requête de lecture : « {trouve.group(0)} »")
    con = ouvrir_lecture(chemin)
    minuteur = threading.Timer(delai_s, con.interrupt)
    try:
        instructions = con.extract_statements(sql)
        if len(instructions) != 1:
            raise RequeteRefusee("une seule requête à la fois")
        if instructions[0].type != duckdb.StatementType.SELECT:
            raise RequeteRefusee("seules les requêtes SELECT sont autorisées")
        minuteur.start()
        curseur = con.execute(sql)
        colonnes = [d[0] for d in curseur.description]
        lignes = curseur.fetchmany(limite + 1)
    except duckdb.InterruptException as exc:
        raise RequeteRefusee(f"requête interrompue après {delai_s:g} s") from exc
    except duckdb.Error as exc:
        raise RequeteRefusee(str(exc).splitlines()[0]) from exc
    finally:
        minuteur.cancel()
        con.close()
    tronque = len(lignes) > limite
    return {
        "colonnes": colonnes,
        "lignes": [[_serialisable(v) for v in l] for l in lignes[:limite]],
        "tronque": tronque,
        "note": f"résultat limité à {limite} lignes" if tronque else None,
    }


def _lignes(chemin: Path, sql: str, params: list | None = None) -> list[dict[str, Any]]:
    con = ouvrir_lecture(chemin)
    try:
        curseur = con.execute(sql, params or [])
        colonnes = [d[0] for d in curseur.description]
        return [{k: _serialisable(v) for k, v in zip(colonnes, l, strict=True)} for l in curseur.fetchall()]
    finally:
        con.close()


def ecarts_ouverts(chemin: Path, gravite: str | None = None, regle: str | None = None, limite: int = 20) -> dict[str, Any]:
    if gravite not in (None, "haute", "moyenne", "basse"):
        raise RequeteRefusee("gravité : haute, moyenne ou basse")
    if regle is not None and regle not in PAR_NOM:
        raise RequeteRefusee(f"règle inconnue. Règles : {', '.join(PAR_NOM)}")
    filtres, params = [], []
    if gravite:
        filtres.append("gravite = ?")
        params.append(gravite)
    if regle:
        filtres.append("regle = ?")
        params.append(regle)
    ou = ("WHERE " + " AND ".join(filtres)) if filtres else ""
    ordre = "CASE gravite WHEN 'haute' THEN 0 WHEN 'moyenne' THEN 1 ELSE 2 END, montant_eur DESC NULLS LAST, ecart_id"
    total = _lignes(chemin, f"SELECT count(*) AS n FROM ecarts {ou}", params)[0]["n"]
    lignes = _lignes(
        chemin,
        f"SELECT ecart_id, regle, gravite, responsable, po_ref, montant_eur, resume FROM ecarts {ou} ORDER BY {ordre} LIMIT ?",
        [*params, limite],
    )
    return {"total": total, "affiches": len(lignes), "ecarts": lignes}


def expliquer_ecart(chemin: Path, ecart_id: str) -> dict[str, Any]:
    trouve = _lignes(chemin, "SELECT * FROM ecarts WHERE ecart_id = ?", [ecart_id])
    if not trouve:
        raise RequeteRefusee(f"écart {ecart_id!r} introuvable : utiliser ecarts_ouverts pour lister les identifiants")
    e = trouve[0]
    regle = PAR_NOM[e["regle"]]
    detail = json.loads(e["detail"])
    contexte: dict[str, Any] = {}
    if e["po_ref"]:
        rapprochement = _lignes(chemin, "SELECT * FROM v_rapprochement WHERE po_ref = ?", [e["po_ref"]])
        if rapprochement:
            contexte["rapprochement_en_trois_voies"] = rapprochement[0]
    if e["partner_key"]:
        p = _lignes(
            chemin, "SELECT nom, identifiant, pays, fiches_odoo FROM dim_partenaire WHERE partner_key = ?", [e["partner_key"]]
        )
        if p:
            contexte["fournisseur"] = p[0]
    return {
        "ecart_id": e["ecart_id"],
        "regle": e["regle"],
        "ce_que_controle_la_regle": regle.libelle,
        "constat": e["resume"],
        "gravite": e["gravite"],
        "responsable": e["responsable"],
        "montant_en_jeu_eur": e["montant_eur"],
        "action_recommandee": e["action"],
        "premiere_detection": e["premiere_detection"],
        "provenance": {
            "systemes": {"A": "Odoo 17 (achats)", "B": "comptabilité (FEC)", "C": "entrepôt (API REST)"},
            "enregistrements": detail,
        },
        **contexte,
    }


INDICATEURS: list[dict[str, str]] = [
    dict(
        cle="ecarts_ouverts",
        libelle="Écarts ouverts",
        unite="nombre",
        definition="Nombre d'écarts détectés par les contrôles à la dernière actualisation.",
        sql="SELECT count(*) FROM ecarts",
    ),
    dict(
        cle="ecarts_gravite_haute",
        libelle="Écarts de gravité haute",
        unite="nombre",
        definition="Écarts qui peuvent déclencher un mauvais paiement : facture en double, mauvais fournisseur, devise, unité.",
        sql="SELECT count(*) FROM ecarts WHERE gravite = 'haute'",
    ),
    dict(
        cle="montant_expose_eur",
        libelle="Montant exposé",
        unite="EUR",
        definition="Somme des montants en jeu des écarts, convertis en EUR au taux fixe de la maquette. Un même montant peut figurer dans plusieurs écarts.",
        sql="SELECT round(coalesce(sum(montant_eur), 0)) FROM ecarts",
    ),
    dict(
        cle="taux_conformite_3_voies",
        libelle="Commandes conformes de bout en bout",
        unite="%",
        definition="Part des commandes de plus de 30 jours sans aucun écart entre commande, réception et facture.",
        sql="""SELECT round(100.0 * (1 - count(DISTINCT e.po_ref) / count(DISTINCT c.po_ref)), 1)
                FROM fact_commande c LEFT JOIN ecarts e ON e.po_ref = c.po_ref
                WHERE c.date_commande < (SELECT as_of FROM parametres) - INTERVAL 30 DAY""",
    ),
    dict(
        cle="recu_non_facture_eur",
        libelle="Reçu depuis plus de 30 jours et non facturé",
        unite="EUR",
        definition="Montant des commandes dont la marchandise est arrivée depuis plus de 30 jours et qui n'ont aucune facture : la charge à provisionner.",
        sql="""SELECT round(coalesce(sum(total_eur), 0)) FROM v_rapprochement
                WHERE recue AND NOT facturee AND premiere_reception < (SELECT as_of FROM parametres) - INTERVAL 30 DAY""",
    ),
    dict(
        cle="delai_commande_facture_jours",
        libelle="Délai moyen commande à facture",
        unite="jours",
        definition="Moyenne, sur les commandes facturées, du nombre de jours entre la date de commande et la date de facture.",
        sql="""SELECT round(avg(date_diff('day', date_commande, date_facture)), 1) FROM v_rapprochement
                WHERE facturee AND date_facture >= date_commande""",
    ),
    dict(
        cle="fournisseurs_fiches_multiples",
        libelle="Fournisseurs à fiches multiples dans Odoo",
        unite="nombre",
        definition="Fournisseurs (un numéro de TVA) présents sous plusieurs codes dans Odoo.",
        sql="SELECT count(*) FROM dim_partenaire WHERE fiches_odoo > 1",
    ),
    dict(
        cle="rattachement_compta_pct",
        libelle="Comptes auxiliaires rattachés sans ambiguïté",
        unite="%",
        definition="Part des comptes fournisseurs de la comptabilité que l'on sait rattacher à un fournisseur, par code ou par nom unique.",
        sql="SELECT round(100.0 * count(*) FILTER (WHERE partner_key IS NOT NULL) / count(*), 1) FROM pont_partenaire WHERE systeme = 'B'",
    ),
]


def indicateurs(chemin: Path) -> list[dict[str, Any]]:
    con = ouvrir_lecture(chemin)
    try:
        sortie = []
        for i in INDICATEURS:
            valeur = con.execute(i["sql"]).fetchone()[0]  # type: ignore[index]
            sortie.append({k: i[k] for k in ("cle", "libelle", "unite", "definition")} | {"valeur": _serialisable(valeur)})
        return sortie
    finally:
        con.close()


def decrire_modele(chemin: Path) -> dict[str, Any]:
    lignes = _lignes(
        chemin,
        """SELECT table_name, table_type, string_agg(column_name || ' ' || data_type, ', ' ORDER BY ordinal_position) AS colonnes
           FROM information_schema.columns JOIN information_schema.tables USING (table_schema, table_name)
           WHERE table_schema = 'main' AND table_name NOT LIKE 'stg_%' AND table_name <> 'parametres'
           GROUP BY table_name, table_type ORDER BY table_name""",
    )
    return {
        "tables": lignes,
        "usage": "Dimensions : dim_*. Faits : fact_*. pont_partenaire relie les codes de chaque système à un fournisseur. "
        "v_rapprochement donne la vue commande, réception, facture. ecarts liste les écarts. Les tables stg_* sont les données brutes lues dans chaque système.",
    }


def sources(chemin: Path) -> list[dict[str, Any]]:
    return _lignes(chemin, "SELECT systeme, mode, lignes, detail, charge_le FROM sources_meta ORDER BY systeme")
