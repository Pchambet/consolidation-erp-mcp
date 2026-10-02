"""La mise en scène : générer les trois sources, et corriger un écart à la source pour le voir disparaître.

`generer` écrit le FEC et le jeu de données de l'entrepôt, et sème Odoo si une instance répond.
`corriger` fait ce qu'un utilisateur métier ferait pour un écart : il modifie la source concernée,
pas l'entrepôt. Après une nouvelle actualisation, l'écart doit avoir disparu, et lui seul.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from . import entrepot, fec
from .erp_c import ecrire_dataset
from .monde import Facture, Monde, construire, normaliser_nom, renommer_commandes, verite_en_dict
from .odoo import ClientOdoo, ErreurOdoo, exporter_instantane, lire, semer
from .pipeline import Config


def odoo_depuis_monde(monde: Monde) -> dict[str, Any]:
    """La même structure que `odoo.lire`, fabriquée sans Odoo. Sert aux tests et à la génération hors ligne."""
    parts = monde.partenaires + monde.doublons_odoo
    id_part = {p.ref: i for i, p in enumerate(parts, start=1)}
    unites = {"Units": 1.0, "Dozens": 12.0}
    commandes, n_ligne = [], 0
    for i, c in enumerate(monde.commandes, start=1):
        lignes = []
        for l in c.lignes:
            n_ligne += 1
            lignes.append(
                {
                    "id": n_ligne,
                    "sku": l.sku,
                    "qty": l.qty,
                    "uom": l.unite,
                    "facteur_base": unites[l.unite],
                    "price_unit": l.prix_unitaire,
                }
            )
        commandes.append(
            {
                "id": i,
                "name": c.ref,
                "partner_id": id_part[c.partenaire_ref],
                "date_order": f"{c.date.isoformat()} 09:00:00",
                "currency": c.devise,
                "amount_untaxed": c.total,
                "state": "purchase",
                "write_date": f"{c.date.isoformat()} 09:00:00",
                "lines": lignes,
            }
        )
    return {
        "partners": [
            {"id": id_part[p.ref], "ref": p.ref, "name": p.nom, "vat": p.vat, "write_date": "2026-09-01 08:00:00"} for p in parts
        ],
        "products": [
            {"id": i, "sku": p.sku, "name": p.nom, "uom_achat": p.unite_achat} for i, p in enumerate(monde.produits, start=1)
        ],
        "orders": commandes,
    }


def generer(cfg: Config, avec_odoo: bool = True) -> dict[str, Any]:
    monde = construire()
    mode_odoo = "hors ligne (structure fabriquée, sans Odoo)"
    if avec_odoo:
        try:
            client = ClientOdoo(cfg.odoo_url, cfg.odoo_base, cfg.odoo_login, cfg.odoo_mot_de_passe)
            renommer_commandes(monde, semer(client, monde))
            exporter_instantane(lire(client), cfg.instantane)
            mode_odoo = f"Odoo {client.version} semé, instantané écrit"
        except ErreurOdoo as exc:
            raise ErreurOdoo(f"{exc}. Utiliser --sans-odoo pour générer sans Odoo.") from exc
    else:
        exporter_instantane(odoo_depuis_monde(monde), cfg.instantane)
    fec.ecrire_fec(monde, cfg.fec)
    ecrire_dataset(monde, cfg.dataset_c)
    cfg.verite.write_text(json.dumps(verite_en_dict(monde), ensure_ascii=False, indent=1), encoding="utf-8")
    return {"odoo": mode_odoo, "fec": str(cfg.fec), "entrepot_c": str(cfg.dataset_c), "ecarts_plantes": len(monde.verite_terrain)}


# ------------------------------------------------------------------------------------------ corrections


class CorrectionImpossible(ValueError):
    pass


def corriger(cfg: Config, ecart_id: str) -> str:
    """Corrige à la source l'écart demandé. Seules trois natures d'écart se corrigent hors d'Odoo."""
    ligne = entrepot._lignes(cfg.entrepot, "SELECT regle, po_ref, detail FROM ecarts WHERE ecart_id = ?", [ecart_id])
    if not ligne:
        raise CorrectionImpossible(f"écart {ecart_id!r} introuvable")
    regle, po_ref, detail = ligne[0]["regle"], ligne[0]["po_ref"], json.loads(ligne[0]["detail"])
    if regle == "facture_en_double":
        return _retirer_doublon(cfg, po_ref)
    if regle == "commande_sans_facture":
        return _saisir_facture(cfg, po_ref)
    if regle == "unite_incoherente":
        return _corriger_unite(cfg, po_ref, detail["A"]["sku"])
    raise CorrectionImpossible(
        f"la règle {regle!r} ne se corrige pas depuis cette maquette (elle touche Odoo ou demande une décision humaine)"
    )


