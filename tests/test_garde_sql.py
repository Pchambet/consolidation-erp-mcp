"""La requête en lecture seule est ouverte à Claude : elle doit tenir face à ce qu'il pourrait essayer,
volontairement ou non. Chaque barrière est testée seule, pour que la chute de l'une ne passe pas
inaperçue derrière les autres."""

from __future__ import annotations

import duckdb
import pytest

from consolidation_erp import entrepot
from consolidation_erp.entrepot import RequeteRefusee, requete_lecture_seule


def test_un_select_normal_passe(actualise):
    r = requete_lecture_seule(actualise.entrepot, "SELECT regle, count(*) AS n FROM ecarts GROUP BY regle ORDER BY n DESC")
    assert r["colonnes"] == ["regle", "n"] and r["lignes"] and not r["tronque"]


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE ecarts",
        "DELETE FROM ecarts",
        "INSERT INTO ecarts SELECT * FROM ecarts",
        "UPDATE ecarts SET gravite = 'basse'",
        "CREATE TABLE x AS SELECT 1",
        "ATTACH 'autre.duckdb'",
        "COPY ecarts TO '/tmp/x.csv'",
        "PRAGMA database_list",
        "INSTALL httpfs",
        "SELECT getenv('HOME')",
        "SELECT * FROM read_csv('/etc/passwd')",
    ],
)
def test_les_ecritures_et_les_acces_externes_sont_refuses(actualise, sql):
    with pytest.raises(RequeteRefusee):
        requete_lecture_seule(actualise.entrepot, sql)


def test_deux_instructions_sont_refusees(actualise):
    with pytest.raises(RequeteRefusee, match="une seule requête"):
        requete_lecture_seule(actualise.entrepot, "SELECT 1; SELECT 2")


def test_la_connexion_elle_meme_interdit_l_ecriture_meme_sans_le_filtre_de_mots(actualise):
    """Deuxième barrière : le fichier est ouvert en lecture seule. On contourne le filtre de mots pour le prouver."""
    con = entrepot.ouvrir_lecture(actualise.entrepot)
    try:
        with pytest.raises(duckdb.Error):
            con.execute("DELETE FROM ecarts")
    finally:
        con.close()


def test_la_connexion_elle_meme_interdit_les_fichiers_meme_sans_le_filtre_de_mots(actualise, tmp_path):
    """Troisième barrière : pas d'accès externe. `SELECT * FROM 'fichier.csv'` ne contient aucun mot que le
    filtre connaisse, et lirait le disque si la connexion le permettait."""
    fichier = tmp_path / "secret.csv"
    fichier.write_text("a\n1\n", encoding="utf-8")
    con = entrepot.ouvrir_lecture(actualise.entrepot)
    try:
        with pytest.raises(duckdb.Error):
            con.execute(f"SELECT * FROM '{fichier}'").fetchall()
    finally:
        con.close()


def test_la_configuration_est_verrouillee(actualise):
    con = entrepot.ouvrir_lecture(actualise.entrepot)
    try:
        with pytest.raises(duckdb.Error):
            con.execute("SET enable_external_access = true")
    finally:
        con.close()


def test_une_requete_trop_longue_est_interrompue(actualise):
    lourde = "SELECT count(*) FROM range(3000000) a, range(3000000) b"
    with pytest.raises(RequeteRefusee, match="interrompue"):
        requete_lecture_seule(actualise.entrepot, lourde, delai_s=0.5)


def test_le_resultat_est_borne_et_le_dit(actualise):
    r = requete_lecture_seule(actualise.entrepot, "SELECT * FROM fact_commande", limite=5)
    assert len(r["lignes"]) == 5 and r["tronque"] and "5 lignes" in r["note"]


def test_la_limite_hors_bornes_est_refusee(actualise):
    with pytest.raises(RequeteRefusee):
        requete_lecture_seule(actualise.entrepot, "SELECT 1", limite=5000)


def test_une_erreur_de_syntaxe_est_rendue_lisible(actualise):
    with pytest.raises(RequeteRefusee, match="Catalog Error"):
        requete_lecture_seule(actualise.entrepot, "SELECT * FROM table_inexistante")
