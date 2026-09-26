"""ERP B : la comptabilité, vue par son export FEC.

Le FEC (fichier des écritures comptables) est le format légal français : dix-huit colonnes, une ligne
par écriture, séparateur tabulation, dates en AAAAMMJJ, virgule décimale. Les logiciels de
comptabilité l'écrivent souvent en cp1252. Le lecteur ne suppose rien : il devine l'encodage et le
séparateur, refuse un fichier dont il manque une colonne, et garde le numéro de ligne d'origine de
chaque écriture pour que chaque écart puisse être retrouvé dans le fichier.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from pathlib import Path

from .monde import TAUX_CHF_EUR, TVA_FR, Facture, Monde

COLONNES = [
    "JournalCode", "JournalLib", "EcritureNum", "EcritureDate", "CompteNum", "CompteLib",
    "CompAuxNum", "CompAuxLib", "PieceRef", "PieceDate", "EcritureLib", "Debit", "Credit",
    "EcritureLet", "DateLet", "ValidDate", "Montantdevise", "Idevise",
]
NOM_FICHIER = "123456789FEC20261231.txt"


class FecInvalide(ValueError):
    """Le fichier n'est pas un FEC exploitable. Le message dit quoi corriger."""


def _montant(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def _date(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def lignes_de_facture(f: Facture) -> list[dict[str, str]]:
    """Les écritures d'une facture d'achat : charge, TVA déductible s'il y en a, compte fournisseur."""
    etrangere = f.devise != "EUR"
    ht = f.ht_devise if not etrangere else round(f.ht_devise * TAUX_CHF_EUR, 2)
    tva = round(ht * TVA_FR, 2) if f.avec_tva else 0.0
    ttc = round(ht + tva, 2)
    devise = {"Montantdevise": _montant(f.ht_devise), "Idevise": f.devise} if etrangere else {"Montantdevise": "", "Idevise": ""}

    def ligne(compte: str, libelle_compte: str, debit: float, credit: float, aux: bool = False, avec_devise: bool = False) -> dict[str, str]:
        return {
            "JournalCode": "HA", "JournalLib": "Journal des achats",
            "EcritureNum": str(f.ecriture_num), "EcritureDate": _date(f.date),
            "CompteNum": compte, "CompteLib": libelle_compte,
            "CompAuxNum": f.aux if aux else "", "CompAuxLib": f.lib_b if aux else "",
            "PieceRef": f.piece_ref, "PieceDate": _date(f.date),
            "EcritureLib": f"Facture {f.piece_ref} {f.po_ref} {f.lib_b}",
            "Debit": _montant(debit), "Credit": _montant(credit),
            "EcritureLet": "", "DateLet": "", "ValidDate": _date(f.date),
            **(devise if avec_devise else {"Montantdevise": "", "Idevise": ""}),
        }

    lignes = [ligne("607000", "Achats de marchandises", ht, 0.0, avec_devise=True)]
    if tva:
        lignes.append(ligne("445660", "TVA déductible sur biens et services", tva, 0.0))
    lignes.append(ligne("401000", "Fournisseurs", 0.0, ttc, aux=True, avec_devise=True))
    return lignes


def ecrire_fec(monde: Monde, chemin: Path) -> Path:
    """Écrit le FEC du monde de démonstration, en cp1252 comme un vrai logiciel de compta."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tampon = io.StringIO(newline="")
    ecrivain = csv.DictWriter(tampon, fieldnames=COLONNES, delimiter="\t", lineterminator="\r\n")
    ecrivain.writeheader()
    for f in monde.factures:
        ecrivain.writerows(lignes_de_facture(f))
    chemin.write_bytes(tampon.getvalue().encode("cp1252"))
    return chemin


def _decoder(brut: bytes) -> tuple[str, str]:
    for encodage in ("utf-8-sig", "cp1252"):
        try:
            return brut.decode(encodage), encodage
        except UnicodeDecodeError:
            continue
    raise FecInvalide("encodage illisible (ni UTF-8 ni cp1252)")


def _flottant(brut: str) -> float | None:
    brut = brut.strip().replace(" ", "").replace(" ", "")
    if not brut:
        return None
    return float(brut.replace(",", "."))


def lire_fec(chemin: Path) -> list[dict]:
    """Toutes les écritures du fichier, typées, avec leur numéro de ligne d'origine."""
    texte, _ = _decoder(chemin.read_bytes())
    en_tete = texte.splitlines()[0] if texte else ""
    separateur = max(("\t", "|", ";"), key=en_tete.count)
    lecteur = csv.DictReader(io.StringIO(texte), delimiter=separateur)
    manquantes = [c for c in COLONNES if c not in (lecteur.fieldnames or [])]
    if manquantes:
        raise FecInvalide(f"colonnes absentes du FEC : {', '.join(manquantes)}")
    ecritures: list[dict] = []
    for numero_ligne, brut in enumerate(lecteur, start=2):
        try:
            ecritures.append(
                {
                    "ecriture_num": int(brut["EcritureNum"]),
                    "journal": brut["JournalCode"],
                    "date": dt.datetime.strptime(brut["EcritureDate"], "%Y%m%d").date(),
                    "compte": brut["CompteNum"],
                    "aux_num": brut["CompAuxNum"] or None,
                    "aux_lib": brut["CompAuxLib"] or None,
                    "piece_ref": brut["PieceRef"],
                    "libelle": brut["EcritureLib"],
                    "debit": _flottant(brut["Debit"]) or 0.0,
                    "credit": _flottant(brut["Credit"]) or 0.0,
                    "montant_devise": _flottant(brut["Montantdevise"]),
                    "idevise": brut["Idevise"] or None,
                    "ligne_fichier": numero_ligne,
                }
            )
        except (ValueError, KeyError) as exc:
            raise FecInvalide(f"ligne {numero_ligne} illisible : {exc}") from exc
    return ecritures
