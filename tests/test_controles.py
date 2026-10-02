"""Les contrôles sont testés contre la vérité de terrain : les écarts plantés dans le monde.

C'est le test qui compte. Un contrôle qui rate un écart est inutile, un contrôle qui en invente est
pire, parce qu'on cesse de lui faire confiance. Les deux sont vérifiés ici : on exige l'égalité exacte
entre ce qui a été planté et ce qui est détecté.
"""

from __future__ import annotations

import duckdb

from consolidation_erp import demo, entrepot, fec
from consolidation_erp.monde import AS_OF, construire
from consolidation_erp.pipeline import Config, actualiser


def _detectes(cfg: Config) -> set[tuple[str, str]]:
    con = duckdb.connect(str(cfg.entrepot), read_only=True)
    try:
        return set(con.execute("SELECT regle, cle FROM ecarts").fetchall())
    finally:
        con.close()


def test_les_controles_retrouvent_exactement_les_ecarts_plantes(actualise, monde):
    plantes = {(e.regle, e.cle) for e in monde.verite_terrain}
    detectes = _detectes(actualise)
    assert plantes - detectes == set(), "écarts plantés non détectés"
    assert detectes - plantes == set(), "écarts détectés qui n'ont pas été plantés"


def test_un_monde_sain_ne_produit_aucun_ecart(tmp_path):
    sain = construire(planter=False)
    cfg = Config(donnees=tmp_path, odoo_mode="instantane", erp_c_url=None, as_of=AS_OF)
    # on écrit les trois sources du monde sain à la main, sans passer par generer (qui plante)
    from consolidation_erp.erp_c import ecrire_dataset
    from consolidation_erp.odoo import exporter_instantane

    fec.ecrire_fec(sain, cfg.fec)
    ecrire_dataset(sain, cfg.dataset_c)
    exporter_instantane(demo.odoo_depuis_monde(sain), cfg.instantane)
    actualiser(cfg)
    assert _detectes(cfg) == set()


def test_une_fiche_en_double_ne_fait_pas_passer_une_facture_pour_un_mauvais_fournisseur(actualise):
    """Les commandes passées sur la deuxième fiche Odoo sont facturées sur le compte de la première.
    Sans dédoublonnage par numéro de TVA, chacune serait signalée à tort."""
    con = duckdb.connect(str(actualise.entrepot), read_only=True)
    try:
        doubles = con.execute("SELECT count(*) FROM fact_commande WHERE code_odoo IN ('F0901', 'F0902')").fetchone()[0]
        mauvais = con.execute("SELECT count(*) FROM ecarts WHERE regle = 'facture_mauvais_fournisseur'").fetchone()[0]
    finally:
        con.close()
    assert doubles >= 2
    assert mauvais == 1


def test_un_compte_auxiliaire_ambigu_n_est_pas_rattache_au_hasard(actualise):
    con = duckdb.connect(str(actualise.entrepot), read_only=True)
    try:
        methode, cle = con.execute(
            "SELECT methode, partner_key FROM pont_partenaire WHERE systeme = 'B' AND code = 'F0777'"
        ).fetchone()
    finally:
        con.close()
    assert methode == "ambigu" and cle is None


def test_une_ecriture_desequilibree_est_signalee(cfg):
    lignes = cfg.fec.read_bytes().decode("cp1252").splitlines()
    colonnes = lignes[0].split("\t")
    i_debit = colonnes.index("Debit")
    premiere = lignes[1].split("\t")
    premiere[i_debit] = "9999,99"  # un débit qui ne se retrouve pas au crédit
    lignes[1] = "\t".join(premiere)
    cfg.fec.write_bytes(("\r\n".join(lignes) + "\r\n").encode("cp1252"))
    actualiser(cfg)
    assert any(r == "ecriture_desequilibree" for r, _ in _detectes(cfg))


def test_les_ecarts_citent_leurs_sources(actualise):
    r = entrepot.ecarts_ouverts(actualise.entrepot, limite=100)
    for e in r["ecarts"]:
        explication = entrepot.expliquer_ecart(actualise.entrepot, e["ecart_id"])
        enregistrements = explication["provenance"]["enregistrements"]
        assert set(enregistrements) <= {"A", "B", "C"} and enregistrements, e["regle"]


def test_l_explication_d_un_ecart_de_facture_donne_la_ligne_du_fec(actualise):
    r = entrepot.ecarts_ouverts(actualise.entrepot, regle="ecart_montant", limite=5)
    detail = entrepot.expliquer_ecart(actualise.entrepot, r["ecarts"][0]["ecart_id"])
    b = detail["provenance"]["enregistrements"]["B"]
    ligne = b["ligne_fichier"]
    assert actualise.fec.read_bytes().decode("cp1252").splitlines()[ligne - 1].split("\t")[2] == str(b["ecriture"])
