"""Draw the README hero figure from the warehouse built by `consolidation actualiser`.

Every number on the figure is read from the warehouse or from the committed ground truth
(`data/verite_terrain.json`), never typed in, so the figure cannot drift from the code.

    uv run consolidation actualiser
    uv run --group figures python scripts/hero_figure.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from consolidation_erp import entrepot
from consolidation_erp.pipeline import Config

SORTIE = Path(__file__).resolve().parents[1] / "docs" / "figures" / "hero.png"

INK, TEAL, AMBER, SLATE, GRID = "#0f172a", "#0d9488", "#d97706", "#64748b", "#e2e8f0"

# English labels for the README; the code base keeps the French rule identifiers.
CONTROLES = {
    "facture_en_double": "Invoice entered twice",
    "facture_mauvais_fournisseur": "Invoice on another supplier's account",
    "devise_incoherente": "Invoice currency differs from order",
    "unite_incoherente": "Received in another unit (dozens vs pieces)",
    "commande_sans_reception": "Old order with no receipt",
    "commande_sans_facture": "Received > 30 days ago, never invoiced",
    "ecart_montant": "Invoiced amount differs from order",
    "ecart_quantite": "Received quantity differs from order",
    "reception_sans_commande": "Receipt for an unknown order",
    "facture_avant_commande": "Invoice dated before its order",
    "partenaire_double": "Supplier twice in Odoo (same VAT)",
    "partenaire_ambigu": "Ledger account matching two suppliers",
}

TRI_VOIES = """
WITH anciennes AS (
    SELECT po_ref FROM fact_commande
    WHERE date_commande < (SELECT as_of FROM parametres) - INTERVAL 30 DAY)
SELECT
  (SELECT count(*) FROM anciennes) AS commandes,
  (SELECT count(*) FROM anciennes a WHERE EXISTS (SELECT 1 FROM fact_reception r WHERE r.po_ref = a.po_ref)) AS recues,
  (SELECT count(*) FROM anciennes a WHERE EXISTS (SELECT 1 FROM fact_facture f WHERE f.po_ref = a.po_ref)) AS facturees,
  (SELECT count(*) FROM anciennes a WHERE NOT EXISTS (SELECT 1 FROM ecarts e WHERE e.po_ref = a.po_ref)) AS conformes
"""


def main() -> None:
    cfg = Config()
    voies = entrepot._lignes(cfg.entrepot, TRI_VOIES)[0]
    detectes = {(l["regle"], l["cle"]) for l in entrepot._lignes(cfg.entrepot, "SELECT regle, cle FROM ecarts")}
    plantes = {(e["regle"], e["cle"]) for e in json.loads(cfg.verite.read_text(encoding="utf-8"))}
    retrouves, faux_positifs = len(plantes & detectes), len(detectes - plantes)
    inconnues = {r for r, _ in plantes | detectes} - CONTROLES.keys()
    if inconnues:
        raise SystemExit(f"no English label for {sorted(inconnues)}")

    plt.rcParams.update(
        {"font.family": "DejaVu Sans", "text.color": INK, "axes.labelcolor": INK, "xtick.color": SLATE, "ytick.color": INK}
    )
    fig, (gauche, droite) = plt.subplots(1, 2, figsize=(12, 5.2), gridspec_kw={"width_ratios": [1, 1.25], "wspace": 0.95})

    # Left: the three-way match on purchase orders older than 30 days. Each bar counts from the
    # first one (an order can be invoiced without a receipt), so they are not nested subsets.
    etapes = [
        ("Orders older\nthan 30 days", voies["commandes"]),
        ("with a receipt\n(warehouse, C)", voies["recues"]),
        ("with an invoice\n(ledger, B)", voies["facturees"]),
        ("pass every\ncontrol", voies["conformes"]),
    ]
    y = range(len(etapes))[::-1]
    couleurs = [SLATE, SLATE, SLATE, TEAL]
    gauche.barh(list(y), [n for _, n in etapes], color=couleurs, height=0.62)
    gauche.set_yticks(list(y), [e for e, _ in etapes], fontsize=10)
    for yi, (_, n) in zip(y, etapes, strict=True):
        gauche.text(n + 1.5, yi, str(n), va="center", fontsize=11, color=INK, fontweight="bold")
    part = voies["conformes"] / voies["commandes"]
    gauche.text(
        voies["conformes"] / 2,
        0,
        f"{part:.1%}".replace(".0%", "%"),
        va="center",
        ha="center",
        color="white",
        fontsize=11,
        fontweight="bold",
    )
    gauche.set_xlim(0, voies["commandes"] * 1.15)
    gauche.set_xlabel("purchase orders (Odoo, A)")
    gauche.set_title("Three-way match: order → receipt → invoice", loc="left", fontsize=12, color=INK)

    # Right: planted vs detected, per control.
    n_plantes, n_detectes = Counter(r for r, _ in plantes), Counter(r for r, _ in detectes)
    ordre = sorted(CONTROLES, key=lambda r: (n_plantes[r], CONTROLES[r]))
    yy = range(len(ordre))
    droite.barh(list(yy), [n_plantes[r] for r in ordre], color=GRID, height=0.72, label="planted")
    droite.scatter([n_detectes[r] for r in ordre], list(yy), color=TEAL, s=46, zorder=3, label="detected by the SQL controls")
    droite.set_yticks(list(yy), [CONTROLES[r] for r in ordre], fontsize=9)
    droite.set_xticks(range(0, max(n_plantes.values()) + 1))
    droite.set_xlim(0, max(n_plantes.values()) + 0.6)
    droite.set_xlabel("discrepancies")
    droite.legend(loc="lower right", frameon=False, fontsize=9)
    droite.set_title(
        f"{retrouves} of {len(plantes)} planted discrepancies found, {faux_positifs} invented", loc="left", fontsize=12, color=INK
    )

    for ax in (gauche, droite):
        ax.spines[["top", "right"]].set_visible(False)
        ax.spines[["left", "bottom"]].set_color(GRID)
        ax.tick_params(length=0)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)

    fig.suptitle(
        "Three systems, one warehouse: deterministic SQL controls find every planted discrepancy and invent none",
        x=0.01,
        ha="left",
        fontsize=13.5,
        fontweight="bold",
        color=INK,
    )
    as_of = entrepot._lignes(cfg.entrepot, "SELECT as_of FROM parametres")[0]["as_of"]
    fig.text(
        0.01,
        0.005,
        f"Synthetic world, fixed seed, cut-off date {as_of}. The 13th control (unbalanced journal entry) has no planted case; it is tested on its own.",
        fontsize=8.5,
        color=SLATE,
    )
    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(SORTIE, dpi=200, bbox_inches="tight", facecolor="white")
    print(SORTIE)


if __name__ == "__main__":
    main()