def _ligne_fec(chemin: Path) -> tuple[str, str, list[list[str]]]:
    texte, _ = fec._decoder(chemin.read_bytes())
    return texte, "\t", list(csv.reader(io.StringIO(texte), delimiter="\t"))


def _ecrire_fec_lignes(chemin: Path, lignes: list[list[str]]) -> None:
    tampon = io.StringIO(newline="")
    csv.writer(tampon, delimiter="\t", lineterminator="\r\n").writerows(lignes)
    chemin.write_bytes(tampon.getvalue().encode("cp1252"))


def _retirer_doublon(cfg: Config, po_ref: str) -> str:
    _, _, lignes = _ligne_fec(cfg.fec)
    en_tete, corps = lignes[0], lignes[1:]
    i_num, i_lib = en_tete.index("EcritureNum"), en_tete.index("EcritureLib")
    numeros = sorted({int(l[i_num]) for l in corps if f" {po_ref} " in l[i_lib] + " "})
    if len(numeros) < 2:
        raise CorrectionImpossible(f"{po_ref} n'a pas de facture en double dans le FEC")
    dernier = numeros[-1]
    _ecrire_fec_lignes(cfg.fec, [en_tete] + [l for l in corps if int(l[i_num]) != dernier])
    return f"écriture {dernier} supprimée du FEC (doublon de la facture de {po_ref})"


def _saisir_facture(cfg: Config, po_ref: str) -> str:
    c = entrepot._lignes(
        cfg.entrepot,
        """SELECT c.total_devise, c.devise, c.code_odoo, d.pays, p.nom
           FROM fact_commande c JOIN dim_partenaire d USING (partner_key)
           JOIN pont_partenaire p ON p.systeme = 'A' AND p.code = c.code_odoo
           WHERE c.po_ref = ?""",
        [po_ref],
    )[0]
    _, _, lignes = _ligne_fec(cfg.fec)
    numero = max(int(l[2]) for l in lignes[1:]) + 1
    facture = Facture(
        ecriture_num=numero,
        piece_ref=f"FA26-{numero:04d}",
        po_ref=po_ref,
        date=cfg.as_of,
        aux=c["code_odoo"],
        lib_b=normaliser_nom(c["nom"]),
        ht_devise=c["total_devise"],
        devise=c["devise"],
        avec_tva=c["pays"] == "FR",
    )
    nouvelles = [[l[k] for k in fec.COLONNES] for l in fec.lignes_de_facture(facture)]
    _ecrire_fec_lignes(cfg.fec, lignes + nouvelles)
    return f"facture {facture.piece_ref} saisie en comptabilité pour {po_ref}"


def _corriger_unite(cfg: Config, po_ref: str, sku: str) -> str:
    donnees = json.loads(cfg.dataset_c.read_text(encoding="utf-8"))
    for r in donnees["receipts"]:
        if r["po_ref"] == po_ref:
            for l in r["lines"]:
                if l["sku"] == sku:
                    l["qty"] = l["qty"] * 12
    cfg.dataset_c.write_text(json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8")
    return f"quantité de {sku} multipliée par 12 sur la réception de {po_ref} dans l'entrepôt"
