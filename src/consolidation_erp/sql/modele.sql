-- Modèle consolidé : des tables brutes (stg_*) vers un modèle en étoile.
-- Chaque table dit d'où viennent ses lignes : identifiant Odoo, numéro de ligne du FEC, référence de
-- réception de l'entrepôt. Rien n'est calculé à la main : tout se refait à chaque actualisation.

-- Un nom sans accents, sans ponctuation, sans forme juridique : la clé d'un rapprochement par le nom.
CREATE MACRO norm(x) AS
    regexp_replace(regexp_replace(upper(strip_accents(x)), '[.,]', '', 'g'), '\s+(SARL|SAS|SA|AG)$', '');

-- ------------------------------------------------------------------------------------------ partenaires
-- Un fournisseur = un numéro de TVA. Odoo et l'entrepôt le connaissent chacun sous leur code.
CREATE TABLE pont_partenaire AS
WITH a AS (
    SELECT 'A' AS systeme, ref AS code, name AS nom,
           COALESCE('TVA:' || vat, 'NOM:' || norm(name)) AS partner_key,
           CASE WHEN vat IS NULL THEN 'nom' ELSE 'tva' END AS methode,
           CASE WHEN vat IS NULL THEN 0.7 ELSE 1.0 END AS confiance
    FROM stg_a_partner WHERE ref IS NOT NULL
), c AS (
    SELECT 'C' AS systeme, code, name AS nom,
           COALESCE('TVA:' || vat, 'NOM:' || norm(name)) AS partner_key,
           CASE WHEN vat IS NULL THEN 'nom' ELSE 'tva' END AS methode,
           CASE WHEN vat IS NULL THEN 0.7 ELSE 1.0 END AS confiance
    FROM stg_c_supplier
),
-- La comptabilité ne connaît que ses comptes auxiliaires. On les rattache par le code s'il existe
-- côté Odoo, sinon par le nom ; un nom qui convient à deux fournisseurs reste non rattaché.
aux AS (SELECT DISTINCT aux_num, aux_lib FROM stg_b_ecriture WHERE aux_num IS NOT NULL),
par_code AS (
    SELECT aux.aux_num, aux.aux_lib, a.partner_key FROM aux JOIN a ON a.code = aux.aux_num
),
par_nom AS (
    SELECT aux.aux_num, aux.aux_lib, list(DISTINCT a.partner_key) AS candidats
    FROM aux
    JOIN a ON norm(a.nom) = norm(aux.aux_lib)
    WHERE aux.aux_num NOT IN (SELECT aux_num FROM par_code)
    GROUP BY aux.aux_num, aux.aux_lib
),
b AS (
    SELECT 'B' AS systeme, aux_num AS code, aux_lib AS nom, partner_key, 'code' AS methode, 1.0 AS confiance
    FROM par_code
    UNION ALL
    SELECT 'B', aux_num, aux_lib,
           CASE WHEN len(candidats) = 1 THEN candidats[1] END,
           CASE WHEN len(candidats) = 1 THEN 'nom' ELSE 'ambigu' END,
           CASE WHEN len(candidats) = 1 THEN 0.8 ELSE 0.0 END
    FROM par_nom
    UNION ALL
    SELECT 'B', aux_num, aux_lib, NULL, 'inconnu', 0.0
    FROM aux
    WHERE aux_num NOT IN (SELECT aux_num FROM par_code) AND aux_num NOT IN (SELECT aux_num FROM par_nom)
)
SELECT * FROM a UNION ALL SELECT * FROM c UNION ALL SELECT * FROM b;

CREATE TABLE dim_partenaire AS
SELECT pk.partner_key,
       any_value(CASE WHEN pk.systeme = 'A' THEN pk.nom END) AS nom,
       replace(pk.partner_key, 'TVA:', '') AS identifiant,
       any_value(s.country) AS pays,
       count(DISTINCT CASE WHEN pk.systeme = 'A' THEN pk.code END) AS fiches_odoo
FROM pont_partenaire pk
LEFT JOIN stg_c_supplier s ON s.code = pk.code AND pk.systeme = 'C'
WHERE pk.partner_key IS NOT NULL
GROUP BY pk.partner_key;

CREATE TABLE dim_produit AS
SELECT p.sku, p.name AS nom, p.uom_achat, i.base_unit AS unite_base
FROM stg_a_product p LEFT JOIN stg_c_item i USING (sku);

CREATE TABLE dim_date AS
SELECT d::DATE AS date, year(d) AS annee, month(d) AS mois, strftime(d, '%Y-%m') AS annee_mois,
       'S' || lpad(weekofyear(d)::VARCHAR, 2, '0') AS semaine
FROM generate_series(DATE '2026-01-01', DATE '2026-12-31', INTERVAL 1 DAY) t(d);

