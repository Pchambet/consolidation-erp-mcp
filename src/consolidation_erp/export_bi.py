"""L'export du modèle en étoile en CSV, pour Power BI ou Tableau.

Un CSV par table, en UTF-8 avec BOM (Power BI le reconnaît sans réglage), virgule comme séparateur,
point comme séparateur décimal, dates en AAAA-MM-JJ. Le CSV est le contrat entre l'entrepôt et
l'outil de BI : pour rafraîchir le tableau de bord, on relance `consolidation actualiser` puis
`consolidation exporter-bi`, et on actualise la source de données dans l'outil.
"""

from __future__ import annotations

import csv
from pathlib import Path

from . import entrepot
from .pipeline import RACINE, Config

TABLES = {
    "dim_partenaire": "SELECT * FROM dim_partenaire ORDER BY partner_key",
    "dim_produit": "SELECT * FROM dim_produit ORDER BY sku",
    "dim_date": "SELECT * FROM dim_date ORDER BY date",
    "dim_devise": "SELECT * FROM dim_devise ORDER BY devise",
    # nb_ecarts : drapeau précalculé, pour qu'une mesure BI n'ait pas à joindre la table des écarts.
    "fact_commande": """SELECT c.*, coalesce(e.n, 0) AS nb_ecarts FROM fact_commande c
                        LEFT JOIN (SELECT po_ref, count(*) AS n FROM ecarts GROUP BY po_ref) e USING (po_ref)
                        ORDER BY po_ref""",
    "fact_commande_ligne": "SELECT * FROM fact_commande_ligne ORDER BY po_ref, odoo_ligne_id",
    "fact_reception": "SELECT * FROM fact_reception ORDER BY receipt_ref",
    "fact_reception_ligne": "SELECT * FROM fact_reception_ligne ORDER BY receipt_ref, sku",
    "fact_facture": "SELECT * FROM fact_facture ORDER BY ecriture_num",
    "rapprochement": """SELECT po_ref, partner_key, date_commande, devise, total_devise, total_eur, premiere_reception,
                               date_facture, facture_ht_devise, facture_devise, recue::INTEGER AS recue, facturee::INTEGER AS facturee
                        FROM v_rapprochement ORDER BY po_ref""",
    "pont_partenaire": "SELECT systeme, code, nom, partner_key, methode, confiance FROM pont_partenaire ORDER BY systeme, code",
    # Le détail JSON reste dans l'entrepôt ; l'outil de BI reçoit trois drapeaux plus faciles à filtrer.
    "ecarts": """SELECT ecart_id, regle, libelle_regle, gravite, responsable, cle, po_ref, partner_key, montant_eur,
                        resume, action, premiere_detection,
                        json_exists(detail, '$.A')::INTEGER AS cite_odoo,
                        json_exists(detail, '$.B')::INTEGER AS cite_compta,
                        json_exists(detail, '$.C')::INTEGER AS cite_entrepot
                 FROM ecarts ORDER BY ecart_id""",
    "parametres": "SELECT * FROM parametres",
}


def exporter(cfg: Config, sortie: str | Path) -> list[Path]:
    dossier = Path(sortie)
    if not dossier.is_absolute():
        dossier = RACINE / dossier
    dossier.mkdir(parents=True, exist_ok=True)
    con = entrepot.ouvrir_lecture(cfg.entrepot)
    ecrits: list[Path] = []
    try:
        for nom, sql in TABLES.items():
            curseur = con.execute(sql)
            colonnes = [d[0] for d in curseur.description]
            chemin = dossier / f"{nom}.csv"
            with chemin.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f, lineterminator="\r\n")
                w.writerow(colonnes)
                w.writerows([entrepot._serialisable(v) for v in ligne] for ligne in curseur.fetchall())
            ecrits.append(chemin)
    finally:
        con.close()
    return ecrits
