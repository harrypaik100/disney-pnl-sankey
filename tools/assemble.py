#!/usr/bin/env python3
"""assemble.py — turn the extracted filings into the figures the site draws.

    python3 tools/assemble.py

Reads   extracted/<report>.json       (one per filing, written by extract.py)
        data/details.json             (editorial "Behind this figure" items, located in the filings here)
Writes  data/values.json              nodes, links, pro forma inputs, EPS, fiscal-year ends
        data/sources.json             page map + the highlighted rectangles behind every figure
        data/consistency.json         every figure reported by more than one filing, with any differences

Rules
  * A fiscal year's figures come from that year's own 10-K; later 10-Ks' comparative columns are kept as
    alternate sources and compared (see consistency.json). Figures a filing does not restate (e.g. the
    significant-segment-expense categories first disclosed in the FY2025 10-K) come from the filing that has them.
  * Every node is a signed sum of extracted fields (NODE_TERMS); every link value is a signed sum of nodes
    (LINK_TERMS). No figure is typed in by hand.
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract import Doc, OPTIONAL, page_label  # noqa: E402

# ---------------------------------------------------------------------------------------------
# Node definitions: id -> list of terms. Term syntax:
#   field        +value          -field       -value
#   pos(field)   max(value, 0)   neg(field)   max(-value, 0)
#   prev(field)  the field for the prior fiscal year (opening balances)
#   @node        another node's value (derived totals)
# Optional fields that a filing does not report count as zero.
# ---------------------------------------------------------------------------------------------
NODE_TERMS = {
    # business lines (revenue note / MD&A)
    'lin': ['linRev'], 'dtc': ['dtcRev'], 'csl': ['cslRev'],
    'saf': ['subAffS'], 'sad': ['advS'], 'sot': ['tvvodS', 'otherS'],
    'tpa': ['tpa'], 'rv': ['rv'], 'mfb': ['mfb'], 'ml': ['ml'], 'plo': ['otherX', 'advX'],
    # segments and the consolidated income statement
    'services': ['services'], 'products': ['products'],
    'ent': ['entRev'], 'spo': ['spoRev'], 'exp': ['expRev'], 'total': ['total'], 'elim': ['-elim'],
    'gp': ['total', 'cos', 'cop'], 'cos': ['-cos'], 'cop': ['-cop'],
    'oi': ['total', 'totalCosts', 'restr'], 'sga': ['-sga'], 'da': ['-da'], 'restr': ['-restr'],
    'eq': ['eq'], 'othin': ['pos(oth)'], 'othout': ['neg(oth)'], 'int': ['-int'],
    'ptx': ['ptx'], 'tax': ['pos(taxSigned)'], 'taxexp': ['neg(taxSigned)'],
    'ni': ['ni'], 'dis': ['dis'], 'nci': ['-nci'],
    # segment P&Ls (segment note: significant segment expenses)
    'eqE': ['eqE'], 'eqS': ['eqS'], 'xeql': ['neg(eqX)'],
    'entR': ['entRev'], 'entOI': ['entOI'], 'ppc': ['ppc'], 'oox': ['oox'], 'esga': ['esga'], 'eda': ['eda'],
    'spoR': ['spoRev'], 'spoOI': ['spoOI'], 'sppc': ['sppc'], 'sox': ['sox'], 'ssga': ['ssga'], 'sda': ['sda'],
    'expR': ['expRev'], 'expOI': ['expOI'], 'xlab': ['xlab'], 'xinf': ['xinf'], 'xcogs': ['xcogs'], 'xoth': ['xoth'], 'xsga': ['xsga'], 'xda': ['xda'],
    # segment operating income view
    'oiE': ['entOI'], 'oiS': ['spoOI'], 'oiX': ['expOI'],
    'cE': ['entRev', 'eqE', '-entOI'], 'cS': ['spoRev', 'eqS', '-spoOI'], 'cX': ['expRev', 'eqX', '-expOI'],
    'tsoi': ['tsoi'], 'corp': ['-corp'], 'india': ['neg(india)'], 'restrS': ['-restrS'], 'tfcf': ['-tfcf'],
    # business-line profitability view (MD&A supplemental detail)
    'espnD': ['espnDRev'], 'espnI': ['espnIRev'], 'star': ['starRev'], 'parksD': ['parksDRev'], 'parksI': ['parksIRev'], 'cp': ['cpRev'],
    'oiLin': ['pos(linOI)'], 'cLin': ['linRev', '-pos(linOI)'],
    'oiDtc': ['pos(dtcOI)'], 'cDtc': ['dtcRev', '-pos(dtcOI)'],
    'oiCsl': ['pos(cslOI)'], 'cCsl': ['cslRev', '-pos(cslOI)'],
    'oiEspnD': ['pos(espnDOI)'], 'cEspnD': ['espnDRev', '-pos(espnDOI)'],
    'oiEspnI': ['pos(espnIOI)'], 'cEspnI': ['espnIRev', '-pos(espnIOI)'],
    'oiStar': ['pos(starOI)'], 'cStar': ['starRev', '-pos(starOI)'],
    'oiParksD': ['pos(parksDOI)'], 'cParksD': ['parksDRev', '-pos(parksDOI)'],
    'oiParksI': ['pos(parksIOI)'], 'cParksI': ['parksIRev', '-pos(parksIOI)'],
    'oiCp': ['pos(cpOI)'], 'cCp': ['cpRev', '-pos(cpOI)'],
    'poolE': ['pos(linOI)', 'pos(dtcOI)', 'pos(cslOI)'],
    'poolS': ['eqS', 'pos(espnDOI)', 'pos(espnIOI)', 'pos(starOI)'],
    'poolX': ['pos(parksDOI)', 'pos(parksIOI)', 'pos(cpOI)'],
    'lossLin': ['neg(linOI)'], 'lossDtc': ['neg(dtcOI)'], 'lossCsl': ['neg(cslOI)'],
    'lossEspnD': ['neg(espnDOI)'], 'lossEspnI': ['neg(espnIOI)'], 'lossStar': ['neg(starOI)'],
    'lossParksD': ['neg(parksDOI)'], 'lossParksI': ['neg(parksIOI)'], 'lossCp': ['neg(cpOI)'],
    # cash & investment view (cash flow statement, content spend table, capex by segment)
    'ocbc': ['cfo', 'licSpend', 'prodSpend'], 'content': ['licSpend', 'prodSpend'], 'lic': ['licSpend'], 'prod': ['prodSpend'],
    'cfo': ['cfo'], 'borrow': ['borrowings', 'pos(cpNet)'], 'cashIn': ['neg(changeCash)'],
    'othIn': ['contributions', 'proceedsInv', 'fx', 'pos(othInv)', 'pos(othFin)', 'pos(optionProceeds)', 'pos(cfDisc)'],
    'pool': ['@cfo', '@borrow', '@cashIn', '@othIn'],
    'capex': ['-capexCF'], 'cxD': ['cxD'], 'cxI': ['cxI'], 'cxE': ['cxE'], 'cxS': ['cxS'], 'cxC': ['cxC'],
    'debt': ['-reduction', 'neg(cpNet)'], 'buyb': ['neg(repurchasesCF)'], 'divs': ['neg(dividendsCF)'],
    'hulu': ['neg(acqRNCI)'], 'invest': ['neg(purchaseInv)'],
    'othOut': ['neg(othFin)', 'neg(othInv)', 'neg(cfDisc)'], 'cashOut': ['pos(changeCash)'],
    # retained earnings and treasury stock roll-forward (statement of equity)
    'reBeg': ['prev(reEnd)'], 'reNI': ['reNI'], 'reDiv': ['neg(reDiv)'], 'reOth': ['reOth'], 'reEnd': ['reEnd'],
    'tsBeg': ['-prev(tsEnd)'], 'tsRep': ['neg(tsRep)'], 'tsOth': ['neg(tsOth)'], 'tsEnd': ['-tsEnd'],
}

# Links whose value is not simply the source or target node's value.
LINK_TERMS = {
    'ent>total': ['ent3p'], 'ent>elim': ['entInter'], 'spo>total': ['spo3p'], 'spo>elim': ['spoInter'],
    'oi>ptx': ['@oi', '-@int', '-@othout'], 'ptx>ni': ['@ptx', '-@taxexp'],
    'entR>entOI': ['@entOI', '-@eqE'], 'spoR>spoOI': ['@spoOI', '-@eqS'],
    'ent>oiE': ['@entOI', '-@eqE'], 'spo>oiS': ['@spoOI', '-@eqS'],
    'tsoi>ptx': ['@tsoi', '-@corp', '-@india', '-@restrS', '-@int', '-@tfcf', '-@othout'],
    'ml>ent': [], 'oiCp>poolE': [],
    'poolE>tsoi': ['@entOI'], 'poolS>tsoi': ['@spoOI'], 'poolX>tsoi': ['@expOI'],
}

# FY2027 pro forma inputs (Consumer Products licensing moves to Entertainment) and headline figures.
PF_TERMS = {'R': 'ml', 'cpRev': 'cp', 'cpOI': 'oiCp'}  # node ids
EPS_FIELD = 'epsDiluted'

# Alternate source tables that show the same figure under another heading (used only when the figure is equal).
ALT_FIELDS = {'saf': ['safM'], 'sad': ['sadM'], 'sot': ['sotM'], 'tpa': ['tpaM'], 'rv': ['rvM'], 'plo': ['ploM'],
              'xlab': ['xlabM'], 'xinf': ['xinfM'], 'xcogs': ['xcogsM'], 'xoth': ['xothM'], 'capex': ['capexTotal'],
              'cE': ['cE'], 'cS': ['cS'], 'cX': ['cX']}
ALT_SIGN = {'xlabM': -1, 'xinfM': -1, 'xcogsM': -1, 'xothM': -1}

NODE_NAMES = {'cfo': 'cash provided by operations', 'borrow': 'borrowings (incl. commercial paper, net)', 'cashIn': 'cash drawn from the balance',
              'othIn': 'other sources (contributions, investment sales, FX, other)', 'oi': 'operating income', 'int': 'interest expense, net',
              'othout': 'other expense', 'ptx': 'income before income taxes', 'taxexp': 'income taxes', 'entOI': 'Entertainment segment operating income',
              'spoOI': 'Sports segment operating income', 'expOI': 'Experiences segment operating income', 'eqE': 'Entertainment equity income',
              'eqS': 'Sports equity income', 'tsoi': 'total segment operating income', 'corp': 'corporate and unallocated shared expenses',
              'india': 'equity in the loss of India joint venture', 'restrS': 'restructuring and impairment charges', 'tfcf': 'TFCF and Hulu acquisition amortization'}

TERM = re.compile(r'^(-?)(?:(pos|neg|prev)\()?(@?\w+)\)?$')


def parse(term):
    m = TERM.match(term)
    if not m: raise ValueError(term)
    sign = -1 if m.group(1) else 1
    return sign, m.group(2), m.group(3)


class Filings:
    def __init__(self):
        self.reports = {}
        for f in sorted((ROOT / 'extracted').glob('*.json')):
            d = json.load(open(f))
            self.reports[d['report']] = d
        if not self.reports:
            sys.exit('no extracted filings — run tools/extract.py --all first')
        self.by_year = {d['fiscalYear']: d['report'] for d in self.reports.values()}
        self.years = sorted(self.by_year)
        self.short = {r: 'r' + str(self.reports[r]['fiscalYear'])[2:] for r in self.reports}

    def order(self, year):
        """Reports to consult for a fiscal year: its own 10-K first, then later ones (comparative columns)."""
        own = [self.by_year[year]] if year in self.by_year else []
        later = [self.by_year[y] for y in self.years if y > year]
        return own + later

    def figure(self, field, year, report):
        f = self.reports[report]['fields'].get(field)
        return f.get(str(year)) if f else None

    def all_figures(self, field, year):
        return [(r, self.figure(field, year, r)) for r in self.order(year) if self.figure(field, year, r)]


class Assembler:
    def __init__(self, fl):
        self.fl = fl
        self.loc = Locator(fl)
        self.values = {}      # node -> {year: value}
        self.sources = {}     # node -> {year: [entries]}
        self.pages = {}       # 'r25:41' -> page info
        self.consistency = []

    def page_key(self, report, pno):
        short = self.fl.short[report]
        key = f'{short}:{pno}'
        if key not in self.pages:
            rep = self.fl.reports[report]
            info = rep['pages'].get(str(pno))
            if info is None:  # a page only the editorial details point at
                doc = self.loc.doc(report); pg = doc.pdf.pages[pno - 1]
                info = {'printed': doc.printed_page(pno), 'label': page_label(doc, pno), 'w': round(float(pg.width)), 'h': round(float(pg.height))}
            fy = rep['fiscalYear']
            self.pages[key] = {
                'printed': info['printed'], 'label': info['label'], 'file': f'pages/{short}-p{pno:03d}.jpg',
                'report': f'{fy} Annual Report (Form 10-K)', 'short': f'{fy} 10-K p. {info["printed"]}',
                'pdf': pno, 'src': f'filings/{report}.pdf', 'w': info['w'], 'h': info['h']}
        return key

    def term_figures(self, terms, year, report):
        """For one report: (value, [(sign, fig, year_used)]) or None if a required field is missing there."""
        total, parts, found = 0, [], 0
        for t in terms:
            sign, fn, name = parse(t)
            if name.startswith('@'):
                found += 1
                v = self.values[name[1:]][year]
                if fn == 'pos': v = max(v, 0)
                elif fn == 'neg': v = max(-v, 0)
                total += sign * v
                parts.append((sign, None, year, name[1:], sign * v))
                continue
            y = year - 1 if fn == 'prev' else year
            fig = self.fl.figure(name, y, report)
            if fig is None:
                if name in OPTIONAL: continue
                return None
            found += 1
            v = fig['value']
            if fn == 'pos': v = max(v, 0)
            elif fn == 'neg': v = max(-v, 0)
            total += sign * v
            parts.append((sign, fig, y, name, sign * v))
        if terms and not found: return None  # this filing has none of the lines
        return total, parts

    def entries_for(self, parts, report, own_report, year, terms):
        """Source entries (one per page) for the figures behind a value."""
        # lines that contribute nothing (zero, or clipped to zero by pos/neg) are not highlighted unless nothing else is
        live = [p for p in parts if p[4] != 0] or parts[:1]
        by_page = {}
        for sign, fig, y, name, _ in live:
            if fig is None:  # a derived node: reuse its own primary sources
                for e in self.sources.get(name, {}).get(year, [])[:1]:
                    d = by_page.setdefault(e['page'], {'rects': [], 'parts': [], 'nums': [], 'calc': [], 'derived': True})
                    d['rects'] += e['rects']; d['nums'] += e['nums']; d['parts'].append((sign, None, NODE_NAMES.get(name, name)))
                continue
            key = self.page_key(report, fig['page'])
            d = by_page.setdefault(key, {'rects': [], 'parts': [], 'nums': [], 'calc': [], 'derived': False})
            rects = fig.get('rects') or [fig['rect']]
            nums = fig.get('values') or [fig['value']]
            d['rects'] += rects; d['nums'] += nums
            d['calc'].append([sign, parse(next(t for t in terms if parse(t)[2] == name))[1], fig['value']])
            d['parts'].append((sign, fig['table'], f"“{fig['label']}”"))
        out = []
        for key, d in by_page.items():
            tables = {t for _, t, _ in d['parts'] if t}
            if len(d['parts']) == 1:
                sign, t, lab = d['parts'][0]
                what = f"{t} — {lab}" if t else lab
            else:
                terms = ''
                for i, (sign, t, lab) in enumerate(d['parts']):
                    op = ('' if i == 0 else ' + ') if sign > 0 else (' − ' if i else '− ')
                    terms += op + (lab if t in tables and len(tables) == 1 or not t else f'{t} — {lab}')
                what = (f"{next(iter(tables))} — " if len(tables) == 1 else '') + terms + ' (derived)'
            if not any(t and 'Equity' in t for _, t, _ in d['parts']):
                what += f', FY{year} column'
            if report != own_report:
                what += f' — comparative column of the {self.fl.reports[report]["fiscalYear"]} 10-K'
            e = {'page': key, 'rects': d['rects'], 'what': what, 'nums': d['nums']}
            if not d['derived']: e['calc'] = d['calc']
            out.append(e)
        return out

    def node(self, nid, terms, year):
        own = self.fl.by_year[year]
        value, entries = None, []
        for report in self.fl.order(year):
            res = self.term_figures(terms, year, report)
            if res is None: continue
            v, parts = res
            if value is None:
                value = v
            elif v != value:
                self.consistency.append({'node': nid, 'year': year, 'report': report, 'value': v, 'own': value})
                continue  # a differing comparative column is not a source for this figure
            entries += self.entries_for(parts, report, own, year, terms)
        if value is None:
            if all(parse(t)[2] in OPTIONAL for t in terms): value = 0  # a line no filing reports for this year
            else: sys.exit(f'{nid} FY{year}: no filing provides {terms}')
        self.values.setdefault(nid, {})[year] = value
        self.sources.setdefault(nid, {})[year] = entries
        # alternates: other tables that print the same figure
        for alt in ALT_FIELDS.get(nid, []):
            for report, fig in self.fl.all_figures(alt, year):
                if fig['value'] * ALT_SIGN.get(alt, 1) == value and fig['table']:
                    key = self.page_key(report, fig['page'])
                    if not any(e['page'] == key for e in entries):
                        entries.append({'page': key, 'rects': fig.get('rects') or [fig['rect']], 'nums': fig.get('values') or [fig['value']],
                                        'calc': [[ALT_SIGN.get(alt, 1), None, fig['value']]], 'alt': True,
                                        'what': f"{fig['table']} — “{fig['label']}”, FY{year} column" +
                                                ('' if report == own else f' (comparative column of the {self.fl.reports[report]["fiscalYear"]} 10-K)')})

    def run(self):
        years = self.fl.years
        # field-level cross-report consistency (same line, same year, different filings)
        fields = sorted({f for r in self.fl.reports.values() for f in r['fields']})
        for f in fields:
            for y in years:
                figs = self.fl.all_figures(f, y)
                if len(figs) > 1:
                    vals = {r: fig['value'] for r, fig in figs}
                    if len(set(vals.values())) > 1:
                        self.consistency.append({'field': f, 'year': y, 'values': vals})
        for nid, terms in NODE_TERMS.items():
            for y in years:
                self.node(nid, terms, y)
        links = {}
        for key, terms in LINK_TERMS.items():
            links[key] = {}
            for y in years:
                v = 0
                for t in terms:
                    sign, fn, name = parse(t)
                    x = self.fl.figure(name, y, self.fl.by_year[y])['value'] if not name.startswith('@') else self.values[name[1:]][y]
                    if fn == 'pos': x = max(x, 0)
                    elif fn == 'neg': x = max(-x, 0)
                    v += sign * x
                links[key][y] = v
        pf = {k: {y: self.values[n][y] for y in years} for k, n in PF_TERMS.items()}
        eps = {y: self.fl.figure(EPS_FIELD, y, self.fl.by_year[y])['value'] for y in years}
        fy_end = {y: self.fl.reports[self.fl.by_year[y]]['fiscalYearEnd'] for y in years}
        values = {'years': years, 'fyEnd': fy_end, 'values': self.values, 'links': links, 'PF': pf, 'EPS': eps,
                  'reports': {self.fl.short[r]: {'report': r, 'fiscalYear': self.fl.reports[r]['fiscalYear']} for r in self.fl.reports}}
        return values


# ---------------------------------------------------------------------------------------------
# Editorial details ("Behind this figure"): located in the fiscal year's own filing by phrase.
# ---------------------------------------------------------------------------------------------
def _norm(w): return re.sub(r'[^a-z0-9]', '', w.lower())


class Locator:
    def __init__(self, fl):
        self.fl = fl; self.docs = {}; self.words = {}

    def doc(self, report):
        if report not in self.docs:
            self.docs[report] = Doc(ROOT / 'filings' / f'{report}.pdf')
        return self.docs[report]

    def page_words(self, report, pno):
        k = (report, pno)
        if k not in self.words:
            page = self.doc(report).pdf.pages[pno - 1]
            self.words[k] = page.extract_words(keep_blank_chars=False, use_text_flow=True)
        return self.words[k]

    def phrase(self, report, text):
        """(page, [rects]) for a phrase, searched as a normalized token sequence across the filing."""
        doc = self.doc(report)
        toks = [_norm(t) for t in text.split() if _norm(t)]
        needle = ''.join(toks)
        for pno in range(1, doc.n + 1):
            if needle not in re.sub(r'[^a-z0-9]', '', doc.text(pno).lower()): continue
            ws = self.page_words(report, pno)
            nw = [_norm(w['text']) for w in ws]
            # walk the words, concatenating until the phrase matches
            for i in range(len(ws)):
                if not nw[i] or not needle.startswith(nw[i]): continue
                acc, j = '', i
                while j < len(ws) and len(acc) < len(needle):
                    acc += nw[j]; j += 1
                if acc == needle:
                    return pno, self.rects(doc, pno, ws[i:j])
        return None

    def number(self, report, pno, num, hint=None):
        doc = self.doc(report)
        ws = self.page_words(report, pno)
        cands = [w for w in ws if w['text'].strip('$') == num]
        if hint and len(cands) > 1:
            lines = doc.lines(pno)
            cands = [w for w in cands if any(hint in l['text'] and any(abs(x['top'] - w['top']) < 2 for x in l['words']) for l in lines)] or cands
        return self.rects(doc, pno, cands[:1]) if cands else []

    @staticmethod
    def rects(doc, pno, words):
        """Highlight boxes as page fractions, one per text line the words span."""
        page = doc.pdf.pages[pno - 1]; W, H = float(page.width), float(page.height)
        out, line = [], []
        for w in words:
            if line and abs(w['top'] - line[-1]['top']) > 3:
                out.append(line); line = []
            line.append(w)
        if line: out.append(line)
        res = []
        for ln in out:
            x0 = min(w['x0'] for w in ln); x1 = max(w['x1'] for w in ln)
            y0 = min(w['top'] for w in ln); y1 = max(w['bottom'] for w in ln)
            res.append([round(x0 / W, 4), round(y0 / H, 4), round((x1 - x0) / W, 4), round((y1 - y0) / H, 4)])
        return res


def details(fl, asm):
    spec_path = ROOT / 'data' / 'details.json'
    if not spec_path.exists(): return {}
    spec = json.load(open(spec_path))
    loc = asm.loc
    out, problems = {}, []
    for item in spec:
        nid, year = item['node'], int(item['year'])
        if year not in fl.by_year: continue
        report = fl.by_year[year]
        entry = {'t': item['title'], 'x': item['text']}
        rects, page = [], None
        for ph in item.get('phrases', []):
            hit = loc.phrase(report, ph)
            if not hit:
                problems.append(f'{nid} FY{year}: phrase not found in {report}: “{ph[:60]}…”'); continue
            if page is None: page = hit[0]
            if hit[0] == page: rects += hit[1]
        for num in item.get('numbers', []):
            if page is None:
                problems.append(f'{nid} FY{year}: number {num} needs a phrase to locate its page'); continue
            r = loc.number(report, page, num, item.get('hint'))
            if not r: problems.append(f'{nid} FY{year}: number {num} not on page {page} of {report}')
            rects += r
        if page is not None:
            entry['page'] = asm.page_key(report, page); entry['rects'] = rects
        out.setdefault(nid, {}).setdefault(str(year), []).append(entry)
    if problems:
        print('details not located:\n  ' + '\n  '.join(problems), file=sys.stderr)
        sys.exit(1)
    return out


def main():
    fl = Filings()
    asm = Assembler(fl)
    values = asm.run()
    det = details(fl, asm)
    (ROOT / 'data').mkdir(exist_ok=True)
    json.dump(values, open(ROOT / 'data' / 'values.json', 'w'), indent=1)
    src = {'pages': asm.pages, 'src': {n: {str(y): e for y, e in ys.items()} for n, ys in asm.sources.items()}, 'details': det}
    json.dump(src, open(ROOT / 'data' / 'sources.json', 'w'), indent=0, ensure_ascii=False)
    json.dump(asm.consistency, open(ROOT / 'data' / 'consistency.json', 'w'), indent=1)
    n_entries = sum(len(e) for ys in asm.sources.values() for e in ys.values())
    print(f"{len(fl.reports)} filings, fiscal years {fl.years[0]}–{fl.years[-1]}: {len(asm.values)} nodes, "
          f"{n_entries} source entries on {len(asm.pages)} pages; {len(asm.consistency)} cross-report differences -> data/")


if __name__ == '__main__':
    main()
