"""Les deux livrables tirés de l'entrepôt : le tableau de bord HTML et les CSV pour la BI."""

from __future__ import annotations

import csv
import datetime as dt
import re
from pathlib import Path

from consolidation_erp import entrepot, export_bi, tableau_de_bord

RACINE = Path(__file__).resolve().parents[1]


def _csv(dossier: Path, nom: str) -> list[dict[str, str]]:
    return list(csv.DictReader((dossier / f"{nom}.csv").open(encoding="utf-8-sig")))


def test_le_tableau_de_bord_est_autonome_et_affiche_les_bons_chiffres(actualise, tmp_path):
    chemin = tableau_de_bord.ecrire(actualise, tmp_path / "index.html")
    page = chemin.read_text(encoding="utf-8")
    assert not re.search(r'(src|href)="https?://', page), "la page doit s'ouvrir hors ligne"
    assert "<script src" not in page
    valeurs = {i["cle"]: i["valeur"] for i in entrepot.indicateurs(actualise.entrepot)}
    assert f">{valeurs['ecarts_ouverts']}<" in page
    assert "prefers-color-scheme: dark" in page and 'data-theme="dark"' in page
    assert page.count('class="puce"') == 22  # une puce de gravité par écart, avec son icône et son libellé
    assert "<details><summary>Voir les données" in page  # la vue tableau existe sous chaque graphique


def test_chaque_gravite_a_une_icone_et_un_libelle_en_plus_de_la_couleur():
    assert {g: (i, l) for g, (i, l, _) in tableau_de_bord.GRAVITES.items()} == {
        "haute": ("▲", "Haute"),
        "moyenne": ("◆", "Moyenne"),
        "basse": ("●", "Basse"),
    }


def test_les_valeurs_du_tableau_de_bord_sont_echappees(actualise):
    donnees = tableau_de_bord._donnees(actualise.entrepot)
    donnees["ecarts"][0]["resume"] = "<script>alert(1)</script>"
    assert "<script>alert(1)" not in tableau_de_bord.page(donnees)


def test_l_export_ecrit_un_csv_par_table_en_utf8_avec_bom(actualise, tmp_path):
    ecrits = export_bi.exporter(actualise, tmp_path)
    assert {p.stem for p in ecrits} == set(export_bi.TABLES)
    assert (tmp_path / "ecarts.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    assert len(_csv(tmp_path, "ecarts")) == 22
    assert {r["recue"] for r in _csv(tmp_path, "rapprochement")} <= {"0", "1"}


def test_les_indicateurs_recalcules_depuis_les_csv_egalent_ceux_de_l_entrepot(actualise, tmp_path):
    """C'est ce que fait Power BI avec les mesures DAX : recalculer les indicateurs depuis les CSV.
    La logique doit donner les mêmes chiffres que l'entrepôt."""
    export_bi.exporter(actualise, tmp_path)

    def d(s: str) -> dt.date | None:
        return dt.date.fromisoformat(s) if s else None

    as_of = d(_csv(tmp_path, "parametres")[0]["as_of"])
    limite = as_of - dt.timedelta(days=30)
    ecarts, commandes, rap = _csv(tmp_path, "ecarts"), _csv(tmp_path, "fact_commande"), _csv(tmp_path, "rapprochement")
    anciennes = [c for c in commandes if d(c["date_commande"]) < limite]
    delais = [
        (d(r["date_facture"]) - d(r["date_commande"])).days
        for r in rap
        if r["facturee"] == "1" and d(r["date_facture"]) >= d(r["date_commande"])
    ]
    pont_b = [p for p in _csv(tmp_path, "pont_partenaire") if p["systeme"] == "B"]
    recalcule = {
        "ecarts_ouverts": len(ecarts),
        "ecarts_gravite_haute": sum(e["gravite"] == "haute" for e in ecarts),
        "montant_expose_eur": round(sum(float(e["montant_eur"] or 0) for e in ecarts)),
        "taux_conformite_3_voies": round(100 * sum(c["nb_ecarts"] == "0" for c in anciennes) / len(anciennes), 1),
        "recu_non_facture_eur": round(
            sum(
                float(r["total_eur"])
                for r in rap
                if r["recue"] == "1" and r["facturee"] == "0" and d(r["premiere_reception"]) < limite
            )
        ),
        "delai_commande_facture_jours": round(sum(delais) / len(delais), 1),
        "fournisseurs_fiches_multiples": sum(int(p["fiches_odoo"]) > 1 for p in _csv(tmp_path, "dim_partenaire")),
        "rattachement_compta_pct": round(100 * sum(p["partner_key"] != "" for p in pont_b) / len(pont_b), 1),
    }
    attendu = {i["cle"]: i["valeur"] for i in entrepot.indicateurs(actualise.entrepot)}
    assert recalcule == {k: attendu[k] for k in recalcule}


def test_la_table_de_verification_de_la_doc_powerbi_donne_les_valeurs_de_l_entrepot(actualise):
    doc = (RACINE / "powerbi" / "README.md").read_text(encoding="utf-8")
    valeurs = {i["cle"]: i["valeur"] for i in entrepot.indicateurs(actualise.entrepot)}
    for attendu in ("| 22 |", "| 8 |", "12 007", "81,8 %", "4 495", "17,4 jours", "97,5 %"):
        assert attendu in doc.replace(" ", " "), attendu
    assert valeurs["ecarts_ouverts"] == 22 and valeurs["montant_expose_eur"] == 12007
    assert valeurs["taux_conformite_3_voies"] == 81.8 and valeurs["recu_non_facture_eur"] == 4495
