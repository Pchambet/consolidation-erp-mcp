"""Le monde de démonstration.

Trois systèmes décrivent les mêmes achats d'un groupe fictif :

* A, un Odoo 17 : fournisseurs, produits, commandes d'achat ;
* B, la comptabilité : un export FEC, avec les factures fournisseurs ;
* C, l'entrepôt : une API REST, avec les réceptions de marchandises.

Tout est synthétique : noms, numéros de TVA, montants. Le monde est déterministe (graine fixe).

Un monde sain est parfaitement cohérent. On y plante ensuite des écarts connus, un par un, et on
garde la liste (`verite_terrain`). Les contrôles sont testés contre cette liste : ils doivent
retrouver exactement ces écarts, ni plus ni moins.
"""

from __future__ import annotations

import datetime as dt
import random
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field

AS_OF = dt.date(2026, 9, 26)
TAUX_CHF_EUR = 1.06  # 1 CHF vaut 1,06 EUR, taux fixe de la maquette
TVA_FR = 0.20

_TOPONYMES = [
    "Léman",
    "Jura",
    "Salève",
    "Arve",
    "Rhône",
    "Chablais",
    "Faucigny",
    "Aravis",
    "Vuache",
    "Bornes",
    "Genevois",
    "Annecy",
    "Valserine",
    "Vercors",
    "Bauges",
    "Mont-Blanc",
]
_METIERS = [
    "Mécanique",
    "Emballages",
    "Logistique",
    "Outillage",
    "Plastiques",
    "Fixations",
    "Textiles",
    "Papeterie",
    "Chimie",
    "Électronique",
    "Câblage",
    "Fonderie",
    "Usinage",
    "Conditionnement",
    "Manutention",
    "Étiquettes",
]
_FORMES_FR = ["SA", "SARL", "SAS"]
_FORMES_CH = ["SA", "Sàrl", "AG"]

# (SKU, nom, unité d'achat dans Odoo, prix par pièce en EUR). Les pièces achetées « à la douzaine »
# sont le piège classique : la quantité de la ligne est en douzaines, pas en pièces.
_PRODUITS = [
    ("VIS-M8-INOX", "Vis inox M8", "Dozens", 0.42),
    ("VIS-M10-ACIER", "Vis acier M10", "Dozens", 0.31),
    ("ECR-M8", "Écrou M8", "Dozens", 0.18),
    ("RON-M8", "Rondelle M8", "Dozens", 0.07),
    ("JNT-TOR-20", "Joint torique 20 mm", "Dozens", 0.95),
    ("ETQ-A6", "Étiquette adhésive A6", "Dozens", 0.05),
    ("GNT-NIT-L", "Gant nitrile L", "Dozens", 0.60),
    ("BOU-PLA-30", "Bouchon plastique 30 mm", "Dozens", 0.12),
    ("ROUL-6204", "Roulement 6204", "Units", 4.80),
    ("ROUL-6206", "Roulement 6206", "Units", 6.10),
    ("CAR-40X30", "Carton 40x30", "Units", 0.85),
    ("CAR-60X40", "Carton 60x40", "Units", 1.30),
    ("PAL-120X80", "Palette bois 120x80", "Units", 9.50),
    ("FIL-ETIR-50", "Film étirable 50 cm", "Units", 7.20),
    ("RUB-ADH-48", "Ruban adhésif 48 mm", "Units", 1.10),
    ("CAB-3G15", "Câble 3G1,5 (m)", "Units", 1.75),
    ("CON-RJ45", "Connecteur RJ45", "Units", 0.90),
    ("CAP-PT100", "Capteur PT100", "Units", 14.40),
    ("REL-24V", "Relais 24 V", "Units", 5.30),
    ("FUS-10A", "Fusible 10 A", "Units", 0.28),
    ("COL-200", "Collier de serrage 200 mm", "Units", 0.09),
    ("HUI-ISO46", "Huile hydraulique ISO 46 (L)", "Units", 3.90),
    ("SAC-30L", "Sac plastique 30 L", "Units", 0.11),
    ("PIL-AA", "Pile AA", "Units", 0.38),
]


