"""Les contrôles : chacun est une requête SQL qui liste les lignes en écart.

Un contrôle rend, pour chaque écart : sa clé (la commande, le numéro de TVA, le compte auxiliaire), le
montant en jeu en EUR, et un `detail` qui cite les enregistrements de chaque système impliqué. C'est ce
détail qui permet d'expliquer un écart à un humain et de savoir où le corriger.

Les seuils sont ceux d'un service achats ordinaire : une commande reçue depuis plus de 30 jours et
jamais facturée, un écart de facturation de plus de 1 %, une livraison de moins de 98 % de la quantité.
"""

from __future__ import annotations

from dataclasses import dataclass

DELAI_JOURS = 30
TOLERANCE_MONTANT = 0.01
TOLERANCE_QUANTITE = 0.02


@dataclass(frozen=True)
class Regle:
    nom: str
    libelle: str
    gravite: str          # haute, moyenne, basse
    responsable: str
    sql: str
    gabarit: str          # résumé en français, rempli avec les colonnes de la ligne
    action: str           # ce qu'il faut faire, et dans quel système


AS_OF = "(SELECT as_of FROM parametres)"

REGLES: list[Regle] = [
    Regle(
        "commande_sans_reception",
        "Commande ancienne sans réception",
        "moyenne", "Achats",
        f"""
        SELECT c.po_ref AS cle, c.po_ref, c.partner_key, c.total_eur AS montant_eur,
               CASE WHEN EXISTS (SELECT 1 FROM fact_facture f WHERE f.po_ref = c.po_ref) THEN 'haute' END AS gravite,
               (date_diff('day', c.date_commande, {AS_OF})) AS jours,
               EXISTS (SELECT 1 FROM fact_facture f WHERE f.po_ref = c.po_ref) AS facturee,
               json_object('A', json_object('table', 'purchase.order', 'id', c.odoo_id, 'date', c.date_commande,
                                             'total', c.total_devise, 'devise', c.devise)) AS detail
        FROM fact_commande c
        WHERE c.date_commande < {AS_OF} - INTERVAL {DELAI_JOURS} DAY
          AND NOT EXISTS (SELECT 1 FROM fact_reception r WHERE r.po_ref = c.po_ref)
        """,
        "Commande {po_ref} passée il y a {jours} jours, aucune réception dans l'entrepôt{suite_facture}",
        "Vérifier avec le fournisseur si la marchandise est arrivée ; si oui, créer la réception dans l'entrepôt (C), sinon relancer ou annuler dans Odoo (A).",
    ),
    Regle(
        "reception_sans_commande",
        "Réception qui cite une commande inconnue",
        "moyenne", "Logistique",
        """
        SELECT r.po_ref AS cle, r.po_ref, r.partner_key, NULL::DOUBLE AS montant_eur, NULL AS gravite,
               r.receipt_ref, r.recu_le,
               json_object('C', json_object('receipt_ref', r.receipt_ref, 'recu_le', r.recu_le, 'supplier_code', r.supplier_code)) AS detail
        FROM fact_reception r
        WHERE NOT EXISTS (SELECT 1 FROM fact_commande c WHERE c.po_ref = r.po_ref)
        """,
        "La réception {receipt_ref} cite la commande {po_ref}, qui n'existe pas dans Odoo",
        "Corriger la référence de commande sur la réception (C) ou créer la commande manquante (A).",
    ),
    Regle(
        "commande_sans_facture",
        "Marchandise reçue depuis plus de 30 jours, jamais facturée",
        "moyenne", "Comptabilité fournisseurs",
        f"""
        SELECT c.po_ref AS cle, c.po_ref, c.partner_key, c.total_eur AS montant_eur, NULL AS gravite,
               date_diff('day', r.recu_le, {AS_OF}) AS jours,
               json_object('A', json_object('table', 'purchase.order', 'id', c.odoo_id, 'total', c.total_devise, 'devise', c.devise),
                           'C', json_object('reception_le', r.recu_le)) AS detail
        FROM fact_commande c
        JOIN (SELECT po_ref, min(recu_le) AS recu_le FROM fact_reception GROUP BY po_ref) r USING (po_ref)
        WHERE r.recu_le < {AS_OF} - INTERVAL {DELAI_JOURS} DAY
          AND NOT EXISTS (SELECT 1 FROM fact_facture f WHERE f.po_ref = c.po_ref)
        """,
        "Commande {po_ref} reçue il y a {jours} jours, aucune facture en comptabilité",
        "Demander la facture au fournisseur, ou provisionner la charge (B).",
    ),
    Regle(
        "facture_en_double",
        "Facture saisie deux fois",
        "haute", "Comptabilité fournisseurs",
        """
        SELECT po_ref AS cle, po_ref, any_value(partner_key_compta) AS partner_key,
               round(sum(ht_eur) - min(ht_eur), 2) AS montant_eur, NULL AS gravite,
               piece_ref, count(*) AS nb,
               json_object('B', json_object('journal', 'HA', 'ecritures', list(ecriture_num ORDER BY ecriture_num),
                                             'lignes_fichier', list(ligne_fichier ORDER BY ecriture_num))) AS detail
        FROM fact_facture
        WHERE po_ref IS NOT NULL
        GROUP BY po_ref, piece_ref
        HAVING count(*) > 1
        """,
        "La facture {piece_ref} de la commande {po_ref} est saisie {nb} fois",
        "Contre-passer l'écriture en double dans la comptabilité (B) avant tout règlement.",
    ),
    Regle(
        "facture_avant_commande",
        "Facture antérieure à la commande",
        "basse", "Achats",
        """
        SELECT c.po_ref AS cle, c.po_ref, c.partner_key, NULL::DOUBLE AS montant_eur, NULL AS gravite,
               f.piece_ref, date_diff('day', f.date_facture, c.date_commande) AS jours,
               json_object('A', json_object('table', 'purchase.order', 'id', c.odoo_id, 'date', c.date_commande),
                           'B', json_object('ecriture', f.ecriture_num, 'ligne_fichier', f.ligne_fichier, 'date', f.date_facture)) AS detail
        FROM fact_commande c JOIN fact_facture f ON f.po_ref = c.po_ref AND f.rang = 1
        WHERE f.date_facture < c.date_commande
        """,
        "La facture {piece_ref} est datée {jours} jours avant la commande {po_ref}",
        "Régulariser : la commande a été passée après coup (A) ou la date de la facture est fausse (B).",
    ),
    Regle(
        "ecart_montant",
        "Montant facturé différent du montant commandé",
        "moyenne", "Achats",
        f"""
        SELECT c.po_ref AS cle, c.po_ref, c.partner_key,
               round(abs(f.ht_devise - c.total_devise) * d.taux_eur, 2) AS montant_eur, NULL AS gravite,
               f.piece_ref, c.total_devise, f.ht_devise, c.devise,
               round(100 * (f.ht_devise - c.total_devise) / c.total_devise, 1) AS ecart_pct,
               json_object('A', json_object('table', 'purchase.order', 'id', c.odoo_id, 'total', c.total_devise, 'devise', c.devise),
                           'B', json_object('ecriture', f.ecriture_num, 'ligne_fichier', f.ligne_fichier, 'ht', f.ht_devise, 'devise', f.devise)) AS detail
        FROM fact_commande c
        JOIN fact_facture f ON f.po_ref = c.po_ref AND f.rang = 1
        JOIN dim_devise d ON d.devise = c.devise
        WHERE f.devise = c.devise
          AND abs(f.ht_devise - c.total_devise) > {TOLERANCE_MONTANT} * c.total_devise
        """,
        "Commande {po_ref} : {total_devise} {devise} commandés, {ht_devise} {devise} facturés ({ecart_pct:+} %)",
        "Comparer le prix de la facture (B) au prix de la commande (A) et faire valider l'écart par les achats.",
    ),
    Regle(
        "devise_incoherente",
        "Devise de la facture différente de celle de la commande",
        "haute", "Comptabilité fournisseurs",
        """
        SELECT c.po_ref AS cle, c.po_ref, c.partner_key,
               round(f.ht_devise * dd.taux_eur, 2) AS montant_eur, NULL AS gravite,
               f.piece_ref, c.devise AS devise_commande, f.devise AS devise_facture, f.ht_devise, c.total_devise,
               json_object('A', json_object('table', 'purchase.order', 'id', c.odoo_id, 'devise', c.devise, 'total', c.total_devise),
                           'B', json_object('ecriture', f.ecriture_num, 'ligne_fichier', f.ligne_fichier, 'devise', f.devise, 'ht', f.ht_devise)) AS detail
        FROM fact_commande c
        JOIN fact_facture f ON f.po_ref = c.po_ref AND f.rang = 1
        JOIN dim_devise dd ON dd.devise = f.devise
        WHERE f.devise <> c.devise
        """,
        "Commande {po_ref} en {devise_commande}, facture {piece_ref} en {devise_facture} ({ht_devise} pour {total_devise} commandés)",
        "Vérifier la devise de la facture d'origine et ressaisir l'écriture avec la bonne devise (B).",
    ),
    Regle(
        "unite_incoherente",
        "Quantité reçue dans une autre unité que la quantité commandée",
        "haute", "Logistique",
        f"""
        WITH cmd AS (
            SELECT po_ref, sku, sum(qty_base) AS qty_cmd, sum(qty_ligne) AS qty_ligne, max(facteur_base) AS facteur,
                   max(unite) AS unite, avg(prix_unitaire) AS prix_unitaire, min(odoo_ligne_id) AS ligne_id
            FROM fact_commande_ligne GROUP BY po_ref, sku
        ), rec AS (
            SELECT po_ref, sku, sum(qty_base) AS qty_recue, string_agg(DISTINCT receipt_ref, ',') AS receptions
            FROM fact_reception_ligne GROUP BY po_ref, sku
        )
        SELECT cmd.po_ref AS cle, cmd.po_ref, c.partner_key,
               round(abs(cmd.qty_cmd - rec.qty_recue) * cmd.prix_unitaire / cmd.facteur * d.taux_eur, 2) AS montant_eur,
               NULL AS gravite, cmd.sku, cmd.qty_ligne, cmd.unite, rec.qty_recue, cmd.qty_cmd, rec.receptions,
               json_object('A', json_object('table', 'purchase.order.line', 'id', cmd.ligne_id, 'sku', cmd.sku, 'qty', cmd.qty_ligne, 'unite', cmd.unite),
                           'C', json_object('receptions', rec.receptions, 'qty_recue_pieces', rec.qty_recue)) AS detail
        FROM cmd JOIN rec USING (po_ref, sku)
        JOIN fact_commande c USING (po_ref)
        JOIN dim_devise d ON d.devise = c.devise
        WHERE cmd.facteur > 1
          AND abs(cmd.qty_cmd - rec.qty_recue) > {TOLERANCE_QUANTITE} * cmd.qty_cmd
          AND abs(rec.qty_recue - cmd.qty_ligne) < 0.01
        """,
        "{sku} sur {po_ref} : {qty_ligne:g} {unite} commandées ({qty_cmd:g} pièces), {qty_recue:g} pièces reçues",
        "La réception a été saisie en pièces avec le nombre de douzaines : corriger la quantité dans l'entrepôt (C).",
    ),
    Regle(
        "ecart_quantite",
        "Quantité reçue différente de la quantité commandée",
        "moyenne", "Logistique",
        f"""
        WITH cmd AS (
            SELECT po_ref, sku, sum(qty_base) AS qty_cmd, sum(qty_ligne) AS qty_ligne, max(facteur_base) AS facteur,
                   avg(prix_unitaire) AS prix_unitaire, min(odoo_ligne_id) AS ligne_id
            FROM fact_commande_ligne GROUP BY po_ref, sku
        ), rec AS (
            SELECT po_ref, sku, sum(qty_base) AS qty_recue, string_agg(DISTINCT receipt_ref, ',') AS receptions
            FROM fact_reception_ligne GROUP BY po_ref, sku
        )
        SELECT cmd.po_ref AS cle, cmd.po_ref, c.partner_key,
               round(abs(cmd.qty_cmd - rec.qty_recue) * cmd.prix_unitaire / cmd.facteur * d.taux_eur, 2) AS montant_eur,
               NULL AS gravite, cmd.sku, rec.qty_recue, cmd.qty_cmd, rec.receptions,
               round(100 * rec.qty_recue / cmd.qty_cmd, 0) AS taux_pct,
               json_object('A', json_object('table', 'purchase.order.line', 'id', cmd.ligne_id, 'sku', cmd.sku, 'qty_pieces', cmd.qty_cmd),
                           'C', json_object('receptions', rec.receptions, 'qty_recue_pieces', rec.qty_recue)) AS detail
        FROM cmd JOIN rec USING (po_ref, sku)
        JOIN fact_commande c USING (po_ref)
        JOIN dim_devise d ON d.devise = c.devise
        WHERE abs(cmd.qty_cmd - rec.qty_recue) > {TOLERANCE_QUANTITE} * cmd.qty_cmd
          AND NOT (cmd.facteur > 1 AND abs(rec.qty_recue - cmd.qty_ligne) < 0.01)
        """,
        "{sku} sur {po_ref} : {qty_recue:g} pièces reçues sur {qty_cmd:g} commandées ({taux_pct:g} %)",
        "Livraison partielle : attendre le solde ou solder la ligne dans Odoo (A) ; ne payer que ce qui est reçu.",
    ),
    Regle(
        "facture_mauvais_fournisseur",
        "Facture inscrite sur le compte d'un autre fournisseur",
        "haute", "Comptabilité fournisseurs",
        """
        SELECT c.po_ref AS cle, c.po_ref, c.partner_key,
               round(f.ht_eur, 2) AS montant_eur, NULL AS gravite,
               f.piece_ref, f.aux_num, c.code_odoo,
               json_object('A', json_object('table', 'purchase.order', 'id', c.odoo_id, 'fournisseur', c.code_odoo),
                           'B', json_object('ecriture', f.ecriture_num, 'ligne_fichier', f.ligne_fichier, 'compte_auxiliaire', f.aux_num)) AS detail
        FROM fact_commande c JOIN fact_facture f ON f.po_ref = c.po_ref AND f.rang = 1
        WHERE f.partner_key_compta IS NOT NULL AND f.partner_key_compta <> c.partner_key
        """,
        "La facture {piece_ref} de la commande {po_ref} ({code_odoo}) est inscrite sur le compte {aux_num}",
        "Ré-affecter l'écriture au bon compte fournisseur (B) avant paiement : le règlement partirait chez le mauvais tiers.",
    ),
    Regle(
        "partenaire_ambigu",
        "Compte auxiliaire dont le nom convient à plusieurs fournisseurs",
        "basse", "Données de base",
        """
        SELECT pb.code AS cle, NULL AS po_ref, NULL AS partner_key, NULL::DOUBLE AS montant_eur, NULL AS gravite,
               pb.code AS aux_num, pb.nom AS aux_lib,
               (SELECT count(DISTINCT partner_key) FROM pont_partenaire a
                 WHERE a.systeme = 'A' AND norm(a.nom) = norm(pb.nom)) AS nb_candidats,
               json_object('B', json_object('compte_auxiliaire', pb.code, 'libelle', pb.nom)) AS detail
        FROM pont_partenaire pb WHERE pb.systeme = 'B' AND pb.methode = 'ambigu'
        """,
        "Le compte auxiliaire {aux_num} (« {aux_lib} ») convient à {nb_candidats} fournisseurs",
        "Choisir le bon fournisseur et renommer le compte auxiliaire en comptabilité (B), pour qu'il porte un code connu d'Odoo.",
    ),
    Regle(
        "partenaire_double",
        "Fournisseur présent deux fois dans Odoo",
        "basse", "Données de base",
        """
        SELECT replace(pk.partner_key, 'TVA:', '') AS cle, NULL AS po_ref, pk.partner_key, NULL::DOUBLE AS montant_eur, NULL AS gravite,
               replace(pk.partner_key, 'TVA:', '') AS tva, list(pk.code ORDER BY pk.code) AS codes,
               count(*) AS nb,
               json_object('A', json_object('table', 'res.partner', 'codes', list(pk.code ORDER BY pk.code))) AS detail
        FROM pont_partenaire pk
        WHERE pk.systeme = 'A'
        GROUP BY pk.partner_key
        HAVING count(*) > 1
        """,
        "Le numéro de TVA {tva} est porté par {nb} fiches Odoo : {codes}",
        "Fusionner les fiches dans Odoo (A). Le rapprochement les traite déjà comme un seul fournisseur.",
    ),
    Regle(
        "ecriture_desequilibree",
        "Écriture comptable déséquilibrée",
        "haute", "Comptabilité fournisseurs",
        """
        SELECT po_ref AS cle, po_ref, partner_key_compta AS partner_key, abs(desequilibre) AS montant_eur, NULL AS gravite,
               ecriture_num, desequilibre,
               json_object('B', json_object('ecriture', ecriture_num, 'ligne_fichier', ligne_fichier, 'desequilibre', desequilibre)) AS detail
        FROM fact_facture WHERE abs(desequilibre) > 0.01
        """,
        "L'écriture {ecriture_num} n'est pas équilibrée (débit moins crédit : {desequilibre})",
        "Rejeter le fichier ou corriger l'écriture à la source (B) : un FEC déséquilibré est refusé au contrôle fiscal.",
    ),
]

PAR_NOM = {r.nom: r for r in REGLES}
