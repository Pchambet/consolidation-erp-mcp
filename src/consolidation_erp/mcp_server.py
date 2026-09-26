"""Serveur MCP : Claude interroge les trois ERP consolidés.

Six outils. Cinq sont en lecture seule et ne peuvent rien modifier : ils lisent l'entrepôt de
données construit par la consolidation, jamais les ERP eux-mêmes. Le sixième, `rafraichir`, relit les
trois systèmes et reconstruit l'entrepôt ; il ne les modifie pas non plus.

Chaque réponse cite le système d'origine (A, B ou C) et l'identifiant de l'enregistrement, pour que
l'utilisateur puisse vérifier dans l'ERP concerné ce que Claude lui a dit.

Lancer :  uv run consolidation-mcp   (protocole stdio, à déclarer dans Claude Desktop, voir le README)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import entrepot
from .erp_c import ErreurErpC
from .fec import FecInvalide
from .odoo import ErreurOdoo
from .pipeline import Config, actualiser

INSTRUCTIONS = """\
Ce serveur donne accès aux données consolidées de trois systèmes d'un groupe fictif :
  A = Odoo 17 (fournisseurs, produits, commandes d'achat)
  B = la comptabilité (export FEC : factures fournisseurs)
  C = l'entrepôt (API REST : réceptions de marchandises)
Le contrôle central est le rapprochement en trois voies : commande (A), réception (C), facture (B).

Comment t'en servir :
- Pour un chiffre déjà défini, utilise lister_indicateurs plutôt que de le recalculer.
- Pour savoir ce qui ne va pas : ecarts_ouverts, puis expliquer_ecart sur un identifiant.
- Pour une question libre : decrire_modele, puis requete_lecture_seule (SELECT uniquement, DuckDB).
- N'affirme jamais qu'il y a un écart que ecarts_ouverts ne liste pas. Cite toujours le système
  (A, B ou C) et l'identifiant de l'enregistrement concerné.
- Les montants sont en EUR ; les commandes suisses en CHF sont converties au taux fixe de 1,06.
- rafraichir relit les trois systèmes : à utiliser quand l'utilisateur dit avoir corrigé quelque chose.
"""

LECTURE = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
ACTUALISATION = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)


class EntrepotAbsent(RuntimeError):
    pass


# Ces erreurs sont anticipées : leur message est fait pour être lu par Claude, qui peut corriger sa
# requête ou dire à l'utilisateur quoi faire. Une autre exception reste un crash, au message masqué.
ATTENDUES = (entrepot.RequeteRefusee, EntrepotAbsent, ErreurOdoo, ErreurErpC, FecInvalide, FileNotFoundError)


def _appeler(fonction) -> dict[str, Any]:
    try:
        return fonction()
    except ATTENDUES as exc:
        raise ToolError(str(exc)) from exc


def creer_serveur(cfg: Config | None = None) -> MCPServer:
    cfg = cfg or Config()
    serveur = MCPServer("consolidation-erp", instructions=INSTRUCTIONS)

    def chemin() -> Path:
        if not cfg.entrepot.exists():
            raise EntrepotAbsent("l'entrepôt n'est pas encore construit : appeler l'outil rafraichir")
        return cfg.entrepot

    @serveur.tool(annotations=LECTURE)
    def decrire_modele() -> dict[str, Any]:
        """Liste les tables du modèle consolidé et leurs colonnes. À appeler avant d'écrire une requête SQL."""
        return _appeler(lambda: entrepot.decrire_modele(chemin()))

    @serveur.tool(annotations=LECTURE)
    def lister_indicateurs() -> dict[str, Any]:
        """Les indicateurs de qualité et de suivi, avec leur définition et leur valeur à la dernière actualisation."""
        return _appeler(lambda: {"indicateurs": entrepot.indicateurs(chemin()), "sources": entrepot.sources(chemin())})

    @serveur.tool(annotations=LECTURE)
    def ecarts_ouverts(gravite: str | None = None, regle: str | None = None, limite: int = 20) -> dict[str, Any]:
        """Liste les écarts détectés entre les trois systèmes, les plus graves d'abord.

        gravite : haute, moyenne ou basse (facultatif). regle : nom d'une règle de contrôle (facultatif).
        """
        return _appeler(lambda: entrepot.ecarts_ouverts(chemin(), gravite, regle, limite))

    @serveur.tool(annotations=LECTURE)
    def expliquer_ecart(ecart_id: str) -> dict[str, Any]:
        """Explique un écart : ce qui a été constaté, la règle, les enregistrements de chaque système
        impliqué (avec leur identifiant), le responsable et l'action recommandée."""
        return _appeler(lambda: entrepot.expliquer_ecart(chemin(), ecart_id))

    @serveur.tool(annotations=LECTURE)
    def requete_lecture_seule(sql: str, limite: int = 100) -> dict[str, Any]:
        """Exécute une requête SELECT (DuckDB) sur le modèle consolidé. Une seule instruction, lecture seule,
        durée limitée à 10 secondes, 1000 lignes au plus. Appeler decrire_modele pour connaître les tables."""
        return _appeler(lambda: entrepot.requete_lecture_seule(chemin(), sql, limite))

    @serveur.tool(annotations=ACTUALISATION)
    def rafraichir() -> dict[str, Any]:
        """Relit les trois systèmes (Odoo, comptabilité, entrepôt) et reconstruit l'entrepôt. Rend ce qui a été
        lu, le nombre d'écarts, et les écarts nouveaux ou résolus depuis la dernière actualisation."""
        return _appeler(lambda: actualiser(cfg))

    return serveur


def main() -> None:
    creer_serveur().run()


if __name__ == "__main__":
    main()
