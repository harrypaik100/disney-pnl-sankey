#!/usr/bin/env node
// verify_site.js — the built page must tie out exactly as the browser will draw it.
//
//   node tools/verify_site.js            # reads site/index.html
//
// Evaluates the page's own data section and value functions (NODES, LINKS, VIEWS, nodeVal, linkValue) and
// checks, for every view, every fiscal year, as reported and with the FY2027 pro forma toggle on:
//   * every block's inflows equal its outflows equal its value (blocks with only one side must match it);
//   * every drawn, non-zero block has a source page and highlight for that year;
//   * every source page image exists in site/pages/.
'use strict';
const fs = require('fs'), path = require('path'), vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'site', 'index.html'), 'utf8');

function slice(from, to) {
  const i = html.indexOf(from); if (i < 0) throw new Error('template changed: ' + from);
  const j = html.indexOf(to, i); if (j < 0) throw new Error('template changed: ' + to);
  return html.slice(i, j);
}
function fn(name) {
  const start = html.indexOf('  function ' + name + '('); if (start < 0) throw new Error('missing ' + name);
  const end = html.indexOf('\n  }\n', start); return html.slice(start, end + 4);
}
const code = slice('  const DATA = ', '  const SUB = {')
  + 'const SOURCES = DATA.sources;\n'
  + 'const state = { proforma: false, share: 0.9 };\n'
  + fn('movedOI') + fn('nodeVal') + fn('linkValue')
  + 'module.exports = { YEARS, NODES, RE, LINKS, VIEWS, PF, CP_NODE, SOURCES, state, nodeVal, linkValue };';
const ctx = { module: { exports: {} }, console };
vm.runInNewContext(code, ctx, { filename: 'site-data.js' });
const { YEARS, NODES, RE, LINKS, VIEWS, CP_NODE, SOURCES, state, nodeVal, linkValue } = ctx.module.exports;

const nodes = new Map(Object.entries(NODES).map(([id, n]) => [id, { id, ...n }]));
nodes.set(CP_NODE.id, { ...CP_NODE });
const links = LINKS.map(([s, t, spec]) => ({ s: nodes.get(s), t: nodes.get(t), spec })).filter(L => L.s && L.t);
const problems = [];
let checks = 0;

for (const [view, spec] of Object.entries(VIEWS)) {
  const active = new Set(spec.cols.flatMap(c => c.ids));
  for (const id of active) if (!nodes.has(id)) problems.push(`${view}: block ${id} is not defined`);
  for (const y of YEARS) for (const proforma of [false, true]) {
    state.proforma = proforma;
    const label = `${view} FY${y}${proforma ? ' pro forma' : ''}`;
    for (const id of active) {
      const n = nodes.get(id); if (!n) continue;
      const v = nodeVal(n, y);
      let inn = 0, out = 0, hasIn = false, hasOut = false;
      for (const L of links) {
        if (L.t.id === id && active.has(L.s.id)) { inn += linkValue(L, y); hasIn = true; }
        if (L.s.id === id && active.has(L.t.id)) { out += linkValue(L, y); hasOut = true; }
      }
      checks++;
      if (hasIn && Math.round(inn) !== Math.round(v)) problems.push(`${label}: ${id} inflows ${inn} ≠ value ${v}`);
      if (hasOut && Math.round(out) !== Math.round(v)) problems.push(`${label}: ${id} outflows ${out} ≠ value ${v}`);
      if (!hasIn && !hasOut) problems.push(`${label}: ${id} is drawn but has no bands`);
      if (!proforma && v && !(SOURCES.src[id] && SOURCES.src[id][y] && SOURCES.src[id][y].length))
        problems.push(`${label}: ${id} = ${v} has no source page`);
    }
  }
}
for (const [id, r] of Object.entries(RE)) for (const y of YEARS) {
  if (r.v[y] && !(SOURCES.src[id] && SOURCES.src[id][y] && SOURCES.src[id][y].length)) problems.push(`retained earnings strip FY${y}: ${id} has no source page`);
}
for (const [key, p] of Object.entries(SOURCES.pages)) {
  if (!fs.existsSync(path.join(ROOT, 'site', p.file))) problems.push(`page image missing: ${p.file} (${key})`);
}
for (const [id, years] of Object.entries(SOURCES.src)) for (const [y, entries] of Object.entries(years)) for (const e of entries) {
  if (!SOURCES.pages[e.page]) problems.push(`${id} FY${y}: source points at unknown page ${e.page}`);
  if (!e.rects.length) problems.push(`${id} FY${y}: source on ${e.page} has no highlight`);
}
const views = Object.keys(VIEWS).length;
if (problems.length) {
  console.log(`site check FAILED: ${problems.length} problems in ${checks} block checks across ${views} views × ${YEARS.length} years × 2\n  ` + problems.slice(0, 40).join('\n  '));
  process.exit(1);
}
console.log(`site check PASS: ${checks} block checks across ${views} views × ${YEARS.length} years (as reported and pro forma); ${Object.keys(SOURCES.pages).length} page images present`);
