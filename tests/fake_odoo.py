"""Un Odoo minimal en mémoire : juste assez de `search_read` et de `read` pour tester le lecteur sans Docker."""

from __future__ import annotations

from typing import Any

from consolidation_erp.demo import odoo_depuis_monde
from consolidation_erp.monde import Monde


class FauxOdoo:
    def __init__(self, monde: Monde) -> None:
        d = odoo_depuis_monde(monde)
        self.tables: dict[str, list[dict[str, Any]]] = {
            "uom.uom": [
                {"id": 1, "name": "Units", "factor_inv": 1.0},
                {"id": 2, "name": "Dozens", "factor_inv": 12.0},
            ],
            "res.partner": [{**p, "supplier_rank": 1} for p in d["partners"]],
            "product.product": [
                {"id": p["id"], "default_code": p["sku"], "name": p["name"],
                 "uom_po_id": (2 if p["uom_achat"] == "Dozens" else 1, p["uom_achat"])}
                for p in d["products"]
            ],
            "purchase.order": [],
            "purchase.order.line": [],
        }
        sku_id = {p["sku"]: p["id"] for p in d["products"]}
        for o in d["orders"]:
            self.tables["purchase.order"].append(
                {"id": o["id"], "name": o["name"], "partner_id": (o["partner_id"], "x"), "date_order": o["date_order"],
                 "currency_id": (1, o["currency"]), "amount_untaxed": o["amount_untaxed"], "state": o["state"],
                 "write_date": o["write_date"], "order_line": [l["id"] for l in o["lines"]]}
            )
            for l in o["lines"]:
                self.tables["purchase.order.line"].append(
                    {"id": l["id"], "order_id": (o["id"], o["name"]), "product_id": (sku_id[l["sku"]], l["sku"]),
                     "product_qty": l["qty"], "product_uom": (2 if l["uom"] == "Dozens" else 1, l["uom"]),
                     "price_unit": l["price_unit"]}
                )
        self.appels = 0

    @staticmethod
    def _garde(ligne: dict[str, Any], domaine: list) -> bool:
        for champ, op, valeur in domaine:
            v = ligne.get(champ)
            if op == "=" and v != valeur:
                return False
            if op == "!=" and v == valeur:
                return False
            if op == ">" and not (v is not None and v > valeur):
                return False
            if op == "in" and v not in valeur:
                return False
        return True

    def kw(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        self.appels += 1
        if method == "search_read":
            lignes = [l for l in self.tables[model] if self._garde(l, args[0])]
            lignes.sort(key=lambda l: l["id"])
            debut = kwargs.get("offset", 0)
            lot = lignes[debut : debut + kwargs.get("limit", len(lignes))]
            champs = kwargs.get("fields")
            return [{k: v for k, v in l.items() if not champs or k in champs or k == "id"} for l in lot]
        if method == "read":
            indexe = {l["id"]: l for l in self.tables[model]}
            return [indexe[i] for i in args[0]]
        raise NotImplementedError(f"{model}.{method}")
