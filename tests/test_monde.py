from __future__ import annotations

from collections import Counter

from consolidation_erp.monde import construire, normaliser_nom


def test_le_monde_est_deterministe():
    a, b = construire(), construire()
    assert [c.ref for c in a.commandes] == [c.ref for c in b.commandes]
    assert [(e.regle, e.cle) for e in a.verite_terrain] == [(e.regle, e.cle) for e in b.verite_terrain]
    assert [f.ht_devise for f in a.factures] == [f.ht_devise for f in b.factures]


def test_le_monde_plante_vingt_deux_ecarts_repartis_sur_douze_regles(monde):
    par_regle = Counter(e.regle for e in monde.verite_terrain)
    assert len(monde.verite_terrain) == 22
    assert len(par_regle) == 12
    assert par_regle["commande_sans_facture"] == 3


def test_le_monde_sain_ne_plante_rien():
    sain = construire(planter=False)
    assert sain.verite_terrain == [] and sain.doublons_odoo == []
    assert len(sain.partenaires) == 44


def test_aucune_commande_plantee_ne_sert_deux_fois(monde):
    # deux écarts de commande ne visent jamais la même commande : chaque écart planté est isolé,
    # sans quoi on ne saurait pas dire lequel un contrôle a trouvé
    cles = [e.cle for e in monde.verite_terrain if e.regle not in ("partenaire_double", "partenaire_ambigu", "reception_sans_commande")]
    assert len(cles) == len(set(cles))


def test_normaliser_nom_ignore_accents_ponctuation_et_forme_juridique():
    assert normaliser_nom("Mécanique Salève SA") == "MECANIQUE SALEVE"
    assert normaliser_nom("Fonderie du Rhône SAS") == "FONDERIE DU RHONE"
    assert normaliser_nom("CABLAGE BORNES") == "CABLAGE BORNES"
