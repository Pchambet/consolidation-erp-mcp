"""ERP A : un Odoo 17, lu par XML-RPC.

Trois pièges d'Odoo que ce code traite, appris sur une vraie instance :

* un mauvais mot de passe n'est pas une exception, `authenticate` renvoie `False` ;
* la quantité d'une ligne de commande est dans l'unité de la ligne, pas dans celle du produit :
  vingt douzaines lues comme vingt pièces faussent tout ce qui se calcule ensuite ;
* une erreur côté serveur revient avec toute la trace Python, dont seule la dernière ligne
  se montre à un humain.

Le lecteur rend une structure simple et stable (`lire`), que l'on peut aussi figer dans un fichier
(`exporter_instantane`) pour rejouer la consolidation sans Odoo.
"""

from __future__ import annotations

import datetime as dt
import json
import xmlrpc.client
from pathlib import Path
from typing import Any, Protocol

from .monde import Monde

ORIGINE_DEMO = "demo-consolidation"
TAILLE_PAGE = 200
DELAI_SECONDES = 30.0


class ErreurOdoo(RuntimeError):
    pass


class DejaSeme(ErreurOdoo):
    pass


class _TransportAvecDelai(xmlrpc.client.Transport):
    def make_connection(self, host: Any) -> Any:
        connexion = super().make_connection(host)
        connexion.timeout = DELAI_SECONDES
        return connexion