-- dim_devise et parametres sont créées avant ce script (taux fixe de la maquette, date d'arrêté).

-- ------------------------------------------------------------------------------------------ commandes
CREATE TABLE fact_commande AS
SELECT o.name AS po_ref, o.id AS odoo_id, pk.partner_key, p.ref AS code_odoo,
       o.date_order::DATE AS date_commande, o.currency AS devise,
       o.amount_untaxed AS total_devise,
       round(o.amount_untaxed * d.taux_eur, 2) AS total_eur,
       o.state
FROM stg_a_order o
JOIN stg_a_partner p ON p.id = o.partner_id
LEFT JOIN pont_partenaire pk ON pk.systeme = 'A' AND pk.code = p.ref
LEFT JOIN dim_devise d ON d.devise = o.currency;

CREATE TABLE fact_commande_ligne AS
SELECT o.name AS po_ref, l.id AS odoo_ligne_id, l.sku, l.qty AS qty_ligne, l.uom AS unite,
       l.facteur_base, l.qty * l.facteur_base AS qty_base,
       l.price_unit AS prix_unitaire, round(l.qty * l.price_unit, 2) AS montant_devise
FROM stg_a_order_line l JOIN stg_a_order o ON o.id = l.order_id;

-- ------------------------------------------------------------------------------------------ réceptions
CREATE TABLE fact_reception AS
SELECT r.receipt_ref, r.po_ref, r.received_at AS recu_le, r.supplier_code, pk.partner_key
FROM stg_c_receipt r
LEFT JOIN pont_partenaire pk ON pk.systeme = 'C' AND pk.code = r.supplier_code;

CREATE TABLE fact_reception_ligne AS
SELECT l.receipt_ref, r.po_ref, l.sku, l.qty AS qty_base
FROM stg_c_receipt_line l JOIN stg_c_receipt r USING (receipt_ref);

-- ------------------------------------------------------------------------------------------ factures
-- Une facture = une écriture du journal des achats. La commande citée est lue dans le libellé, comme
-- le fait un comptable ; le montant hors taxes est celui de la ligne de charge.
CREATE TABLE fact_facture AS
WITH ecriture AS (
    SELECT ecriture_num,
           min(date) AS date_facture,
           any_value(piece_ref) AS piece_ref,
           NULLIF(regexp_extract(any_value(libelle), '\bP[0-9]{5}\b', 0), '') AS po_ref,
           sum(CASE WHEN compte LIKE '6%' THEN debit END) AS ht_eur,
           any_value(CASE WHEN compte LIKE '6%' THEN idevise END) AS idevise,
           sum(CASE WHEN compte LIKE '6%' THEN montant_devise END) AS ht_montant_devise,
           any_value(CASE WHEN compte LIKE '401%' THEN aux_num END) AS aux_num,
           any_value(CASE WHEN compte LIKE '401%' THEN aux_lib END) AS aux_lib,
           min(ligne_fichier) AS ligne_fichier,
           round(sum(debit) - sum(credit), 2) AS desequilibre
    FROM stg_b_ecriture
    WHERE journal = 'HA'
    GROUP BY ecriture_num
)
SELECT e.ecriture_num, e.piece_ref, e.po_ref, e.date_facture,
       e.aux_num, e.aux_lib, pb.partner_key AS partner_key_compta, pb.methode AS methode_rattachement,
       COALESCE(e.idevise, 'EUR') AS devise,
       CASE WHEN e.idevise IS NULL THEN e.ht_eur ELSE e.ht_montant_devise END AS ht_devise,
       e.ht_eur, e.ligne_fichier, e.desequilibre,
       row_number() OVER (PARTITION BY e.po_ref, e.piece_ref ORDER BY e.ecriture_num) AS rang
FROM ecriture e
LEFT JOIN pont_partenaire pb ON pb.systeme = 'B' AND pb.code = e.aux_num;

-- ------------------------------------------------------------------------------------------ rapprochement
-- Le rapprochement en trois voies : commande (Odoo), réception (entrepôt), facture (comptabilité).
CREATE VIEW v_rapprochement AS
SELECT c.po_ref, c.partner_key, c.date_commande, c.devise, c.total_devise, c.total_eur,
       r.premiere_reception, f.date_facture, f.ht_devise AS facture_ht_devise, f.devise AS facture_devise,
       r.po_ref IS NOT NULL AS recue, f.po_ref IS NOT NULL AS facturee
FROM fact_commande c
LEFT JOIN (SELECT po_ref, min(recu_le) AS premiere_reception FROM fact_reception GROUP BY po_ref) r USING (po_ref)
LEFT JOIN (SELECT * FROM fact_facture WHERE rang = 1) f USING (po_ref);
