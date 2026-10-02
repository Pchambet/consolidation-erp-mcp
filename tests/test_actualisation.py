from __future__ import annotations

import duckdb
import pytest

from consolidation_erp import demo, entrepot
from consolidation_erp.fec import FecInvalide
from consolidation_erp.pipeline import actualiser


def _ids(cfg, regle=None):
    con = duckdb.connect(str(cfg.entrepot), read_only=True)
    try:
        if regle:
            return {i for (i,) in con.execute("SELECT ecart_id FROM ecarts WHERE regle = ?", [regle]).fetchall()}
        return {i for (i,) in con.execute("SELECT ecart_id FROM ecarts").fetchall()}
    finally:
        con.close()


def test_l_actualisation_est_idempotente(cfg):
    premiere = actualiser(cfg)
    avant = _ids(cfg)
    seconde = actualiser(cfg)
    assert premiere["premiere_construction"] and not seconde["premiere_construction"]
    assert seconde["nouveaux"] == [] and seconde["resolus"] == []
    assert _ids(cfg) == avant  # mêmes identifiants, pas seulement le même nombre
    assert seconde["ecarts"] == premiere["ecarts"] == 22


def test_les_identifiants_d_ecart_sont_stables_d_une_construction_a_l_autre(cfg, tmp_path):
    actualiser(cfg)
    a = _ids(cfg)
    cfg.entrepot.unlink()
    actualiser(cfg)
    assert _ids(cfg) == a


def test_la_premiere_detection_traverse_les_actualisations(cfg):
    actualiser(cfg)
    con = duckdb.connect(str(cfg.entrepot), read_only=True)
    avant = dict(con.execute("SELECT ecart_id, premiere_detection FROM ecarts").fetchall())
    con.close()
    import datetime as dt
    actualiser(type(cfg)(**{**cfg.__dict__, "as_of": cfg.as_of + dt.timedelta(days=3)}))
    con = duckdb.connect(str(cfg.entrepot), read_only=True)
    apres = dict(con.execute("SELECT ecart_id, premiere_detection FROM ecarts").fetchall())
    con.close()
    communs = set(avant) & set(apres)
    assert communs and all(apres[i] == avant[i] for i in communs)


def test_une_construction_qui_echoue_laisse_l_ancien_entrepot_intact(cfg):
    actualiser(cfg)
    avant = cfg.entrepot.read_bytes()
    cfg.fec.write_text("colonne_absurde\n1\n", encoding="utf-8")
    with pytest.raises(FecInvalide):
        actualiser(cfg)
    assert cfg.entrepot.read_bytes() == avant
    assert not cfg.entrepot.with_suffix(".construction.duckdb").exists()


@pytest.mark.parametrize("regle", ["facture_en_double", "commande_sans_facture", "unite_incoherente"])
def test_corriger_a_la_source_fait_disparaitre_cet_ecart_et_lui_seul(cfg, regle):
    actualiser(cfg)
    avant = _ids(cfg)
    cible = sorted(_ids(cfg, regle))[0]
    demo.corriger(cfg, cible)
    bilan = actualiser(cfg)
    apres = _ids(cfg)
    assert avant - apres == {cible}, "l'écart corrigé doit être le seul à disparaître"
    assert apres - avant == set(), "la correction ne doit pas en créer de nouveau"
    assert bilan["resolus"] == [cible] and bilan["nouveaux"] == []


def test_une_regle_qui_touche_odoo_ne_se_corrige_pas_depuis_la_maquette(actualise):
    cible = sorted(_ids(actualise, "partenaire_double"))[0]
    with pytest.raises(demo.CorrectionImpossible, match="ne se corrige pas"):
        demo.corriger(actualise, cible)


def test_les_indicateurs_bougent_apres_correction(cfg):
    actualiser(cfg)
    avant = {i["cle"]: i["valeur"] for i in entrepot.indicateurs(cfg.entrepot)}
    demo.corriger(cfg, sorted(_ids(cfg, "commande_sans_facture"))[0])
    actualiser(cfg)
    apres = {i["cle"]: i["valeur"] for i in entrepot.indicateurs(cfg.entrepot)}
    assert apres["ecarts_ouverts"] == avant["ecarts_ouverts"] - 1
    assert apres["recu_non_facture_eur"] < avant["recu_non_facture_eur"]
    assert apres["taux_conformite_3_voies"] > avant["taux_conformite_3_voies"]