class Modeles(Protocol):
    """Ce dont le reste du code a besoin d'Odoo : un seul appel, `kw`."""

    def kw(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any: ...


class ClientOdoo:
    def __init__(self, url: str, base: str, identifiant: str, mot_de_passe: str) -> None:
        self.base, self.mot_de_passe = base, mot_de_passe
        commun: Any = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", allow_none=True, transport=_TransportAvecDelai())
        self._objets: Any = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", allow_none=True, transport=_TransportAvecDelai())
        try:
            uid = commun.authenticate(base, identifiant, mot_de_passe, {})
            self.version = commun.version()["server_version"]
        except (OSError, xmlrpc.client.ProtocolError) as exc:
            raise ErreurOdoo(f"Odoo injoignable sur {url} : {exc}") from exc
        if not uid:  # un mauvais mot de passe renvoie False, pas une exception
            raise ErreurOdoo(f"authentification refusée pour {identifiant!r} sur la base {base!r}")
        self.uid = int(uid)

    def kw(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return self._objets.execute_kw(self.base, self.uid, self.mot_de_passe, model, method, list(args), kwargs)
        except xmlrpc.client.Fault as exc:
            derniere_ligne = (exc.faultString or "").strip().splitlines()[-1]
            raise ErreurOdoo(f"{model}.{method} : {derniere_ligne}") from exc


def _pages(odoo: Modeles, model: str, domaine: list, champs: list[str], **kwargs: Any) -> list[dict]:
    lignes: list[dict] = []
    while True:
        lot = odoo.kw(model, "search_read", domaine, fields=champs, limit=TAILLE_PAGE, offset=len(lignes), order="id", **kwargs)
        lignes.extend(lot)
        if len(lot) < TAILLE_PAGE:
            return lignes


# ----------------------------------------------------------------------------------- lecture


def lire(odoo: Modeles) -> dict[str, Any]:
    """Fournisseurs, produits (avec leur unité d'achat) et commandes confirmées, avec leurs lignes."""
    partenaires = _pages(odoo, "res.partner", [["supplier_rank", ">", 0]], ["ref", "name", "vat", "write_date"])
    unites = {u["id"]: u for u in _pages(odoo, "uom.uom", [], ["name", "factor_inv"], context={"active_test": False})}
    produits = _pages(odoo, "product.product", [["default_code", "!=", False]], ["default_code", "name", "uom_po_id"])
    sku_de = {p["id"]: p["default_code"] for p in produits}
    commandes = _pages(
        odoo,
        "purchase.order",
        [["state", "in", ["purchase", "done"]]],
        ["name", "partner_id", "date_order", "currency_id", "amount_untaxed", "state", "write_date", "order_line"],
    )
    ids_lignes = [i for c in commandes for i in c["order_line"]]
    lignes_par_commande: dict[int, list[dict]] = {}
    for debut in range(0, len(ids_lignes), TAILLE_PAGE):
        for l in odoo.kw(
            "purchase.order.line",
            "read",
            ids_lignes[debut : debut + TAILLE_PAGE],
            fields=["order_id", "product_id", "product_qty", "product_uom", "price_unit"],
        ):
            unite = unites[l["product_uom"][0]]
            lignes_par_commande.setdefault(l["order_id"][0], []).append(
                {
                    "id": l["id"],
                    "sku": sku_de[l["product_id"][0]],
                    "qty": l["product_qty"],
                    "uom": unite["name"],
                    # nombre de pièces (unité de référence) dans une unité de cette ligne
                    "facteur_base": unite["factor_inv"],
                    "price_unit": l["price_unit"],
                }
            )
    return {
        "partners": [
            {"id": p["id"], "ref": p["ref"] or None, "name": p["name"], "vat": p["vat"] or None, "write_date": p["write_date"]}
            for p in partenaires
        ],
        "products": [
            {
                "id": p["id"],
                "sku": p["default_code"],
                "name": p["name"],
                "uom_achat": unites[p["uom_po_id"][0]]["name"] if p["uom_po_id"] else None,
            }
            for p in produits
        ],
        "orders": [
            {
                "id": c["id"],
                "name": c["name"],
                "partner_id": c["partner_id"][0],
                "date_order": c["date_order"],
                "currency": c["currency_id"][1],
                "amount_untaxed": c["amount_untaxed"],
                "state": c["state"],
                "write_date": c["write_date"],
                "lines": lignes_par_commande.get(c["id"], []),
            }
            for c in commandes
        ],
    }


def exporter_instantane(donnees: dict[str, Any], chemin: Path) -> Path:
    """La date d'export est écrite dans le fichier : sa date de modification change à chaque clone."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    contenu = {"exporte_le": dt.date.today().isoformat(), **donnees}
    chemin.write_text(json.dumps(contenu, ensure_ascii=False, indent=1), encoding="utf-8")
    return chemin


def lire_instantane(chemin: Path) -> tuple[dict[str, Any], str | None]:
    """Rend les données et leur date d'export (None pour un instantané qui ne la porte pas)."""
    donnees = json.loads(chemin.read_text(encoding="utf-8"))
    return donnees, donnees.pop("exporte_le", None)


# ----------------------------------------------------------------------------------- écriture


def _societe_en_euros(odoo: Modeles) -> None:
    """Une base neuve est en USD. Odoo interdit de changer la devise de la société dès la première écriture
    comptable : on la passe en EUR tant que la base est vide, et on active le CHF."""
    moi = odoo.kw("res.users", "search_read", [["login", "=", "admin"]], fields=["company_id"], limit=1)[0]["company_id"][0]
    devise = odoo.kw("res.company", "read", [moi], fields=["currency_id"])[0]["currency_id"][1]
    inactif = {"active_test": False}
    if devise != "EUR":
        euro = odoo.kw("res.currency", "search", [["name", "=", "EUR"]], context=inactif)
        odoo.kw("res.currency", "write", euro, {"active": True})
        odoo.kw("res.company", "write", [moi], {"currency_id": euro[0]})
    franc = odoo.kw("res.currency", "search", [["name", "=", "CHF"]], context=inactif)
    odoo.kw("res.currency", "write", franc, {"active": True})


def semer(odoo: Modeles, monde: Monde) -> dict[str, str]:
    """Crée fournisseurs, produits et commandes confirmées. Renvoie la correspondance entre les numéros
    de commande prévus par le monde et ceux qu'Odoo a attribués."""
    if odoo.kw("purchase.order", "search_count", [["origin", "=", ORIGINE_DEMO]]):
        raise DejaSeme("la base contient déjà les commandes de la maquette : ./scripts/odoo_up.sh --reset pour repartir de zéro")
    manquants = odoo.kw(
        "ir.module.module", "search_read", [["name", "=", "purchase"], ["state", "!=", "installed"]], fields=["name"]
    )
    if manquants:
        raise ErreurOdoo("le module « purchase » n'est pas installé")
    _societe_en_euros(odoo)

    unite = {n: odoo.kw("uom.uom", "search", [["name", "=", n]], limit=1)[0] for n in ("Units", "Dozens")}
    devises = {
        n: odoo.kw("res.currency", "search", [["name", "=", n]], limit=1, context={"active_test": False})[0]
        for n in ("EUR", "CHF")
    }

    partenaire_id: dict[str, int] = {}
    for p in monde.partenaires + monde.doublons_odoo:
        partenaire_id[p.ref] = odoo.kw(
            "res.partner",
            "create",
            {"name": p.nom, "ref": p.ref, "vat": p.vat, "is_company": True, "supplier_rank": 1, "comment": ORIGINE_DEMO},
        )
    produit_id: dict[str, int] = {}
    for prod in monde.produits:
        produit_id[prod.sku] = odoo.kw(
            "product.product",
            "create",
            {
                "name": prod.nom,
                "default_code": prod.sku,
                "type": "consu",
                "purchase_ok": True,
                "uom_id": unite["Units"],
                "uom_po_id": unite[prod.unite_achat],
                "standard_price": prod.prix_base,
            },
        )

    correspondance: dict[str, str] = {}
    for c in monde.commandes:
        instant = dt.datetime.combine(c.date, dt.time(9, 0)).strftime("%Y-%m-%d %H:%M:%S")
        id_commande = odoo.kw(
            "purchase.order",
            "create",
            {
                "partner_id": partenaire_id[c.partenaire_ref],
                "currency_id": devises[c.devise],
                "date_order": instant,
                "origin": ORIGINE_DEMO,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": produit_id[l.sku],
                            "name": l.sku,
                            "product_qty": l.qty,
                            "product_uom": unite[l.unite],
                            "price_unit": l.prix_unitaire,
                            "date_planned": instant,
                        },
                    )
                    for l in c.lignes
                ],
            },
        )
        odoo.kw("purchase.order", "button_confirm", [id_commande])
        correspondance[c.ref] = odoo.kw("purchase.order", "read", [id_commande], fields=["name"])[0]["name"]
    return correspondance
