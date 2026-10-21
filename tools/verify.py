#!/usr/bin/env python3
"""verify.py — the gate. Exits non-zero unless every figure ties.

    python3 tools/verify.py            # writes data/verify-report.md

Checks
  1. Filing re-read   every highlighted rectangle behind every figure is re-read from the PDF (independently of
                      the extractor's row/column logic) and must print the number the site relies on.
  2. Arithmetic       each node equals the signed sum of the lines highlighted for it.
  3. Tie-outs         the accounting identities the diagram draws: revenue lines → segments → consolidated
                      income statement → net income; segment P&Ls; segment OI reconciliation; business-line
                      profit pools; cash sources = uses; capex by segment; retained-earnings and treasury-stock
                      roll-forwards; cash-flow statement subtotals.
  4. Consistency      a line reported by more than one filing must agree, unless data/restatements.json
                      records the difference with a reason.
  5. Coverage         every non-zero figure has a source; every editorial detail was located in its filing.
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract import Doc, to_number, NUM, DASH  # noqa: E402

problems, notes = [], []


def fail(msg): problems.append(msg)


def check(name, lhs, rhs, year):
    if lhs != rhs: fail(f'FY{year} {name}: {lhs:,} ≠ {rhs:,} (difference {lhs - rhs:,})')


# ---------------------------------------------------------------------------------------------
def reread(values, sources):
    docs, words = {}, {}
    pages = sources['pages']

    def page_words(key):
        if key not in words:
            info = pages[key]
            if info['src'] not in docs: docs[info['src']] = Doc(ROOT / info['src'])
            page = docs[info['src']].pdf.pages[info['pdf'] - 1]
            words[key] = (page.extract_words(keep_blank_chars=False), float(page.width), float(page.height))
        return words[key]

    n_rects = 0
    for nid, years in sources['src'].items():
        for y, entries in years.items():
            for e in entries:
                ws, W, H = page_words(e['page'])
                if len(e['rects']) != len(e['nums']):
                    fail(f'{nid} FY{y} {e["page"]}: {len(e["rects"])} rectangles but {len(e["nums"])} numbers'); continue
                for rect, num in zip(e['rects'], e['nums']):
                    x, yy, w, h = rect[0] * W, rect[1] * H, rect[2] * W, rect[3] * H
                    inside = [t for t in ws if t['x0'] >= x - 1 and t['x1'] <= x + w + 1 and t['top'] >= yy - 1 and t['bottom'] <= yy + h + 1]
                    toks = [t['text'] for t in inside if t['text'] in DASH or NUM.match(t['text'])]
                    n_rects += 1
                    if len(toks) != 1:
                        fail(f'{nid} FY{y} {e["page"]}: rectangle {rect} holds {toks or [t["text"] for t in inside]} — expected one number'); continue
                    got = to_number(toks[0])
                    if got != num:
                        fail(f'{nid} FY{y} {e["page"]}: the filing prints {got:,} where the site expects {num:,}')
            # arithmetic: the highlighted lines of one filing (its pages together) must sum to the figure
            groups = {}
            for e in entries:
                if 'calc' not in e: continue
                key = ('alt', e['page']) if e.get('alt') else ('primary', e['page'].split(':')[0])
                groups.setdefault(key, []).extend(e['calc'])
            for key, calc in groups.items():
                total = 0
                for sign, fn, v in calc:
                    if fn == 'pos': v = max(v, 0)
                    elif fn == 'neg': v = max(-v, 0)
                    total += sign * v
                if total != values['values'][nid][y]:
                    fail(f'{nid} FY{y} {key[1]}: highlighted lines sum to {total:,}, the site shows {values["values"][nid][y]:,}')
    notes.append(f'{n_rects} highlighted numbers re-read from the filings')


# ---------------------------------------------------------------------------------------------
def tieouts(values):
    V = values['values']; L = values['links']
    for y in values['years']:
        y = str(y); v = lambda k: V[k][y]; l = lambda k: L[k][y]  # noqa: E731
        # revenue lines -> segments -> consolidated revenue
        check('Entertainment revenue = Linear + DTC + Content Sales', v('lin') + v('dtc') + v('csl'), v('ent'), y)
        check('Sports revenue = affiliate/subscription + advertising + other', v('saf') + v('sad') + v('sot'), v('spo'), y)
        check('Experiences revenue = admissions + resorts + merchandise/F&B + licensing + other', v('tpa') + v('rv') + v('mfb') + v('ml') + v('plo'), v('exp'), y)
        check('Total revenues = services + products', v('services') + v('products'), v('total'), y)
        check('Total revenues = segments − eliminations', v('ent') + v('spo') + v('exp') - v('elim'), v('total'), y)
        check('Entertainment third-party + intersegment = segment revenue', l('ent>total') + l('ent>elim'), v('ent'), y)
        check('Sports third-party + intersegment = segment revenue', l('spo>total') + l('spo>elim'), v('spo'), y)
        check('Eliminations = intersegment revenue', l('ent>elim') + l('spo>elim'), v('elim'), y)
        check('Total revenues = third-party revenue', l('ent>total') + l('spo>total') + v('exp'), v('total'), y)
        # consolidated income statement
        check('Gross profit = revenues − cost of services − cost of products', v('total') - v('cos') - v('cop'), v('gp'), y)
        check('Operating income = gross profit − SG&A − D&A − restructuring', v('gp') - v('sga') - v('da') - v('restr'), v('oi'), y)
        check('Pre-tax income = OI + equity income + other income − interest − other expense', v('oi') + v('eq') + v('othin') - v('int') - v('othout'), v('ptx'), y)
        check('Net income = pre-tax + tax benefit − tax expense', v('ptx') + v('tax') - v('taxexp'), v('ni'), y)
        check('Net income = Disney + noncontrolling interests', v('dis') + v('nci'), v('ni'), y)
        check('OI→pre-tax link', v('oi') - v('int') - v('othout'), l('oi>ptx'), y)
        check('pre-tax→net link', v('ptx') - v('taxexp'), l('ptx>ni'), y)
        # segment P&Ls
        check('Entertainment: revenue + equity income = costs + segment OI', v('entR') + v('eqE'), v('ppc') + v('oox') + v('esga') + v('eda') + v('entOI'), y)
        check('Sports: revenue + equity income = costs + segment OI', v('spoR') + v('eqS'), v('sppc') + v('sox') + v('ssga') + v('sda') + v('spoOI'), y)
        check('Experiences: revenue = costs + equity loss + segment OI', v('expR'), v('xlab') + v('xinf') + v('xcogs') + v('xoth') + v('xsga') + v('xda') + v('xeql') + v('expOI'), y)
        check('Entertainment costs block', v('ent') + v('eqE') - v('oiE'), v('cE'), y)
        check('Sports costs block', v('spo') + v('eqS') - v('oiS'), v('cS'), y)
        check('Experiences costs block', v('exp') - v('xeql') - v('oiX'), v('cX'), y)
        # segment OI reconciliation
        check('Total segment OI = Entertainment + Sports + Experiences', v('oiE') + v('oiS') + v('oiX'), v('tsoi'), y)
        check('Pre-tax = segment OI − corporate − India JV − restructuring − interest − TFCF amortization − other ± other income',
              v('tsoi') - v('corp') - v('india') - v('restrS') - v('int') - v('tfcf') - v('othout') + v('othin'), v('ptx'), y)
        check('segment OI→pre-tax link', v('tsoi') - v('corp') - v('india') - v('restrS') - v('int') - v('tfcf') - v('othout'), l('tsoi>ptx'), y)
        # business lines
        check('Sports revenue = ESPN Domestic + ESPN International + Star', v('espnD') + v('espnI') + v('star'), v('spo'), y)
        check('Experiences revenue = Parks Domestic + Parks International + Consumer Products', v('parksD') + v('parksI') + v('cp'), v('exp'), y)
        for line, oi, cost, loss in (('lin', 'oiLin', 'cLin', 'lossLin'), ('dtc', 'oiDtc', 'cDtc', 'lossDtc'), ('csl', 'oiCsl', 'cCsl', 'lossCsl'),
                                     ('espnD', 'oiEspnD', 'cEspnD', 'lossEspnD'), ('espnI', 'oiEspnI', 'cEspnI', 'lossEspnI'), ('star', 'oiStar', 'cStar', 'lossStar'),
                                     ('parksD', 'oiParksD', 'cParksD', 'lossParksD'), ('parksI', 'oiParksI', 'cParksI', 'lossParksI'), ('cp', 'oiCp', 'cCp', 'lossCp')):
            check(f'{line}: revenue = costs + operating income', v(cost) + v(oi), v(line), y)
            if v(oi) and v(loss): fail(f'FY{y} {line}: both a profit ({v(oi):,}) and a loss ({v(loss):,})')
        check('Entertainment pool − losses = segment OI', v('poolE') - v('lossLin') - v('lossDtc') - v('lossCsl'), v('entOI'), y)
        check('Sports pool − losses = segment OI', v('poolS') - v('lossEspnD') - v('lossEspnI') - v('lossStar'), v('spoOI'), y)
        check('Experiences pool − losses = segment OI', v('poolX') - v('lossParksD') - v('lossParksI') - v('lossCp'), v('expOI'), y)
        check('Entertainment pool = line operating income', v('oiLin') + v('oiDtc') + v('oiCsl'), v('poolE'), y)
        check('Sports pool = line operating income + equity income', v('eqS') + v('oiEspnD') + v('oiEspnI') + v('oiStar'), v('poolS'), y)
        check('Experiences pool = line operating income', v('oiParksD') + v('oiParksI') + v('oiCp'), v('poolX'), y)
        # cash & investment
        check('Operating cash before content = content spend + cash from operations', v('content') + v('cfo'), v('ocbc'), y)
        check('Content spend = licensed + produced', v('lic') + v('prod'), v('content'), y)
        check('Cash available = sources', v('cfo') + v('borrow') + v('cashIn') + v('othIn'), v('pool'), y)
        check('Cash available = uses', v('capex') + v('debt') + v('buyb') + v('divs') + v('hulu') + v('invest') + v('othOut') + v('cashOut'), v('pool'), y)
        check('Capex = segments + corporate', v('cxD') + v('cxI') + v('cxE') + v('cxS') + v('cxC'), v('capex'), y)
        # retained earnings / treasury stock
        check('Retained earnings roll-forward', v('reBeg') + v('reNI') - v('reDiv') + v('reOth'), v('reEnd'), y)
        check('Retained earnings: net income = net income to Disney', v('reNI'), v('dis'), y)
        check('Treasury stock roll-forward', v('tsBeg') + v('tsRep') + v('tsOth'), v('tsEnd'), y)
        # pro forma inputs are the drawn figures
        check('Pro forma: merchandise licensing', values['PF']['R'][y], v('ml'), y)
        check('Pro forma: Consumer Products revenue', values['PF']['cpRev'][y], v('cp'), y)
        check('Pro forma: Consumer Products OI', values['PF']['cpOI'][y], v('oiCp'), y)


def cashflow_subtotals():
    """Within each filing: operations + investing + financing + FX (+ discontinued) = change in cash."""
    for f in sorted((ROOT / 'extracted').glob('*.json')):
        d = json.load(open(f)); F = d['fields']
        for y in F['changeCash']:
            parts = [F[k][y]['value'] for k in ('cfo', 'cfi', 'cff', 'fx') if y in F.get(k, {})]
            if len(parts) < 4: fail(f'{d["report"]} FY{y}: cash flow subtotals missing'); continue
            disc = F['cfDisc'][y]['value'] if y in F.get('cfDisc', {}) else 0
            check(f'{d["report"]}: cash flow statement foots', sum(parts) + disc, F['changeCash'][y]['value'], y)
            check(f'{d["report"]}: income statement foots', F['total'][y]['value'] + F['totalCosts'][y]['value'] + F['restr'][y]['value'] + F['eq'][y]['value'] + F['int'][y]['value'] + F['oth'][y]['value'], F['ptx'][y]['value'], y)
            check(f'{d["report"]}: capex in cash flow = capex by segment', -F['capexCF'][y]['value'], F['capexTotal'][y]['value'], y)


# ---------------------------------------------------------------------------------------------
def consistency():
    items = json.load(open(ROOT / 'data' / 'consistency.json'))
    allow = {}
    p = ROOT / 'data' / 'restatements.json'
    if p.exists():
        for r in json.load(open(p)): allow[(r['field'], int(r['year']))] = r['reason']
    n_ok = 0
    for it in items:
        if 'field' not in it: continue  # node-level echoes of a field difference
        key = (it['field'], int(it['year']))
        if key in allow:
            n_ok += 1; notes.append(f'accepted restatement: {it["field"]} FY{it["year"]} {it["values"]} — {allow[key]}')
        else:
            fail(f'{it["field"]} FY{it["year"]} differs between filings: {it["values"]} — add it to data/restatements.json with a reason if the later filing restated it')
    for key in allow:
        if not any(('field' in it and (it['field'], int(it['year'])) == key) for it in items):
            notes.append(f'restatements.json entry no longer needed: {key}')


def coverage(values, sources):
    for nid, years in values['values'].items():
        for y, val in years.items():
            if val and not sources['src'].get(nid, {}).get(y):
                fail(f'{nid} FY{y} = {val:,} has no source in any filing')
    n = 0
    for nid, years in sources['details'].items():
        for y, items in years.items():
            for it in items:
                n += 1
                if 'page' in it and not it['rects']: fail(f'detail “{it["t"]}” ({nid} FY{y}) has a page but no highlight')
    notes.append(f'{n} editorial details located')


def main():
    values = json.load(open(ROOT / 'data' / 'values.json'))
    sources = json.load(open(ROOT / 'data' / 'sources.json'))
    reread(values, sources)
    tieouts(values)
    cashflow_subtotals()
    consistency()
    coverage(values, sources)
    n_fig = sum(len(v) for v in values['values'].values())
    lines = [f"# Verification report", "",
             f"Fiscal years {values['years'][0]}–{values['years'][-1]} · {len(values['values'])} nodes · {n_fig} figures · "
             f"{sum(len(e) for ys in sources['src'].values() for e in ys.values())} source entries on {len(sources['pages'])} filing pages", ""]
    lines += [f"**Result: {'PASS' if not problems else f'FAIL ({len(problems)} problems)'}**", ""]
    if problems: lines += ['## Problems', ''] + [f'- {p}' for p in problems] + ['']
    lines += ['## Notes', ''] + [f'- {n}' for n in notes] + ['']
    (ROOT / 'data' / 'verify-report.md').write_text('\n'.join(lines))
    print('\n'.join(lines))
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()
