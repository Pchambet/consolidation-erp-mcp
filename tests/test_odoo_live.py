"""Contre le vrai Odoo 17 de la maquette (docker compose). Ignoré si l'instance ne répond pas ou si elle n'a
pas été semée par `consolidation generer`."""

from __future__ import annotations

import pytest

from consolidation_erp import odoo
from consolidation_erp.monde import construire
from consolidation_erp.pipeline import Config

pytestmark = pytest.mark.odoo


@pytest.fixture(scope="module")
def client():
    cfg = Config()
    try:
        c = odoo.ClientOdoo(cfg.odoo_url, cfg.odoo_base, cfg.odoo_login, cfg.odoo_mot_de_passe)
    except odoo.ErreurOdoo as exc:
        pytest.skip(f"Odoo injoignable : {exc}")
    if not c.kw("purchase.order", "search_count", [["origin", "=", odoo.ORIGINE_DEMO]]):
        pytest.skip("l'instance n'est pas semée : consolidation generer")
    return c


def test_le_vrai_odoo_donne_les_memes_commandes_que_le_monde(client):
    monde = construire()
    lu = odoo.lire(client)
    par_nom = {o["name"]: o for o in lu["orders"]}
    assert set(par_nom) == {c.ref for c in monde.commandes}
    for c in monde.commandes[:25]:
        o = par_nom[c.ref]
        assert o["currency"] == c.devise
        assert o["amount_untaxed"] == pytest.approx(c.total, abs=0.02)
        assert len(o["lines"]) == len(c.lignes)


def test_le_vrai_odoo_donne_le_facteur_douze_pour_les_douzaines(client):
    lignes = [l for o in odoo.lire(client)["orders"] for l in o["lines"]]
    assert any(l["uom"] == "Dozens" and l["facteur_base"] == 12.0 for l in lignes)
    assert all(l["facteur_base"] == 1.0 for l in lignes if l["uom"] == "Units")


def test_le_vrai_odoo_a_bien_les_deux_fiches_de_fournisseur_en_double(client):
    partenaires = odoo.lire(client)["partners"]
    par_tva: dict[str, list[str]] = {}
    for p in partenaires:
        par_tva.setdefault(p["vat"], []).append(p["ref"])
    doubles = {tva: refs for tva, refs in par_tva.items() if len(refs) > 1}
    assert len(doubles) == 2
