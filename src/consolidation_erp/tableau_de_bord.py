"""Le tableau de bord : une page HTML autonome, générée depuis l'entrepôt.

Une seule teinte pour les barres (elles mesurent une quantité), des puces de gravité qui portent une
icône et un libellé en plus de la couleur, une infobulle au survol et au clavier, une vue tableau sous
chaque graphique, un rendu clair et sombre. Aucune dépendance : le fichier s'ouvre hors ligne.

Ce n'est pas un rapport Power BI. C'est la même information, servie sans licence, pour que la
consolidation soit lisible dès le premier lancement. La version Power BI se construit sur les CSV
du dossier `powerbi/` (voir son README).
"""

from __future__ import annotations

import datetime as dt
import html
from pathlib import Path
from typing import Any

from . import entrepot
from .pipeline import Config, RACINE

GRAVITES = {
    "haute": ("▲", "Haute", "var(--critique)"),
    "moyenne": ("◆", "Moyenne", "var(--serieux)"),
    "basse": ("●", "Basse", "var(--discret)"),
}


def _n(x: float | int | None, decimales: int = 0) -> str:
    if x is None:
        return "n/a"
    texte = f"{x:,.{decimales}f}".replace(",", " ").replace(".", ",")
    return texte


def _valeur(v: Any, unite: str) -> str:
    if unite == "EUR":
        return f"{_n(v)} €"
    if unite == "%":
        return f"{_n(v, 1)} %"
    if unite == "jours":
        return f"{_n(v, 1)} j"
    return _n(v)


def _e(x: Any) -> str:
    return html.escape(str(x), quote=True)


def _barres(lignes: list[tuple[str, float, str]], unite: str = "", maximum: float | None = None) -> str:
    """Barres horizontales. Chaque ligne : (libellé, valeur, infobulle). Une seule teinte, valeur au bout."""
    maximum = maximum or max((v for _, v, _ in lignes), default=1) or 1
    rangs = []
    for libelle, valeur, info in lignes:
        pct = 100 * valeur / maximum
        rangs.append(
            f'<div class="rang" tabindex="0" data-tip="{_e(info)}">'
            f'<span class="etiquette">{_e(libelle)}</span>'
            f'<span class="piste"><span class="barre" style="width:{pct:.1f}%"></span>'
            f'<span class="valeur">{_e(_n(valeur))}{_e(unite)}</span></span></div>'
        )
    return '<div class="barres" role="list">' + "".join(rangs) + "</div>"


def _donnees(chemin: Path) -> dict[str, Any]:
    q = lambda sql, p=None: entrepot._lignes(chemin, sql, p)  # noqa: E731
    return {
        "indicateurs": {i["cle"]: i for i in entrepot.indicateurs(chemin)},
        "sources": entrepot.sources(chemin),
        "as_of": q("SELECT as_of FROM parametres")[0]["as_of"],
        "par_regle": q(
            """SELECT regle, any_value(libelle_regle) AS libelle, count(*) AS n, round(sum(coalesce(montant_eur, 0))) AS eur,
                      any_value(responsable) AS responsable, any_value(action) AS action
               FROM ecarts GROUP BY regle ORDER BY n DESC, eur DESC"""
        ),
        "par_systeme": q(
            """SELECT s.systeme, count(*) AS n FROM ecarts e,
               (VALUES ('A'), ('B'), ('C')) s(systeme)
               WHERE json_exists(e.detail, '$.' || s.systeme) GROUP BY s.systeme ORDER BY s.systeme"""
        ),
        "par_responsable": q(
            """SELECT responsable, count(*) AS n, round(sum(coalesce(montant_eur, 0))) AS eur
               FROM ecarts GROUP BY responsable ORDER BY n DESC"""
        ),
        "tri_voies": q(
            """WITH anciennes AS (
                   SELECT c.po_ref, c.partner_key FROM fact_commande c
                   WHERE c.date_commande < (SELECT as_of FROM parametres) - INTERVAL 30 DAY)
               SELECT
                 (SELECT count(*) FROM anciennes) AS commandes,
                 (SELECT count(*) FROM anciennes a WHERE EXISTS (SELECT 1 FROM fact_reception r WHERE r.po_ref = a.po_ref)) AS recues,
                 (SELECT count(*) FROM anciennes a WHERE EXISTS (SELECT 1 FROM fact_facture f WHERE f.po_ref = a.po_ref)) AS facturees,
                 (SELECT count(*) FROM anciennes a WHERE NOT EXISTS (SELECT 1 FROM ecarts e WHERE e.po_ref = a.po_ref)) AS conformes"""
        )[0],
        "ecarts": q(
            """SELECT ecart_id, gravite, regle, responsable, coalesce(po_ref, cle) AS objet, montant_eur, resume
               FROM ecarts
               ORDER BY CASE gravite WHEN 'haute' THEN 0 WHEN 'moyenne' THEN 1 ELSE 2 END, montant_eur DESC NULLS LAST, ecart_id"""
        ),
    }


