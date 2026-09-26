"""L'actualisation : lire les trois systèmes, reconstruire l'entrepôt, dire ce qui a changé.

Une seule fonction publique, `actualiser`, appelée telle quelle par la ligne de commande et par le
serveur MCP. Elle est idempotente : la relancer sans que les sources aient bougé donne le même
entrepôt et la même liste d'écarts, avec les mêmes identifiants.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from . import entrepot, fec, odoo
from .erp_c import ClientErpC, creer_app
from .monde import AS_OF

RACINE = Path(__file__).resolve().parents[2]


@dataclass
class Config:
    donnees: Path = field(default_factory=lambda: Path(os.environ.get("CONSOLIDATION_DATA", RACINE / "data")))
    odoo_url: str = field(default_factory=lambda: os.environ.get("ODOO_URL", "http://localhost:8170"))
    odoo_base: str = field(default_factory=lambda: os.environ.get("ODOO_DB", "demo_a"))
    odoo_login: str = field(default_factory=lambda: os.environ.get("ODOO_LOGIN", "admin"))
    odoo_mot_de_passe: str = field(default_factory=lambda: os.environ.get("ODOO_PASSWORD", "admin"))
    odoo_mode: str = field(default_factory=lambda: os.environ.get("ODOO_MODE", "auto"))  # auto, live, instantane
    erp_c_url: str | None = field(default_factory=lambda: os.environ.get("ERP_C_URL") or None)
    as_of: dt.date = field(default_factory=lambda: _as_of(os.environ.get("CONSOLIDATION_AS_OF")))

    @property
    def entrepot(self) -> Path:
        return self.donnees / "entrepot.duckdb"

    @property
    def fec(self) -> Path:
        return self.donnees / "compta" / fec.NOM_FICHIER

    @property
    def dataset_c(self) -> Path:
        return self.donnees / "erp_c" / "dataset.json"

    @property
    def instantane(self) -> Path:
        return self.donnees / "odoo_instantane.json"

    @property
    def verite(self) -> Path:
        return self.donnees / "verite_terrain.json"


def _as_of(brut: str | None) -> dt.date:
    """Date d'arrêté des contrôles. Figée par défaut : les seuils à 30 jours donnent alors les mêmes
    résultats un jour ou l'autre, ce qui rend la démonstration reproductible."""
    if not brut:
        return AS_OF
    return dt.date.today() if brut == "today" else dt.date.fromisoformat(brut)


def _lire_odoo(cfg: Config) -> tuple[dict[str, Any], str]:
    if cfg.odoo_mode in ("auto", "live"):
        try:
            client = odoo.ClientOdoo(cfg.odoo_url, cfg.odoo_base, cfg.odoo_login, cfg.odoo_mot_de_passe)
            return odoo.lire(client), f"en direct, Odoo {client.version}"
        except odoo.ErreurOdoo:
            if cfg.odoo_mode == "live":
                raise
    if not cfg.instantane.exists():
        raise odoo.ErreurOdoo(
            "Odoo est injoignable et il n'y a pas d'instantané : ./scripts/odoo_up.sh puis consolidation generer"
        )
    date = dt.datetime.fromtimestamp(cfg.instantane.stat().st_mtime).strftime("%Y-%m-%d")
    return odoo.lire_instantane(cfg.instantane), f"instantané du {date} (Odoo injoignable)"


def _lire_entrepot(cfg: Config) -> tuple[dict[str, list[dict]], str]:
    if cfg.erp_c_url:
        with httpx.Client(base_url=cfg.erp_c_url, timeout=15) as http:
            return ClientErpC(http).charger(), f"HTTP, {cfg.erp_c_url}"
    donnees = json.loads(cfg.dataset_c.read_text(encoding="utf-8"))
    return ClientErpC(TestClient(creer_app(donnees))).charger(), "API REST simulée, dans le même processus"


def lire_sources(cfg: Config) -> entrepot.Sources:
    a, mode_a = _lire_odoo(cfg)
    b = fec.lire_fec(cfg.fec)
    c, mode_c = _lire_entrepot(cfg)
    return entrepot.Sources(a, b, c, {"A": mode_a, "B": f"fichier {cfg.fec.name}", "C": mode_c})


def actualiser(cfg: Config | None = None) -> dict[str, Any]:
    """Relit les trois systèmes et reconstruit l'entrepôt. Rend un compte rendu de ce qui a été lu et de
    ce qui a changé depuis la dernière actualisation."""
    cfg = cfg or Config()
    debut = time.monotonic()
    sources = lire_sources(cfg)
    bilan = entrepot.construire(cfg.entrepot, sources, cfg.as_of)
    return {
        "duree_s": round(time.monotonic() - debut, 2),
        "date_d_arret": cfg.as_of.isoformat(),
        "sources": entrepot.sources(cfg.entrepot),
        "ecarts": bilan["ecarts"],
        "nouveaux": bilan["nouveaux"],
        "resolus": bilan["resolus"],
        "premiere_construction": bilan["premiere_construction"],
    }
