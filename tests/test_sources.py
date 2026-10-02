from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from consolidation_erp import fec, odoo
from consolidation_erp.demo import odoo_depuis_monde
from consolidation_erp.erp_c import ClientErpC, ErreurErpC, creer_app, dataset

from fake_odoo import FauxOdoo

# ------------------------------------------------------------------ B : le FEC

def test_le_fec_est_ecrit_en_cp1252_avec_virgule_decimale(monde, tmp_path):
    chemin = fec.ecrire_fec(monde, tmp_path / "fec.txt")
    brut = chemin.read_bytes()
    with pytest.raises(UnicodeDecodeError):
        brut.decode("utf-8")  # un vrai export de logiciel de compta n'est pas de l'UTF-8
    texte = brut.decode("cp1252")
    assert texte.splitlines()[0].split("\t") == fec.COLONNES
    assert "Journal des achats" in texte and "," in texte.splitlines()[1]


def test_lire_le_fec_redonne_les_ecritures_et_leur_ligne_d_origine(monde, tmp_path):
    ecritures = fec.lire_fec(fec.ecrire_fec(monde, tmp_path / "fec.txt"))
    assert ecritures[0]["ligne_fichier"] == 2  # la ligne 1 est l'en-tête
    achats = [e for e in ecritures if e["compte"] == "607000"]
    assert len(achats) == len(monde.factures)
    f = monde.factures[0]
    lu = next(e for e in achats if e["ecriture_num"] == f.ecriture_num)
    assert lu["debit"] == pytest.approx(f.ht_devise if f.devise == "EUR" else round(f.ht_devise * 1.06, 2))


def test_un_fec_sans_colonne_est_refuse_avec_le_nom_de_la_colonne(tmp_path):
    chemin = tmp_path / "mauvais.txt"
    chemin.write_text("JournalCode\tEcritureNum\nHA\t1\n", encoding="utf-8")
    with pytest.raises(fec.FecInvalide, match="colonnes absentes"):
        fec.lire_fec(chemin)


def test_un_fec_en_utf8_avec_separateur_pipe_se_lit_aussi(monde, tmp_path):
    source = fec.ecrire_fec(monde, tmp_path / "fec.txt").read_bytes().decode("cp1252").replace("\t", "|")
    chemin = tmp_path / "pipe.txt"
    chemin.write_text(source, encoding="utf-8")
    assert len(fec.lire_fec(chemin)) > 100


# ------------------------------------------------------------------ C : l'API REST

def test_l_api_refuse_une_cle_absente_ou_fausse(monde):
    client = TestClient(creer_app(dataset(monde)))
    assert client.get("/api/v1/receipts").status_code == 401
    assert client.get("/api/v1/receipts", headers={"X-API-Key": "faux"}).status_code == 401
    assert client.get("/api/v1/receipts", headers={"X-API-Key": "demo-key"}).status_code == 200


def test_le_client_suit_la_pagination_jusqu_au_bout(monde):
    donnees = dataset(monde)
    tout = ClientErpC(TestClient(creer_app(donnees))).tout("receipts")
    assert len(tout) == len(donnees["receipts"]) > 50  # plus d'une page de 50


def test_le_client_refuse_une_cle_invalide(monde):
    with pytest.raises(ErreurErpC, match="clé d'API"):
        ClientErpC(TestClient(creer_app(dataset(monde))), cle_api="faux").tout("items")


def test_le_client_reessaie_sur_429_puis_reussit():
    appels = {"n": 0}

    def handler(requete: httpx.Request) -> httpx.Response:
        appels["n"] += 1
        if appels["n"] < 3:
            return httpx.Response(429)
        return httpx.Response(200, json={"data": [{"sku": "X"}], "meta": {"page": 1, "per_page": 50, "total": 1, "total_pages": 1}})

    client = ClientErpC(httpx.Client(transport=httpx.MockTransport(handler), base_url="http://c"), attente=0)
    assert client.tout("items") == [{"sku": "X"}] and appels["n"] == 3


def test_le_client_abandonne_apres_trois_erreurs_serveur():
    client = ClientErpC(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)), base_url="http://c"), attente=0)
    with pytest.raises(ErreurErpC, match="indisponible"):
        client.tout("items")


def test_le_client_detecte_un_total_incoherent():
    def handler(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"a": 1}], "meta": {"page": 1, "per_page": 50, "total": 5, "total_pages": 1}})

    client = ClientErpC(httpx.Client(transport=httpx.MockTransport(handler), base_url="http://c"))
    with pytest.raises(ErreurErpC, match="1 lignes lues, 5 annoncées"):
        client.tout("items")


# ------------------------------------------------------------------ A : le lecteur Odoo

def test_le_lecteur_odoo_convertit_les_unites_et_pagine(monde, monkeypatch):
    monkeypatch.setattr(odoo, "TAILLE_PAGE", 20)  # 110 commandes : plusieurs pages
    faux = FauxOdoo(monde)
    lu = odoo.lire(faux)
    attendu = odoo_depuis_monde(monde)
    assert len(lu["orders"]) == len(attendu["orders"]) == 110
    douzaines = [l for o in lu["orders"] for l in o["lines"] if l["uom"] == "Dozens"]
    assert douzaines and all(l["facteur_base"] == 12.0 for l in douzaines)
    assert all(l["facteur_base"] == 1.0 for o in lu["orders"] for l in o["lines"] if l["uom"] == "Units")
    assert faux.appels > 8  # la pagination a bien tourné


def test_un_mauvais_mot_de_passe_est_dit_clairement(monkeypatch):
    class Commun:
        def authenticate(self, *a): return False
        def version(self): return {"server_version": "17.0"}

    monkeypatch.setattr(odoo.xmlrpc.client, "ServerProxy", lambda *a, **k: Commun())
    with pytest.raises(odoo.ErreurOdoo, match="authentification refusée"):
        odoo.ClientOdoo("http://x", "base", "admin", "faux")