def _tuile(i: dict[str, Any], hero: bool = False) -> str:
    return (
        f'<div class="tuile{" hero" if hero else ""}" tabindex="0" data-tip="{_e(i["definition"])}">'
        f'<div class="tuile-etiquette">{_e(i["libelle"])}</div>'
        f'<div class="tuile-valeur">{_e(_valeur(i["valeur"], i["unite"]))}</div></div>'
    )


def _table(entetes: list[str], lignes: list[list[Any]]) -> str:
    tete = "".join(f"<th>{_e(h)}</th>" for h in entetes)
    corps = "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in l) + "</tr>" for l in lignes)
    return f'<div class="defilement"><table><thead><tr>{tete}</tr></thead><tbody>{corps}</tbody></table></div>'


def page(d: dict[str, Any]) -> str:
    ind = d["indicateurs"]
    total = ind["ecarts_ouverts"]["valeur"]
    haute = ind["ecarts_gravite_haute"]["valeur"]

    graphique_regles = _barres([
        (r["libelle"], r["n"], f'{r["libelle"]} : {r["n"]} écart(s), {_n(r["eur"])} € en jeu. Responsable : {r["responsable"]}. {r["action"]}')
        for r in d["par_regle"]
    ])
    table_regles = _table(
        ["Contrôle", "Écarts", "Montant en jeu (€)", "Responsable"],
        [[r["libelle"], r["n"], _n(r["eur"]), r["responsable"]] for r in d["par_regle"]],
    )

    noms = {"A": "A · Odoo (achats)", "B": "B · Comptabilité (FEC)", "C": "C · Entrepôt (API)"}
    graphique_systemes = _barres(
        [(noms[s["systeme"]], s["n"], f'{s["n"]} écart(s) citent des enregistrements du système {noms[s["systeme"]]}') for s in d["par_systeme"]],
        maximum=total,
    )
    table_systemes = _table(["Système", "Écarts qui le citent"], [[noms[s["systeme"]], s["n"]] for s in d["par_systeme"]])

    t = d["tri_voies"]
    etapes = [
        ("Commandes de plus de 30 jours", t["commandes"], "Commandes passées dans Odoo il y a plus de 30 jours"),
        ("… avec une réception", t["recues"], "Commandes dont l'entrepôt a enregistré une réception"),
        ("… avec une facture", t["facturees"], "Commandes dont la comptabilité a enregistré une facture"),
        ("… sans aucun écart", t["conformes"], "Commandes qui passent tous les contrôles, de la commande à la facture"),
    ]
    graphique_voies = _barres([(l, v, i) for l, v, i in etapes], maximum=t["commandes"])
    table_voies = _table(["Étape", "Commandes"], [[l, v] for l, v, _ in etapes])

    graphique_resp = _barres(
        [(r["responsable"], r["n"], f'{r["responsable"]} : {r["n"]} écart(s), {_n(r["eur"])} € en jeu') for r in d["par_responsable"]]
    )

    lignes_ecarts = ""
    for e in d["ecarts"]:
        icone, libelle, couleur = GRAVITES[e["gravite"]]
        montant = f'{_n(e["montant_eur"], 2)} €' if e["montant_eur"] is not None else "n/a"
        lignes_ecarts += (
            f'<tr><td><span class="puce"><span class="puce-icone" style="color:{couleur}" aria-hidden="true">{icone}</span>{libelle}</span></td>'
            f'<td>{_e(e["objet"])}</td><td class="texte">{_e(e["resume"])}</td><td class="nombre">{montant}</td>'
            f'<td>{_e(e["responsable"])}</td><td><code>{_e(e["ecart_id"])}</code></td></tr>'
        )

    sources = "".join(
        f'<li><strong>{_e(s["systeme"])}</strong> {_e(s["mode"])} <span class="discret">({_e(s["detail"])})</span></li>'
        for s in d["sources"]
    )
    genere = dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    as_of = dt.date.fromisoformat(d["as_of"]).strftime("%d/%m/%Y")

    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Consolidation de trois ERP</title>
