# Disney P&L Sankey

An interactive Sankey diagram of The Walt Disney Company's income statement, segment results, business-line profitability and cash allocation for fiscal 2023–2025, built **directly from the Form 10-K PDFs** — and rebuilt, re-verified and redeployed automatically every time a new filing is dropped in.

Live site: `https://harrypaik100.github.io/disney-pnl-sankey/`

- **Eight views** — by segment, by business line, segment operating income, line profitability, cash & investment, and a P&L drill-down for each segment; a year scrubber animates FY2023 → FY2025 on one dollar scale; an FY2027 toggle shows the Consumer Products licensing move.
- **Every figure is sourced** — click any block to open the page of that year's 10-K with the number highlighted. Capex blocks also list the ships and park projects the report names, each linked to its sentence.
- **Nothing is typed in** — every number on the page is extracted from the filings by `tools/extract.py`, derived by explicit formulas in `tools/assemble.py`, and checked by `tools/verify.py` before `tools/build.py` is allowed to render the page.

## The pipeline

```
filings/*.pdf ──extract.py──▶ extracted/*.json ──assemble.py──▶ data/values.json + data/sources.json
                                                                        │
                      verify.py (re-read every highlight, 60+ tie-outs) ─┤── fails → no site
                      xbrl.py   (SEC companyfacts cross-check)          ─┤
                                                                        ▼
                                              build.py ──▶ site/ ──verify_site.js──▶ deploy
```

| step | what it does |
|---|---|
| `tools/extract.py` | Opens each `filings/<year>-10-K.pdf` with pdfplumber and pulls ~120 line items from the income statement, cash flow statement, statement of equity, segment note, revenue note and MD&A tables, with the page and the exact rectangle of every number. Tables are found by their wording, so a page shift in a new filing doesn't matter. Fails if a required line is missing. |
| `tools/assemble.py` | Turns the extracted lines into the 136 blocks the diagram draws, each defined as a signed sum of filing lines (`NODE_TERMS`). Each fiscal year comes from its own 10-K; later 10-Ks' comparative columns become alternate sources and are compared. Locates the editorial "behind this figure" items (`data/details.json`) in the filing text. |
| `tools/verify.py` | **The gate.** Re-reads every highlighted rectangle from the PDF and checks it prints the number the site uses; checks each block is the sum of its highlighted lines; runs the accounting identities across all views (revenue lines → segments → consolidated statement → net income, segment P&Ls, segment-OI reconciliation, profit pools, cash sources = uses, capex by segment, retained-earnings and treasury-stock roll-forwards, cash-flow and income-statement footings); and fails on any figure that differs between filings unless `data/restatements.json` records why. Writes `data/verify-report.md`. |
| `tools/xbrl.py` | Fetches Disney's XBRL facts from the SEC (`companyfacts`, CIK 1744489) and compares the consolidated figures and EPS. Writes `data/xbrl-report.md`. Segment figures carry XBRL dimensions the API doesn't expose, so they are verified against the filing text instead. |
| `tools/build.py` | Renders `site/index.html` from `template/index.html`: injects the data and source map, generates the table view, renders the referenced filing pages to `site/pages/` with `pdftoppm`. Refuses to build if the template draws a block the data lacks, or the data has a non-zero figure the template can't draw (e.g. a business line that newly turns loss-making). |
| `tools/verify_site.js` | Evaluates the built page's own data and value functions and checks that every block's inflows = outflows = value in every view, every year, as reported and pro forma, and that every drawn figure has a source page image. |

`.github/workflows/verify-and-deploy.yml` runs all of this on every push to `main`; the Pages deploy job only runs if the verify job passed, so **the site cannot deploy unless everything ties**.

## When Disney files

1. Drop the new 10-K into `filings/` as `<fiscal year>-10-K.pdf` (e.g. `2026-10-K.pdf`).
2. Run `make` (needs Python 3 with `pdfplumber`, `poppler-utils`, and Node). The extractor reports any line it can't find; if Disney relabelled a line, add the new wording as an alternative in `SPEC` in `tools/extract.py`.
3. Read `data/verify-report.md`. If the new filing restates a prior-year line, the verifier names it; record the reason in `data/restatements.json` (the site keeps each year as originally reported).
4. Update the editorial text — `template/notes.html` (reading notes), the `YEAR_NOTE` and `note:` strings in `template/index.html`, and `data/details.json` (projects named in the new filing, located by quoting a phrase from the report; the assembler fails if a phrase isn't found).
5. `make publish`, commit, push. GitHub Actions re-runs the whole chain and deploys only on a clean pass.

`make xbrl` runs the SEC cross-check locally (set `SEC_USER_AGENT="disney-pnl-sankey (you@example.com)"` — the SEC asks for a contact address). In GitHub, set a repository variable `SEC_USER_AGENT` to make the XBRL check a hard gate; without it the check still runs and still fails on mismatches, but a blocked fetch is a warning rather than a failure.

### GitHub Pages setting

The workflow deploys with `actions/deploy-pages`, so the repository's Pages source must be **GitHub Actions** (Settings → Pages → Build and deployment → Source). With the older "Deploy from a branch" setting the root `index.html` is served directly and nothing gates it — the CI job still fails loudly, but the broken page would already be live.

## Layout

```
filings/      the 10-K PDFs (input)
tools/        extract.py · assemble.py · verify.py · xbrl.py · build.py · verify_site.js
template/     index.html (page, with D('id') placeholders for every figure) · notes.html (editorial)
data/         details.json, restatements.json (hand-maintained) · values.json, sources.json, consistency.json,
              verify-report.md, xbrl-report.md, xbrl-cache.json (generated)
extracted/    one JSON per filing: every line found, with page and rectangle (generated)
site/         the built site (generated, not committed; what GitHub Actions deploys)
index.html    the committed copy of the verified build (`make publish`); pages/ holds the rendered filing pages
```

Figures are as reported, in $ millions. Gross profit and operating income are derived (Disney does not report those subtotals). The FY2027 structure is a pro forma: FY2025 figures rearranged as the Q3 FY2026 8-K describes, not a forecast.
