# consolidation-erp-mcp

Three systems describe the same purchases. Do they agree? This project consolidates an Odoo 17
instance, an accounting ledger export and a warehouse API into one DuckDB star schema, runs a
three-way match (order, receipt, invoice) as SQL controls, and lets an LLM agent query the result
over MCP, reading discrepancies from deterministic SQL controls instead of deciding them itself.

[![CI](https://github.com/Pchambet/consolidation-erp-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Pchambet/consolidation-erp-mcp/actions/workflows/ci.yml)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-0d9488)
[![License: MIT](https://img.shields.io/badge/license-MIT-64748b)](LICENSE)
· [Version française](README.fr.md)

![Three-way match on the 99 purchase orders older than 30 days (97 with a receipt, 95 with an invoice, 81 passing every control), and the 22 planted discrepancies all detected with none invented](docs/figures/hero.png)

*Drawn by [`scripts/hero_figure.py`](scripts/hero_figure.py) from the warehouse and the committed ground truth.
The generated control dashboard (French UI) is [shown in full below](#control-dashboard).*

## TL;DR

- **22 known discrepancies are planted** in an otherwise consistent synthetic world, across 12 of the
  13 controls (the 13th, unbalanced journal entry, has its own test). The SQL controls recover **all 22
  and nothing else**, and a clean world produces **zero** (`tests/test_controles.py`).
- **Three-way match:** of the 99 purchase orders older than 30 days, 97 have a receipt, 95 an invoice,
  and **81 (81.8 %) pass every control end to end**. 8 discrepancies are high severity; 4,495 EUR of
  goods were received more than 30 days ago and never invoiced.
- **Free-form SQL from the agent is bounded by three independent guards** (read-only connection without
  file or network access, a single `SELECT`, a 10 s timeout) plus a 1,000-row cap; each guard has its
  own test.
- **The MCP server is exercised by a real MCP client**, in memory and as a stdio subprocess launched
  with a minimal environment (only the variables the MCP client passes by default).
- **69 tests pass without Odoo**, in CI on every push to `main`; 3 more run against a live Odoo 17
  instance and are skipped when it does not answer.

## Why it matters

Multi-entity groups close their books by reconciling systems that were never designed to agree: the
purchasing ERP, the general ledger and the warehouse. Most of the effort goes into finding *which*
record is wrong and *who* owns the fix. Putting an LLM on top is only useful if it does not invent
discrepancies and points back to the source record. Here the model never decides what is wrong:
deterministic, tested SQL controls do. The agent reads their output and is instructed to cite the
system and the record identifier for every claim.

## Approach

```
 A  Odoo 17 (purchasing)       XML-RPC ─┐
 B  accounting (FEC export)    file    ─┼─► DuckDB warehouse ─┬─► MCP server (6 tools) ─► Claude
 C  warehouse logistics        REST API ┘   (star schema,     │
                                             SQL controls)    └─► HTML dashboard, CSV for Power BI
```

1. **Read** each system with its own connector (`odoo.py`, `fec.py`, `erp_c.py`), handling the traps of
   each: Odoo returns `False` instead of raising on a bad password and stores quantities in the line's
   unit of measure (twenty dozens are not twenty pieces); the FEC is tab-separated, cp1252, with a
   decimal comma; the warehouse API is paginated, keyed and retried.
2. **Model** raw data into a star schema in SQL (`sql/modele.sql`). Suppliers are matched across systems
   by VAT number, so a supplier with two Odoo records is still one supplier.
3. **Control** with one SQL query per rule (`regles.py`). Each discrepancy carries a severity, an owning
   team, a recommended action and the identifiers of its source records in A, B and C (down to the FEC
   line number).
4. **Serve** the result: an MCP server for an LLM agent, a self-contained HTML dashboard, and CSV + DAX
   for Power BI or Tableau. The refresh rebuilds the warehouse atomically and is idempotent, with stable
   discrepancy identifiers across rebuilds.

### What is real and what is simulated

| System | Nature |
|---|---|
| **A, Odoo 17** | **A real Odoo 17.0** in Docker with the purchasing module, read over XML-RPC. |
| **B, accounting** | A file in the **real FEC format** (French statutory ledger export: 18 columns, tabs, decimal comma, cp1252). Written by this project, not by accounting software. |
| **C, warehouse** | A **simulated REST API** (API key, pagination, retries). There is no warehouse software behind it. |
| Data | Synthetic names, VAT numbers and amounts. Deterministic (fixed seed). |

Only one of the three systems is a real ERP. What this demonstrates is the method, not an
integration of three commercial ERPs.

## Results

### Every planted discrepancy is found, and nothing else

| Control | Severity | Owner | Planted |
|---|---|---|---|
| Invoice entered twice | high | Accounts payable | 2 |
| Invoice booked on another supplier's account | high | Accounts payable | 1 |
| Invoice currency differs from order currency | high | Accounts payable | 2 |
| Quantity received in another unit (dozens vs pieces) | high | Logistics | 2 |
| Old order with no receipt (high if already invoiced) | medium | Purchasing | 2 |
| Goods received over 30 days ago, never invoiced | medium | Accounts payable | 3 |
| Invoiced amount differs from ordered amount | medium | Purchasing | 2 |
| Received quantity differs from ordered quantity | medium | Logistics | 2 |
| Receipt referencing an unknown order | medium | Logistics | 1 |
| Invoice dated before its order | low | Purchasing | 2 |
| Supplier present twice in Odoo (same VAT) | low | Master data | 2 |
| Ledger sub-account whose name matches two suppliers | low | Master data | 1 |
| Unbalanced journal entry | high | Accounts payable | 0, tested separately |

Two cases show why supplier matching matters. Two suppliers have a second Odoo record with the same
VAT number while their invoices stay on the first record's account: without VAT-based deduplication,
each would be falsely flagged as "invoice on the wrong supplier". Conversely, a ledger account whose
name fits two different suppliers is **not attached at random**: it is flagged, and its invoice is left
unassigned.

### Indicators at the cut-off date (26 Sep 2026)

| Indicator | Value |
|---|---|
| Open discrepancies | 22 |
| High-severity discrepancies | 8 |
| Exposed amount (an amount can appear in several discrepancies) | 12,007 EUR |
| Orders older than 30 days clean end to end | 81.8 % |
| Received over 30 days ago, not invoiced | 4,495 EUR |
| Mean order-to-invoice delay | 17.4 days |
| Suppliers with several Odoo records | 2 |
| Ledger sub-accounts matched unambiguously | 97.5 % |

Reproduce with `uv run consolidation indicateurs`. The same eight values are recomputed from the Power
BI CSV export by a test.

### Control dashboard

`uv run consolidation tableau-de-bord` writes a self-contained HTML page (French UI, light and dark):
indicator tiles, discrepancies per control, the three-way match, which system to fix, which team must
act, and the full list. Its figures come from the same SQL indicators that the tests recompute from
the CSV export; the test on the page itself checks the open-discrepancy count and the 22 severity
markers. GitHub shows the committed copy, [`tableau_de_bord/index.html`](tableau_de_bord/index.html),
as source: [view it rendered](https://htmlpreview.github.io/?https://github.com/Pchambet/consolidation-erp-mcp/blob/main/tableau_de_bord/index.html)
through htmlpreview.github.io, or open the file locally.

<details><summary>Full-page screenshot (all 22 discrepancies)</summary>

![Control dashboard generated from the warehouse: 22 open discrepancies, 8 high severity, 81.8 % of orders older than 30 days clean end to end](docs/tableau_de_bord.png)

</details>

### The MCP server

Six tools. Five are read-only and read the warehouse, never the ERPs. Tool names are in French, as is
the rest of the code base.

| Tool | Purpose |
|---|---|
| `decrire_modele` | tables and columns; call before writing a query |
| `lister_indicateurs` | the 8 indicators, their definition, value and the sources read |
| `ecarts_ouverts` | open discrepancies, filterable by severity and control |
| `expliquer_ecart` | finding, rule, records in each system, owner, recommended action |
| `requete_lecture_seule` | one DuckDB `SELECT`, 10 s and 1,000 rows at most |
| `rafraichir` | re-reads A, B and C, rebuilds the warehouse, reports new and resolved discrepancies |

The server instructions require the agent to **cite the system and the identifier** of every record,
and to never assert a discrepancy that `ecarts_ouverts` does not list. Refusal messages from the SQL
guard are written to be read by the model, so it can correct its own query.

What the guard tests prove (`tests/test_garde_sql.py`): a keyword filter sits on top of the three
guards, and the read-only connection and the blocked file access are each tested *with that filter
bypassed*. The locked configuration, the timeout and the row cap have their own tests. Network access
is disabled by the same setting as file access but has no dedicated test.

Three questions to try, with the expected answer:

1. "How good is the consolidated data?" → 22 discrepancies, 8 high severity; 81.8 % of orders older
   than 30 days pass every control.
2. "What are the most serious discrepancies, and who must act?" → invoice FA26-0052 entered twice
   (2,728 EUR), to be reversed by accounts payable.
3. "Which suppliers exist under two codes in Odoo, and is it a problem?" → F0018 and F0902, F0022 and
   F0901; the matching treats each pair as one supplier.

Then, to see the refresh: fix one discrepancy at the source with `uv run consolidation corriger <id>`,
tell the agent, and it calls `rafraichir`: 22 discrepancies become 21.

## Reproduce

Requires [uv](https://docs.astral.sh/uv/). Docker only for the live Odoo. No download needed: the three
generated sources are committed in `data/`, and a refresh takes a few seconds.

```bash
uv sync
uv run consolidation actualiser       # read A, B, C and rebuild the warehouse: 22 discrepancies
uv run consolidation ecarts           # list them, most severe first
uv run consolidation expliquer <id>   # one discrepancy, with its source records in each system
uv run consolidation tableau-de-bord  # write tableau_de_bord/index.html
uv run pytest                         # 69 tests without Odoo
uv run --group figures python scripts/hero_figure.py   # redraw docs/figures/hero.png
```

Without Docker, Odoo is read from `data/odoo_instantane.json`, a snapshot exported from the real Odoo
and committed with the repository (`ODOO_MODE=instantane`, or automatically when Odoo does not answer;
the source actually read is always printed).

With the live Odoo:

```bash
./scripts/odoo_up.sh                  # start Odoo 17 and create the database (admin/admin, throwaway instance)
uv run consolidation generer          # seed Odoo, write the FEC, the warehouse dataset and the snapshot
uv run consolidation actualiser       # Odoo is now read live
```

### Connect Claude Desktop

In `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS), with absolute paths to
`uv` (`which uv`) and to this repository; Claude Desktop does not inherit your `PATH`.

```json
{
  "mcpServers": {
    "consolidation-erp": {
      "command": "/absolute/path/to/uv",
      "args": ["--directory", "/absolute/path/to/consolidation-erp-mcp", "run", "consolidation-mcp"]
    }
  }
}
```

In Claude Code:
`claude mcp add consolidation-erp -- /absolute/path/to/uv --directory /absolute/path/to/consolidation-erp-mcp run consolidation-mcp`.

## Repository layout

```
src/consolidation_erp/
  monde.py                     synthetic world and its planted discrepancies (ground truth)
  odoo.py, fec.py, erp_c.py    one reader per system
  sql/modele.sql               raw tables to star schema
  regles.py                    the controls, one SQL query each
  entrepot.py                  atomic build, indicators, bounded free-form query
  pipeline.py                  the refresh, shared by the CLI and the MCP server
  mcp_server.py                the MCP server
  demo.py                      generate the sources; fix a discrepancy at the source
  tableau_de_bord.py           HTML dashboard
  export_bi.py                 star-schema CSV export
data/                          generated sources (FEC, warehouse dataset, Odoo snapshot, ground truth)
powerbi/                       CSV, relationships and DAX measures (untested in Power BI, see below)
scripts/hero_figure.py         the figure at the top of this page
docs/                          figures and the dashboard screenshot
tests/                         69 tests offline, 3 more against a live Odoo
```

## Limitations and what was not verified

- **Claude Desktop UI:** the server is tested by an MCP client (in memory and as a stdio subprocess
  with a minimal environment: only the variables the MCP client passes by default), not yet from the
  Claude Desktop interface.
- **What the agent says is not tested.** Citing sources and not asserting unlisted discrepancies are
  instructions given to the model, not an enforced constraint. The tests cover the tools' outputs, not
  the model's answers.
- **Power BI and Tableau:** the [`powerbi/`](powerbi/README.md) folder (star-schema CSV, relationships,
  DAX measures; documented in French with an English summary) **has never been opened in Power BI
  Desktop** (developed on a Mac). A test recomputes the eight indicators from the CSV, which validates
  columns and logic, not DAX syntax.
- **Odoo:** a single instance, version 17.0, purchasing module only. No multi-company, no taxes, no
  Odoo receipts (the warehouse is system C).
- **Exchange rate** is fixed (1 CHF = 1.06 EUR). A real deployment would read daily rates.
- **The refresh rebuilds everything** instead of reading changes only. It is idempotent and takes a few
  seconds at this scale. A production connector would read incrementally on `write_date`; this one
  does not.
- **Synthetic data:** the planted discrepancies are the ones I thought of. Recovering them exactly
  proves the controls do what they claim, not that they cover every failure mode of a real close.

## References

- Odoo 17 external API (XML-RPC): https://www.odoo.com/documentation/17.0/developer/reference/external_api.html
- FEC (*fichier des écritures comptables*) format: article A47 A-1 of the French *Livre des procédures
  fiscales*.
- Model Context Protocol specification: https://modelcontextprotocol.io/
- DuckDB: https://duckdb.org/

---

Built by [Pierre Chambet](https://github.com/Pchambet) — decision science for operations under uncertainty.