<style>
:root {{
  color-scheme: light;
  --surface: #fcfcfb; --surface-2: #f4f3f0; --texte: #0b0b0b; --texte-2: #52514e; --texte-3: #75746f;
  --filet: #e4e3df; --serie: #2a78d6; --serie-fond: #dbe8f8;
  --critique: #d03b3b; --serieux: #c4581f; --discret: #75746f;
}}
@media (prefers-color-scheme: dark) {{
  :root:where(:not([data-theme="light"])) {{
    color-scheme: dark;
    --surface: #1a1a19; --surface-2: #232321; --texte: #ffffff; --texte-2: #c3c2b7; --texte-3: #9a9990;
    --filet: #383835; --serie: #3987e5; --serie-fond: #22364f;
    --critique: #e66767; --serieux: #ec835a; --discret: #9a9990;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --surface: #1a1a19; --surface-2: #232321; --texte: #ffffff; --texte-2: #c3c2b7; --texte-3: #9a9990;
  --filet: #383835; --serie: #3987e5; --serie-fond: #22364f;
  --critique: #e66767; --serieux: #ec835a; --discret: #9a9990;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--surface); color: var(--texte); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 1120px; margin: 0 auto; padding: 32px 16px 64px; }}
h1 {{ font-size: 22px; margin: 0 0 4px; }}
h2 {{ font-size: 16px; margin: 0 0 4px; }}
.sous-titre, .discret {{ color: var(--texte-2); }}
.sous-titre {{ margin: 0 0 24px; }}
.discret {{ font-size: 13px; }}
.tuiles {{ display: grid; gap: 12px; grid-template-columns: repeat(4, 1fr); margin-bottom: 24px; }}
.tuile {{ background: var(--surface-2); border-radius: 8px; padding: 14px 16px; }}
.tuile-etiquette {{ color: var(--texte-2); font-size: 13px; }}
.tuile-valeur {{ font-size: 26px; font-weight: 600; line-height: 1.2; margin-top: 4px; }}
.tuile.hero {{ grid-column: span 2; }}
.tuile.hero .tuile-valeur {{ font-size: 52px; }}
.grille {{ display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(440px, 1fr)); margin-bottom: 16px; }}
.carte {{ border: 1px solid var(--filet); border-radius: 10px; padding: 16px 18px; }}
.carte p {{ margin: 0 0 12px; }}
.barres {{ display: grid; gap: 8px; }}
.rang {{ display: grid; grid-template-columns: minmax(130px, 42%) 1fr; align-items: center; gap: 10px; border-radius: 4px; }}
.rang:hover, .rang:focus-visible {{ background: var(--surface-2); outline: none; }}
.etiquette {{ font-size: 13px; color: var(--texte-2); }}
.piste {{ position: relative; display: flex; align-items: center; height: 24px;
  background: linear-gradient(to right, var(--filet) 1px, transparent 1px) 0 0 / 25% 100% repeat-x; }}
.barre {{ height: 16px; background: var(--serie); border-radius: 0 4px 4px 0; min-width: 2px; }}
.valeur {{ margin-left: 8px; font-size: 13px; font-variant-numeric: tabular-nums; }}
details {{ margin-top: 12px; }}
summary {{ cursor: pointer; color: var(--texte-2); font-size: 13px; }}
.defilement {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--filet); vertical-align: top; }}
th {{ color: var(--texte-2); font-weight: 600; white-space: nowrap; }}
td.nombre {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
td.texte {{ min-width: 280px; }}
.puce {{ display: inline-flex; gap: 6px; align-items: center; white-space: nowrap; }}
.puce-icone {{ font-size: 12px; }}
code {{ font-size: 12px; color: var(--texte-2); }}
ul.sources {{ margin: 8px 0 0; padding-left: 18px; }}
#infobulle {{ position: fixed; z-index: 10; max-width: 320px; padding: 8px 10px; border-radius: 6px; font-size: 12.5px;
  background: var(--texte); color: var(--surface); pointer-events: none; opacity: 0; transition: opacity .08s; }}
