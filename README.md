# Disney FY2025 P&L Sankey

An interactive Sankey diagram of The Walt Disney Company's consolidated income statement for fiscal 2023–2025, built from the 2025 Annual Report (Form 10-K).

- **Year scrubber** — FY2023 → FY2025 on one dollar scale, with animated bands.
- **Two revenue views** — by segment, or by the eleven business lines.
- **Segment P&Ls** — click Entertainment, Sports or Experiences to see its revenue lines, the expense categories Disney reports for it, and segment operating income.
- **Every figure is sourced** — click any block to open the page of the 10-K it comes from with the number highlighted (`pages/`), plus a table view of all three years.

Everything is one static page (`index.html`) with eight page images; no build step, no dependencies beyond Google Fonts.

Figures are as reported, in $ millions. Gross profit and operating income are derived (Disney does not report those subtotals). FY2023 Entertainment business-line detail comes from the fiscal 2024 fourth-quarter earnings release (recast basis).
