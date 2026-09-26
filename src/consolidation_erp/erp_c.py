"""ERP C : l'entrepôt, une API REST.

Deux moitiés dans ce fichier. Le serveur simule l'API d'un logiciel d'entrepôt : clé d'API dans
l'en-tête, résultats paginés, ressources fournisseurs, articles et réceptions. Le client est ce que
la consolidation utilise : il suit la pagination jusqu'au bout, refuse une réponse incohérente et
réessaie quand le serveur répond 429 ou 5xx.

C'est une simulation, dite comme telle : il n'existe pas de vrai logiciel derrière ce serveur.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query

from .monde import Monde

CLE_API_DEMO = "demo-key"
PAGE_MAX = 50


def dataset(monde: Monde) -> dict[str, list[dict]]:
    """Ce que l'entrepôt sait : ses fournisseurs (sans doublons), ses articles, ses réceptions."""
    return {
        "suppliers": [
            {"code": p.code_c, "name": p.nom_c, "vat": p.vat, "country": p.pays, "updated_at": "2026-09-01T08:00:00"}
            for p in monde.partenaires
        ],
        "items": [{"sku": p.sku, "name": p.nom, "base_unit": "pce"} for p in monde.produits],
        "receipts": [
            {
                "receipt_ref": r.ref,
                "po_ref": r.po_ref,
                "supplier_code": r.code_c,
                "received_at": r.date.isoformat(),
                "lines": [{"sku": sku, "qty": qty, "unit": "pce"} for sku, qty in r.lignes],
            }
            for r in monde.receptions
        ],
    }


def ecrire_dataset(monde: Monde, chemin: Path) -> Path:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(dataset(monde), ensure_ascii=False, indent=1), encoding="utf-8")
    return chemin


def creer_app(donnees: dict[str, list[dict]], cle_api: str = CLE_API_DEMO) -> FastAPI:
    app = FastAPI(title="ERP C (entrepôt), simulé", docs_url=None, redoc_url=None)

    def verifier(x_api_key: str | None = Header(default=None)) -> None:
        if x_api_key != cle_api:
            raise HTTPException(status_code=401, detail="clé d'API absente ou invalide")

    def page(ressource: str, page: int, per_page: int) -> dict[str, Any]:
        lignes = donnees[ressource]
        debut = (page - 1) * per_page
        total = len(lignes)
        return {
            "data": lignes[debut : debut + per_page],
            "meta": {"page": page, "per_page": per_page, "total": total, "total_pages": max(1, -(-total // per_page))},
        }

    def route(ressource: str) -> None:
        @app.get(f"/api/v1/{ressource}", dependencies=[Depends(verifier)], name=ressource)
        def liste(page_: int = Query(1, alias="page", ge=1), per_page: int = Query(20, ge=1, le=PAGE_MAX)) -> dict[str, Any]:
            return page(ressource, page_, per_page)

    for ressource in ("suppliers", "items", "receipts"):
        route(ressource)
    return app


class ErreurErpC(RuntimeError):
    pass


class ClientErpC:
    """Lit une ressource de l'API page par page, jusqu'à ce que le total annoncé soit atteint."""

    def __init__(self, client: httpx.Client, cle_api: str = CLE_API_DEMO, essais: int = 3, attente: float = 0.2) -> None:
        self._client, self._cle, self._essais, self._attente = client, cle_api, essais, attente

    def _get(self, chemin: str, params: dict[str, Any]) -> dict[str, Any]:
        for essai in range(1, self._essais + 1):
            reponse = self._client.get(chemin, params=params, headers={"X-API-Key": self._cle})
            if reponse.status_code == 401:
                raise ErreurErpC("ERP C refuse la clé d'API")
            if reponse.status_code == 429 or reponse.status_code >= 500:
                if essai == self._essais:
                    raise ErreurErpC(f"ERP C indisponible ({reponse.status_code}) après {essai} essais")
                time.sleep(self._attente * essai)
                continue
            reponse.raise_for_status()
            return reponse.json()
        raise AssertionError("inatteignable")

    def tout(self, ressource: str) -> list[dict]:
        page, lignes = 1, []
        while True:
            corps = self._get(f"/api/v1/{ressource}", {"page": page, "per_page": PAGE_MAX})
            lignes.extend(corps["data"])
            meta = corps["meta"]
            if page >= meta["total_pages"]:
                break
            page += 1
        if len(lignes) != meta["total"]:
            raise ErreurErpC(f"{ressource} : {len(lignes)} lignes lues, {meta['total']} annoncées")
        return lignes

    def charger(self) -> dict[str, list[dict]]:
        return {r: self.tout(r) for r in ("suppliers", "items", "receipts")}
