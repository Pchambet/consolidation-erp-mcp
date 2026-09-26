"""La ligne de commande : générer les sources, actualiser, lister et expliquer les écarts, corriger."""

from __future__ import annotations

import argparse
import json
import sys

from . import demo, entrepot
from .odoo import ErreurOdoo
from .pipeline import Config, actualiser


def _table(colonnes: list[str], lignes: list[list]) -> str:
    largeurs = [max(len(str(c)), *(len(str(l[i])) for l in lignes)) if lignes else len(str(c)) for i, c in enumerate(colonnes)]
    def fmt(l: list) -> str:
        return "  ".join(str(v).ljust(w) for v, w in zip(l, largeurs)).rstrip()
    return "\n".join([fmt(colonnes), fmt(["-" * w for w in largeurs]), *(fmt(l) for l in lignes)])


def _generer(cfg: Config, args: argparse.Namespace) -> int:
    print(json.dumps(demo.generer(cfg, avec_odoo=not args.sans_odoo), ensure_ascii=False, indent=1))
    return 0


def _actualiser(cfg: Config, args: argparse.Namespace) -> int:
    r = actualiser(cfg)
    print(f"Actualisé en {r['duree_s']} s, date d'arrêté {r['date_d_arret']}")
    for s in r["sources"]:
        print(f"  {s['systeme']}  {s['mode']} : {s['detail']}")
    print(f"{r['ecarts']} écarts" + ("" if r["premiere_construction"] else f" ({len(r['nouveaux'])} nouveaux, {len(r['resolus'])} résolus)"))
    return 0


def _ecarts(cfg: Config, args: argparse.Namespace) -> int:
    r = entrepot.ecarts_ouverts(cfg.entrepot, args.gravite, args.regle, args.limite)
    print(f"{r['total']} écarts" + (f", {r['affiches']} affichés" if r["affiches"] < r["total"] else ""))
    print(_table(
        ["id", "gravité", "règle", "commande", "EUR", "constat"],
        [[e["ecart_id"], e["gravite"], e["regle"], e["po_ref"] or "", e["montant_eur"] if e["montant_eur"] is not None else "", e["resume"]] for e in r["ecarts"]],
    ))
    return 0


def _expliquer(cfg: Config, args: argparse.Namespace) -> int:
    print(json.dumps(entrepot.expliquer_ecart(cfg.entrepot, args.ecart_id), ensure_ascii=False, indent=1))
    return 0


def _corriger(cfg: Config, args: argparse.Namespace) -> int:
    print(demo.corriger(cfg, args.ecart_id))
    print("Relancer `consolidation actualiser` : l'écart doit avoir disparu.")
    return 0


def _requete(cfg: Config, args: argparse.Namespace) -> int:
    r = entrepot.requete_lecture_seule(cfg.entrepot, args.sql, args.limite)
    print(_table(r["colonnes"], r["lignes"]))
    if r["note"]:
        print(r["note"])
    return 0


def _indicateurs(cfg: Config, args: argparse.Namespace) -> int:
    print(_table(["indicateur", "valeur", "unité"], [[i["libelle"], i["valeur"], i["unite"]] for i in entrepot.indicateurs(cfg.entrepot)]))
    return 0


def _erp_c(cfg: Config, args: argparse.Namespace) -> int:
    import uvicorn
    from .erp_c import creer_app
    donnees = json.loads(cfg.dataset_c.read_text(encoding="utf-8"))
    print(f"ERP C (simulé) sur http://127.0.0.1:{args.port}, en-tête X-API-Key: demo-key")
    uvicorn.run(creer_app(donnees), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def _tableau_de_bord(cfg: Config, args: argparse.Namespace) -> int:
    from .tableau_de_bord import ecrire
    print(ecrire(cfg, args.sortie))
    return 0


def _exporter_bi(cfg: Config, args: argparse.Namespace) -> int:
    from .export_bi import exporter
    for chemin in exporter(cfg, args.sortie):
        print(chemin)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="consolidation", description=__doc__)
    sub = p.add_subparsers(dest="commande", required=True)

    g = sub.add_parser("generer", help="écrit les trois sources (Odoo si une instance répond, FEC, entrepôt)")
    g.add_argument("--sans-odoo", action="store_true", help="ne pas semer Odoo : fabrique l'instantané sans instance")
    g.set_defaults(f=_generer)
    sub.add_parser("actualiser", help="relit les trois systèmes et reconstruit l'entrepôt").set_defaults(f=_actualiser)
    e = sub.add_parser("ecarts", help="liste les écarts")
    e.add_argument("--gravite", choices=["haute", "moyenne", "basse"])
    e.add_argument("--regle")
    e.add_argument("--limite", type=int, default=30)
    e.set_defaults(f=_ecarts)
    x = sub.add_parser("expliquer", help="explique un écart et cite ses sources")
    x.add_argument("ecart_id")
    x.set_defaults(f=_expliquer)
    c = sub.add_parser("corriger", help="corrige un écart à la source (FEC ou entrepôt), pour le voir disparaître")
    c.add_argument("ecart_id")
    c.set_defaults(f=_corriger)
    r = sub.add_parser("requete", help="exécute un SELECT en lecture seule")
    r.add_argument("sql")
    r.add_argument("--limite", type=int, default=50)
    r.set_defaults(f=_requete)
    sub.add_parser("indicateurs", help="affiche les indicateurs").set_defaults(f=_indicateurs)
    s = sub.add_parser("erp-c", help="lance l'API REST simulée de l'entrepôt")
    s.add_argument("--port", type=int, default=8171)
    s.set_defaults(f=_erp_c)
    t = sub.add_parser("tableau-de-bord", help="écrit le tableau de bord HTML")
    t.add_argument("--sortie", default="tableau_de_bord/index.html")
    t.set_defaults(f=_tableau_de_bord)
    b = sub.add_parser("exporter-bi", help="exporte le modèle en étoile en CSV, pour Power BI ou Tableau")
    b.add_argument("--sortie", default="powerbi/donnees")
    b.set_defaults(f=_exporter_bi)

    args = p.parse_args(argv)
    try:
        return args.f(Config(), args)
    except (ErreurOdoo, entrepot.RequeteRefusee, demo.CorrectionImpossible, FileNotFoundError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