@media (max-width: 900px) {{ .tuiles {{ grid-template-columns: repeat(2, 1fr); }} }}
@media (max-width: 600px) {{ .grille {{ grid-template-columns: 1fr; }} .tuiles {{ grid-template-columns: 1fr; }} .tuile.hero {{ grid-column: span 1; }} .rang {{ grid-template-columns: 1fr; gap: 2px; }} }}
</style>
</head>
<body>
<main>
<h1>Consolidation de trois ERP : contrôle des écarts</h1>
<p class="sous-titre">Achats (Odoo), comptabilité (FEC) et entrepôt (API REST), rapprochés en trois voies. Date d'arrêté des contrôles : {as_of}. Données synthétiques.</p>

<section class="tuiles" aria-label="Indicateurs">
{_tuile(ind["ecarts_ouverts"], hero=True)}
{_tuile(ind["ecarts_gravite_haute"])}
{_tuile(ind["taux_conformite_3_voies"])}
{_tuile(ind["recu_non_facture_eur"])}
{_tuile(ind["delai_commande_facture_jours"])}
{_tuile(ind["fournisseurs_fiches_multiples"])}
{_tuile(ind["rattachement_compta_pct"])}
</section>

<section class="grille">
  <div class="carte">
    <h2>Écarts par contrôle</h2>
    <p class="discret">Nombre d'écarts détectés, du plus fréquent au moins fréquent.</p>
    {graphique_regles}
    <details><summary>Voir les données</summary>{table_regles}</details>
  </div>
  <div class="carte">
    <h2>Rapprochement en trois voies</h2>
    <p class="discret">Commandes de plus de 30 jours : combien vont jusqu'à la facture sans le moindre écart.</p>
    {graphique_voies}
    <details><summary>Voir les données</summary>{table_voies}</details>
  </div>
  <div class="carte">
    <h2>Où corriger</h2>
    <p class="discret">Écarts qui citent chaque système (un écart peut en citer deux ou trois).</p>
    {graphique_systemes}
    <details><summary>Voir les données</summary>{table_systemes}</details>
  </div>
  <div class="carte">
    <h2>Qui doit agir</h2>
    <p class="discret">Écarts à traiter par équipe.</p>
    {graphique_resp}
  </div>
</section>

<section class="carte">
  <h2>Les {total} écarts, du plus grave au moins grave ({haute} de gravité haute)</h2>
  <div class="defilement"><table>
    <thead><tr><th>Gravité</th><th>Objet</th><th>Constat</th><th>Montant en jeu</th><th>Responsable</th><th>Identifiant</th></tr></thead>
    <tbody>{lignes_ecarts}</tbody>
  </table></div>
</section>

<section class="carte" style="margin-top:16px">
  <h2>Sources lues</h2>
  <ul class="sources">{sources}</ul>
  <p class="discret" style="margin-top:8px">Page générée le {genere}. Chaque écart se retrouve par son identifiant dans le serveur MCP (outil expliquer_ecart) ou en ligne de commande (consolidation expliquer).</p>
</section>
</main>
<div id="infobulle" role="tooltip"></div>
<script>
const bulle = document.getElementById('infobulle');
function montrer(cible, x, y) {{
  bulle.textContent = cible.dataset.tip;
  const largeur = bulle.offsetWidth, hauteur = bulle.offsetHeight;
  bulle.style.left = Math.max(8, Math.min(x + 14, window.innerWidth - largeur - 8)) + 'px';
  bulle.style.top = Math.max(8, Math.min(y + 14, window.innerHeight - hauteur - 8)) + 'px';
  bulle.style.opacity = 1;
}}
document.querySelectorAll('[data-tip]').forEach(el => {{
  el.addEventListener('mousemove', e => montrer(el, e.clientX, e.clientY));
  el.addEventListener('mouseleave', () => bulle.style.opacity = 0);
  el.addEventListener('focus', () => {{ const r = el.getBoundingClientRect(); montrer(el, r.left + 40, r.bottom - 10); }});
  el.addEventListener('blur', () => bulle.style.opacity = 0);
}});
</script>
</body>
</html>
"""


def ecrire(cfg: Config, sortie: str | Path) -> Path:
    chemin = Path(sortie)
    if not chemin.is_absolute():
        chemin = RACINE / chemin
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(page(_donnees(cfg.entrepot)), encoding="utf-8")
    return chemin