def normaliser_nom(nom: str) -> str:
    """Nom sans accents ni forme juridique, en majuscules : la clé d'un rapprochement par le nom."""
    sans_accents = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()
    mots = [m for m in sans_accents.upper().replace(".", "").replace(",", " ").split()]
    formes = {"SA", "SARL", "SAS", "AG", "SRL"}
    while mots and mots[-1] in formes:
        mots.pop()
    return " ".join(mots)


def _pointer(nom: str) -> str:
    """Le même nom avec sa forme juridique ponctuée : « X SA » devient « X S.A. »."""
    for forme, pointee in (("SARL", "S.A.R.L."), ("SAS", "S.A.S."), ("Sàrl", "S.à r.l."), ("SA", "S.A."), ("AG", "A.G.")):
        if nom.endswith(" " + forme):
            return nom[: -len(forme)] + pointee
    return nom


@dataclass
class Partenaire:
    ref: str  # code dans Odoo, identique au compte auxiliaire de la comptabilité
    nom: str  # nom dans Odoo
    vat: str
    pays: str  # FR ou CH
    devise: str  # EUR ou CHF
    code_c: str  # code dans l'entrepôt
    nom_c: str
    coef_prix: float
    lib_b: str  # libellé du compte auxiliaire en comptabilité


@dataclass
class Produit:
    sku: str
    nom: str
    unite_achat: str  # « Units » ou « Dozens »
    prix_base: float  # par pièce, en EUR

    @property
    def facteur(self) -> int:
        return 12 if self.unite_achat == "Dozens" else 1


@dataclass
class Ligne:
    sku: str
    qty: float  # dans l'unité de la ligne
    unite: str
    prix_unitaire: float  # par unité de la ligne, dans la devise de la commande


@dataclass
class Commande:
    ref: str
    partenaire_ref: str
    date: dt.date
    devise: str
    lignes: list[Ligne] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(sum(l.qty * l.prix_unitaire for l in self.lignes), 2)


@dataclass
class Reception:
    ref: str
    po_ref: str
    code_c: str
    date: dt.date
    lignes: list[list] = field(default_factory=list)  # [sku, quantité en pièces]


@dataclass
class Facture:
    ecriture_num: int
    piece_ref: str
    po_ref: str
    date: dt.date
    aux: str
    lib_b: str
    ht_devise: float
    devise: str
    avec_tva: bool


@dataclass
class Ecart:
    regle: str
    cle: str
    note: str


@dataclass
class Monde:
    partenaires: list[Partenaire]
    doublons_odoo: list[Partenaire]  # mêmes numéros de TVA, deuxième fiche dans Odoo
    produits: list[Produit]
    commandes: list[Commande]
    receptions: list[Reception]
    factures: list[Facture]
    verite_terrain: list[Ecart]

    def partenaire(self, ref: str) -> Partenaire:
        for p in self.partenaires + self.doublons_odoo:
            if p.ref == ref:
                return p
        raise KeyError(ref)

    def partenaire_c(self, code_c: str) -> Partenaire:
        for p in self.partenaires:
            if p.code_c == code_c:
                return p
        raise KeyError(code_c)


# ------------------------------------------------------------------ fabrication du monde sain


def _partenaires(rng: random.Random) -> list[Partenaire]:
    combinaisons = [(t, m) for t in _TOPONYMES for m in _METIERS]
    rng.shuffle(combinaisons)
    vus: set[str] = set()
    partenaires: list[Partenaire] = []
    for i in range(44):
        topo, metier = combinaisons[i]
        suisse = i >= 36
        forme = rng.choice(_FORMES_CH if suisse else _FORMES_FR)
        if i == 9:
            forme = "SA"  # le jumeau planté plus tard s'appellera pareil, en SARL
        nom = f"{metier} {topo} {forme}"
        while True:
            vat = (
                f"CHE-{rng.randint(100, 999)}.{rng.randint(100, 999)}.{rng.randint(100, 999)} TVA"
                if suisse
                else f"FR{rng.randint(10, 99)}{rng.randint(100_000_000, 999_999_999)}"
            )
            if vat not in vus:
                vus.add(vat)
                break
        partenaires.append(
            Partenaire(
                ref=f"F{i + 1:04d}",
                nom=nom,
                vat=vat,
                pays="CH" if suisse else "FR",
                devise="CHF" if suisse else "EUR",
                code_c=f"SUP{i + 1:05d}",
                nom_c=_pointer(nom),
                coef_prix=round(rng.uniform(0.92, 1.08), 3),
                lib_b=normaliser_nom(f"{metier} {topo}"),
            )
        )
    return partenaires


