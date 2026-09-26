from __future__ import annotations

import pytest

from consolidation_erp import demo
from consolidation_erp.monde import AS_OF, construire
from consolidation_erp.pipeline import Config, actualiser


@pytest.fixture(scope="session")
def monde():
    return construire()


@pytest.fixture()
def cfg(tmp_path):
    """Trois sources générées hors ligne dans un dossier jetable, Odoo lu depuis l'instantané."""
    c = Config(donnees=tmp_path, odoo_mode="instantane", erp_c_url=None, as_of=AS_OF)
    demo.generer(c, avec_odoo=False)
    return c


@pytest.fixture()
def actualise(cfg):
    actualiser(cfg)
    return cfg
