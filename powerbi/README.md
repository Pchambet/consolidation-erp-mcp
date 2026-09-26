# Power BI, ou Tableau : le tableau de bord sur le modèle consolidé

**Ce dossier n'a jamais été ouvert dans Power BI Desktop.** La maquette a été développée sur un Mac, et
Power BI Desktop n'existe que sous Windows. Ce que contient ce dossier est prêt à être chargé, mais
**non vérifié** : le modèle, les relations et les mesures DAX ci-dessous sont écrits d'après la
documentation de Power BI, pas d'après un essai. La page HTML `../tableau_de_bord/index.html` montre les
mêmes indicateurs, et sert de référence pour vérifier les chiffres.

## Ce que l'entrepôt fournit

`consolidation exporter-bi` écrit un CSV par table dans `donnees/` : UTF-8 avec BOM, virgule comme
séparateur, point comme séparateur décimal, dates en AAAA-MM-JJ. Les CSV du dépôt correspondent à la
date d'arrêté du 26/09/2026.

| Table | Rôle | Clé |
|---|---|---|
| `dim_partenaire` | un fournisseur = un numéro de TVA | `partner_key` |
| `dim_produit`, `dim_date`, `dim_devise` | dimensions | `sku`, `date`, `devise` |
| `fact_commande` | commandes d'Odoo, avec `nb_ecarts` précalculé | `po_ref` |
| `fact_commande_ligne` | lignes de commande, quantité en unité de la ligne et en pièces | `odoo_ligne_id` |
| `fact_reception`, `fact_reception_ligne` | réceptions de l'entrepôt | `receipt_ref` |
| `fact_facture` | factures du journal des achats (FEC) | `ecriture_num` |
| `rapprochement` | une ligne par commande : reçue ? facturée ? | `po_ref` |
| `ecarts` | les écarts détectés, avec `cite_odoo`, `cite_compta`, `cite_entrepot` | `ecart_id` |
| `pont_partenaire` | le code de chaque système et le fournisseur auquel il est rattaché | `systeme, code` |
| `parametres` | la date d'arrêté des contrôles | |

## Étapes dans Power BI Desktop

1. **Obtenir des données, Dossier**, choisir `donnees/`, puis transformer : typer les dates
   (`date_commande`, `date_facture`, `premiere_reception`, `date`, `as_of`, `premiere_detection`).
2. **Relations** (plusieurs à un, filtrage dans un seul sens) :

   | De | Vers |
   |---|---|
   | `fact_commande[partner_key]` | `dim_partenaire[partner_key]` |
   | `fact_commande[date_commande]` | `dim_date[date]` |
   | `fact_commande_ligne[po_ref]` | `fact_commande[po_ref]` |
   | `fact_commande_ligne[sku]` | `dim_produit[sku]` |
   | `rapprochement[po_ref]` | `fact_commande[po_ref]` (un à un) |
   | `ecarts[po_ref]` | `fact_commande[po_ref]` |
   | `ecarts[partner_key]` | `dim_partenaire[partner_key]` |

   `po_ref` est vide pour les écarts qui portent sur un fournisseur (deux règles) : c'est voulu.
3. **Mesures** : coller `mesures.dax`, une mesure à la fois.
4. **Pages** proposées : *Vue d'ensemble* (tuiles et écarts par contrôle), *Écarts à traiter* (la table
   des écarts, filtrable par gravité et responsable), *Qualité par source* (les trois drapeaux `cite_*`).

## Vérifier les mesures

Chaque mesure doit rendre la valeur de l'indicateur correspondant dans l'entrepôt
(`uv run consolidation indicateurs`, date d'arrêté du 26/09/2026) :

| Mesure | Valeur attendue |
|---|---|
| Écarts ouverts | 22 |
| Écarts de gravité haute | 8 |
| Montant exposé (EUR) | 12 007 |
| Taux de conformité | 81,8 % |
| Reçu non facturé (EUR) | 4 495 |
| Délai moyen commande à facture | 17,4 jours |
| Fournisseurs à fiches multiples | 2 |
| Comptes auxiliaires rattachés | 97,5 % |

Si une mesure donne un autre chiffre, c'est elle qui est fausse.

## Voir le rafraîchissement

```bash
uv run consolidation ecarts --regle commande_sans_facture     # noter un identifiant
uv run consolidation corriger <identifiant>                   # saisit la facture manquante dans le FEC
uv run consolidation actualiser && uv run consolidation exporter-bi
```

Puis **Actualiser** dans Power BI : les écarts passent de 22 à 21, le taux de conformité monte, le
« reçu non facturé » baisse. C'est ce que vérifie `tests/test_actualisation.py` côté entrepôt.

## Tableau au lieu de Power BI

Les mêmes CSV se chargent dans Tableau Desktop ou Tableau Public (Connexion, Fichier texte). Les relations
se dessinent dans l'onglet *Modèle de données* avec les mêmes clés. Les mesures s'écrivent en champs
calculés ; le fichier `mesures.dax` en donne la définition métier.