def _produits() -> list[Produit]:
    return [Produit(*p) for p in _PRODUITS]


def _commandes(rng: random.Random, partenaires: list[Partenaire], produits: list[Produit]) -> list[Commande]:
    debut, fin = dt.date(2026, 3, 2), dt.date(2026, 9, 14)
    jours = (fin - debut).days
    dates = sorted(debut + dt.timedelta(days=rng.randint(0, jours)) for _ in range(110))
    commandes: list[Commande] = []
    for n, date in enumerate(dates, start=1):
        p = rng.choice(partenaires)
        lignes: list[Ligne] = []
        for prod in rng.sample(produits, rng.randint(1, 3)):
            qty = float(rng.randrange(5, 61) if prod.unite_achat == "Dozens" else rng.randrange(20, 501, 10))
            prix_eur = prod.prix_base * prod.facteur * p.coef_prix
            prix = round(prix_eur / TAUX_CHF_EUR if p.devise == "CHF" else prix_eur, 2)
            lignes.append(Ligne(prod.sku, qty, prod.unite_achat, prix))
        commandes.append(Commande(f"P{n:05d}", p.ref, date, p.devise, lignes))
    return commandes


def _receptions_et_factures(
    rng: random.Random, m_partenaires: list[Partenaire], commandes: list[Commande], produits: list[Produit]
) -> tuple[list[Reception], list[Facture]]:
    facteur = {p.sku: p.facteur for p in produits}
    par_ref = {p.ref: p for p in m_partenaires}
    receptions: list[Reception] = []
    factures: list[Facture] = []
    for c in commandes:
        p = par_ref[c.partenaire_ref]
        recu = c.date + dt.timedelta(days=rng.randint(2, 10))
        if recu > AS_OF:
            continue
        receptions.append(
            Reception(
                ref="",
                po_ref=c.ref,
                code_c=p.code_c,
                date=recu,
                lignes=[[l.sku, l.qty * facteur[l.sku]] for l in c.lignes],
            )
        )
        facture = recu + dt.timedelta(days=rng.randint(3, 20))
        if facture > AS_OF:
            continue
        factures.append(
            Facture(
                ecriture_num=0,
                piece_ref="",
                po_ref=c.ref,
                date=facture,
                aux=p.ref,
                lib_b=p.lib_b,
                ht_devise=c.total,
                devise=c.devise,
                avec_tva=p.pays == "FR",
            )
        )
    _numeroter(receptions, factures)
    return receptions, factures


def _numeroter(receptions: list[Reception], factures: list[Facture]) -> None:
    for n, r in enumerate(sorted(receptions, key=lambda r: (r.date, r.po_ref)), start=1):
        r.ref = f"REC-{n:06d}"
    for n, f in enumerate(sorted(factures, key=lambda f: (f.date, f.po_ref)), start=1):
        f.ecriture_num = n
        if not f.piece_ref:
            f.piece_ref = f"FA26-{n:04d}"


# ------------------------------------------------------------------ les écarts plantés


