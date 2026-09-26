"""Le serveur MCP, testé comme le ferait Claude : par un client MCP, en mémoire puis par un vrai
sous-processus stdio, qui est le transport que Claude Desktop utilise."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
from mcp import StdioServerParameters
from mcp.client import Client

from consolidation_erp.mcp_server import creer_serveur

OUTILS = {"decrire_modele", "lister_indicateurs", "ecarts_ouverts", "expliquer_ecart", "requete_lecture_seule", "rafraichir"}


def appeler(serveur, scenario):
    async def executer():
        async with Client(serveur) as client:
            return await scenario(client)

    return asyncio.run(executer())


def test_six_outils_dont_cinq_en_lecture_seule(actualise):
    async def scenario(c):
        return (await c.list_tools()).tools

    outils = appeler(creer_serveur(actualise), scenario)
    assert {o.name for o in outils} == OUTILS
    lecture = {o.name for o in outils if o.annotations and o.annotations.read_only_hint}
    assert lecture == OUTILS - {"rafraichir"}
    assert all(o.description for o in outils)


def test_ecarts_ouverts_filtre_par_gravite(actualise):
    async def scenario(c):
        return await c.call_tool("ecarts_ouverts", {"gravite": "haute"})

    r = appeler(creer_serveur(actualise), scenario)
    assert not r.is_error
    donnees = r.structured_content
    assert donnees["total"] == 8 and {e["gravite"] for e in donnees["ecarts"]} == {"haute"}


def test_expliquer_un_ecart_cite_le_systeme_et_l_identifiant(actualise):
    async def scenario(c):
        liste = (await c.call_tool("ecarts_ouverts", {"regle": "facture_en_double", "limite": 1})).structured_content
        return await c.call_tool("expliquer_ecart", {"ecart_id": liste["ecarts"][0]["ecart_id"]})

    r = appeler(creer_serveur(actualise), scenario)
    e = r.structured_content
    assert e["regle"] == "facture_en_double" and e["responsable"] == "Comptabilité fournisseurs"
    assert e["provenance"]["enregistrements"]["B"]["ecritures"]
    assert "action_recommandee" in e


@pytest.mark.parametrize("sql,mot", [("DROP TABLE ecarts", "DROP"), ("SELECT 1; SELECT 2", "une seule requête")])
def test_une_requete_refusee_dit_pourquoi_a_claude(actualise, sql, mot):
    async def scenario(c):
        return await c.call_tool("requete_lecture_seule", {"sql": sql})

    r = appeler(creer_serveur(actualise), scenario)
    assert r.is_error
    assert mot in r.content[0].text  # le message n'est pas masqué : Claude peut corriger


def test_sans_entrepot_le_message_dit_quoi_faire(cfg):
    async def scenario(c):
        return await c.call_tool("lister_indicateurs", {})

    r = appeler(creer_serveur(cfg), scenario)
    assert r.is_error and "rafraichir" in r.content[0].text


def test_rafraichir_construit_puis_ne_change_rien_a_la_seconde_fois(cfg):
    async def scenario(c):
        premiere = (await c.call_tool("rafraichir", {})).structured_content
        seconde = (await c.call_tool("rafraichir", {})).structured_content
        return premiere, seconde

    premiere, seconde = appeler(creer_serveur(cfg), scenario)
    assert premiere["ecarts"] == seconde["ecarts"] == 22
    assert seconde["nouveaux"] == [] and seconde["resolus"] == []
    assert {s["systeme"] for s in seconde["sources"]} == {"A", "B", "C"}


def test_une_question_libre_passe_par_decrire_modele_puis_une_requete(actualise):
    async def scenario(c):
        modele = (await c.call_tool("decrire_modele", {})).structured_content
        r = await c.call_tool(
            "requete_lecture_seule",
            {"sql": "SELECT count(*) AS n FROM dim_partenaire WHERE fiches_odoo > 1"},
        )
        return modele, r.structured_content

    modele, resultat = appeler(creer_serveur(actualise), scenario)
    tables = {t["table_name"] for t in modele["tables"]}
    assert {"fact_commande", "fact_facture", "ecarts", "pont_partenaire", "v_rapprochement"} <= tables
    assert not any(t.startswith("stg_") for t in tables)
    assert resultat["lignes"] == [[2]]


def test_le_serveur_tourne_en_sous_processus_stdio(actualise):
    """Le transport réel : le protocole passe par stdin et stdout, donc rien d'autre ne doit y être écrit."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "consolidation_erp.mcp_server"],
        env={**os.environ, "CONSOLIDATION_DATA": str(actualise.donnees), "ODOO_MODE": "instantane"},
    )

    async def scenario(c):
        outils = {t.name for t in (await c.list_tools()).tools}
        indicateurs = (await c.call_tool("lister_indicateurs", {})).structured_content["indicateurs"]
        return outils, {i["cle"]: i["valeur"] for i in indicateurs}

    outils, valeurs = appeler(params, scenario)
    assert outils == OUTILS
    assert valeurs["ecarts_ouverts"] == 22