def _planter(rng: random.Random, m: Monde) -> None:
    verite = m.verite_terrain
    cmd = {c.ref: c for c in m.commandes}
    rec = {r.po_ref: r for r in m.receptions}
    fac = {f.po_ref: f for f in m.factures}
    limite = AS_OF - dt.timedelta(days=60)
    anciens = [c for c in m.commandes if c.date <= limite and c.ref in rec and c.ref in fac]
    rng.shuffle(anciens)
    pris: set[str] = set()

    def prendre(n: int, predicat=lambda c: True) -> list[Commande]:
        sortie: list[Commande] = []
        for c in anciens:
            if c.ref in pris or not predicat(c):
                continue
            sortie.append(c)
            pris.add(c.ref)
            if len(sortie) == n:
                return sortie
        raise RuntimeError(f"monde trop petit pour planter {n} écarts")

    def a_douzaines(c: Commande) -> bool:
        return any(l.unite == "Dozens" for l in c.lignes)

    def fr(c: Commande) -> bool:
        return m.partenaire(c.partenaire_ref).pays == "FR"

    def ch(c: Commande) -> bool:
        return m.partenaire(c.partenaire_ref).pays == "CH"

    # 1. Un partenaire jumeau : même nom à la forme juridique près, autre numéro de TVA.
    base = m.partenaires[9]
    jumeau = Partenaire(
        ref="F0045",
        nom=base.nom.removesuffix(" SA") + " SARL",
        vat=f"FR{rng.randint(10, 99)}{rng.randint(100_000_000, 999_999_999)}",
        pays="FR",
        devise="EUR",
        code_c="SUP00045",
        nom_c=_pointer(base.nom.removesuffix(" SA") + " SARL"),
        coef_prix=1.0,
        lib_b=base.lib_b,
    )
    m.partenaires.append(jumeau)
    trois = prendre(3, lambda c: fr(c) and c.partenaire_ref not in {base.ref})
    for c in trois:
        _reassigner(m, c, jumeau, cmd, rec, fac)
    # Une de ses trois factures est saisie sur un compte auxiliaire qui n'existe pas ailleurs, et
    # dont le libellé convient aux deux jumeaux : la comptabilité ne sait pas à qui elle a écrit.
    f = fac[trois[0].ref]
    f.aux, f.lib_b = "F0777", base.lib_b
    verite.append(Ecart("partenaire_ambigu", "F0777", "compte auxiliaire F0777, libellé commun à deux fournisseurs"))

    # 2. Deux fournisseurs qui ont une deuxième fiche dans Odoo, avec le même numéro de TVA.
    #    Les factures restent sur le compte de la première fiche : sans dédoublonnage, elles
    #    ressembleraient à des factures sur le mauvais fournisseur. On choisit ceux qui ont le
    #    plus de commandes récentes, pour que la démonstration ait de la matière.
    compte = Counter(
        c.partenaire_ref for c in anciens if c.ref not in pris and fr(c) and c.partenaire_ref not in (base.ref, jumeau.ref)
    )
    for k, (ref, _) in enumerate(compte.most_common(2)):
        orig = m.partenaire(ref)
        double = Partenaire(
            ref=f"F09{k + 1:02d}",
            nom=_pointer(orig.nom),
            vat=orig.vat,
            pays=orig.pays,
            devise=orig.devise,
            code_c=orig.code_c,
            nom_c=orig.nom_c,
            coef_prix=orig.coef_prix,
            lib_b=orig.lib_b,
        )
        m.doublons_odoo.append(double)
        verite.append(Ecart("partenaire_double", orig.vat, f"{orig.ref} et {double.ref} pour la même TVA"))
        for c in [c for c in anciens if c.partenaire_ref == orig.ref and c.ref not in pris][:2]:
            pris.add(c.ref)
            c.partenaire_ref = double.ref  # commande passée sur la deuxième fiche

    # 3. Réception absente : une facturée (haute), une non facturée (moyenne).
    for i, c in enumerate(prendre(2)):
        m.receptions.remove(rec.pop(c.ref))
        if i == 1:
            m.factures.remove(fac.pop(c.ref))
        verite.append(Ecart("commande_sans_reception", c.ref, "facturée" if i == 0 else "non facturée"))

    # 4. Réception sans commande.
    p0 = m.partenaires[0]
    m.receptions.append(Reception("", "P09999", p0.code_c, AS_OF - dt.timedelta(days=40), [["PAL-120X80", 120.0]]))
    verite.append(Ecart("reception_sans_commande", "P09999", "réception qui cite une commande inconnue"))

    # 5. Reçue depuis plus de 30 jours, jamais facturée.
    for c in prendre(3):
        m.factures.remove(fac.pop(c.ref))
        verite.append(Ecart("commande_sans_facture", c.ref, "reçue, jamais facturée"))

    # 6. Facture saisie deux fois.
    for c in prendre(2):
        f = fac[c.ref]
        m.factures.append(
            Facture(0, f.piece_ref, f.po_ref, f.date + dt.timedelta(days=2), f.aux, f.lib_b, f.ht_devise, f.devise, f.avec_tva)
        )
        verite.append(Ecart("facture_en_double", c.ref, f"facture {f.piece_ref} saisie deux fois"))

    # 7. Facture antérieure à la commande.
    for c in prendre(2):
        fac[c.ref].date = c.date - dt.timedelta(days=4)
        verite.append(Ecart("facture_avant_commande", c.ref, "facture datée 4 jours avant la commande"))

    # 8. Montant facturé différent du montant commandé.
    for c, coef in zip(prendre(2), (1.10, 0.93), strict=True):
        fac[c.ref].ht_devise = round(c.total * coef, 2)
        verite.append(Ecart("ecart_montant", c.ref, f"facturé à {coef:.2f} fois le commandé"))

    # 9. Devise incohérente : le même chiffre, une autre devise.
    (a,) = prendre(1, ch)
    fac[a.ref].devise = "EUR"  # commandé en CHF, facturé « en EUR » avec le même montant
    verite.append(Ecart("devise_incoherente", a.ref, "commande en CHF, facture en EUR pour le même chiffre"))
    (b,) = prendre(1, fr)
    fac[b.ref].devise = "CHF"  # commandé en EUR, facturé « en CHF » avec le même montant
    verite.append(Ecart("devise_incoherente", b.ref, "commande en EUR, facture en CHF pour le même chiffre"))

    # 10. Unité : commandé en douzaines, reçu en pièces avec le même nombre.
    for c in prendre(2, a_douzaines):
        r = rec[c.ref]
        ligne_dz = next(l for l in c.lignes if l.unite == "Dozens")
        for lr in r.lignes:
            if lr[0] == ligne_dz.sku:
                lr[1] = ligne_dz.qty
        verite.append(
            Ecart(
                "unite_incoherente",
                c.ref,
                f"{ligne_dz.sku} : {ligne_dz.qty:g} douzaines commandées, {ligne_dz.qty:g} pièces reçues",
            )
        )

    # 11. Livraison incomplète.
    for c in prendre(2, lambda c: not a_douzaines(c) and fr(c)):
        r = rec[c.ref]
        r.lignes[0][1] = float(int(r.lignes[0][1] * 0.8))
        verite.append(Ecart("ecart_quantite", c.ref, f"{r.lignes[0][0]} : 80 % seulement de la quantité reçue"))

    # 12. Facture inscrite sur le compte d'un autre fournisseur.
    (w,) = prendre(1, fr)
    autre = next(p for p in m.partenaires if p.ref not in (w.partenaire_ref, "F0777") and p.pays == "FR" and p.ref != "F0045")
    fac[w.ref].aux, fac[w.ref].lib_b = autre.ref, autre.lib_b
    verite.append(Ecart("facture_mauvais_fournisseur", w.ref, f"commande {w.partenaire_ref}, facture sur {autre.ref}"))

    m.receptions.sort(key=lambda r: (r.date, r.po_ref))
    _numeroter(m.receptions, m.factures)
    m.factures.sort(key=lambda f: f.ecriture_num)


def _reassigner(m: Monde, c: Commande, p: Partenaire, cmd, rec, fac) -> None:
    """Passe une commande, sa réception et sa facture à un autre fournisseur, partout."""
    c.partenaire_ref = p.ref
    c.devise = p.devise
    rec[c.ref].code_c = p.code_c
    f = fac[c.ref]
    f.aux, f.lib_b, f.devise, f.avec_tva = p.ref, p.lib_b, p.devise, p.pays == "FR"


def construire(seed: int = 2026, planter: bool = True) -> Monde:
    """Le monde de démonstration. `planter=False` donne le monde sain, sans aucun écart."""
    rng = random.Random(seed)
    partenaires = _partenaires(rng)
    produits = _produits()
    commandes = _commandes(rng, partenaires, produits)
    receptions, factures = _receptions_et_factures(rng, partenaires, commandes, produits)
    monde = Monde(partenaires, [], produits, commandes, receptions, factures, [])
    if planter:
        _planter(random.Random(seed + 1), monde)
    return monde


def renommer_commandes(monde: Monde, correspondance: dict[str, str]) -> None:
    """Si Odoo a numéroté les commandes autrement, les deux autres systèmes suivent."""
    for c in monde.commandes:
        c.ref = correspondance.get(c.ref, c.ref)
    for r in monde.receptions:
        r.po_ref = correspondance.get(r.po_ref, r.po_ref)
    for f in monde.factures:
        f.po_ref = correspondance.get(f.po_ref, f.po_ref)
    for e in monde.verite_terrain:
        e.cle = correspondance.get(e.cle, e.cle)


def verite_en_dict(monde: Monde) -> list[dict]:
    return [asdict(e) for e in monde.verite_terrain]
