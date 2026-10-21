#!/usr/bin/env python3
"""build.py — render the site from the template and the verified data.

    python3 tools/build.py            # -> site/index.html, site/pages/*.jpg, site/.nojekyll

Reads   template/index.html, template/notes.html, data/values.json, data/sources.json, filings/*.pdf
Writes  site/

The page's figures, links, pro forma inputs, EPS, fiscal-year ends, source map and the table view are all
generated here; the template holds only layout, code and editorial text. Every node the template draws must
have a value, and every value the pipeline produces that the template cannot draw must be zero.
"""
import json, re, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / 'site'
WORDS = {1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six', 7: 'seven', 8: 'eight', 9: 'nine', 10: 'ten'}


def fmt(v, paren=False):
    """1234 -> 1,234; -1234 -> (1,234); 0 -> —"""
    if v is None: return ''
    if v == 0: return '—'
    s = f'{abs(v):,}'
    return f'({s})' if (v < 0) else s


def pct(a, b):
    """Change of a vs b as +3.4% / −1.6%; — when not meaningful."""
    if not a or not b or (a < 0) != (b < 0): return '—', ''
    c = (a - b) / abs(b) * 100
    return (('+' if c >= 0 else '−') + f'{abs(c):.1f}%'), ('pos' if c > 0 else 'neg' if c < 0 else '')


# ---------------------------------------------------------------------------------------------
# Table view. Cell expressions: a node id, '-id' to show it as a deduction, 'a-b' for a signed net,
# 'EPS' for diluted EPS, '' for a blank cell.
# ---------------------------------------------------------------------------------------------
def cell(V, y, expr, EPS):
    if expr == '': return None
    if expr == 'EPS': return EPS[y]
    total = 0
    for term in re.findall(r'[+-]?\w+', expr.replace(' ', '')):
        sign = -1 if term.startswith('-') else 1
        total += sign * V[term.lstrip('+-')][y]
    return total

INCOME = [  # (label, class, expr, favourable-when-up)
    ('Services revenue', '', 'services', True), ('Products revenue', '', 'products', True),
    ('Total revenues', 'sum', 'total', True),
    ('Cost of services (excl. D&amp;A)', '', '-cos', False), ('Cost of products (excl. D&amp;A)', '', '-cop', False),
    ('Gross profit', 'sum derived', 'gp', True),
    ('Selling, general, administrative and other', '', '-sga', False), ('Depreciation and amortization', '', '-da', False),
    ('Restructuring and impairment charges', '', '-restr', False),
    ('Operating income', 'sum derived', 'oi', True),
    ('Other income (expense), net', '', 'othin-othout', None), ('Interest expense, net', '', '-int', False),
    ('Equity in the income of investees', '', 'eq', True),
    ('Income before income taxes', 'sum', 'ptx', True), ('Income taxes: benefit / (expense)', '', 'tax-taxexp', None),
    ('Net income', 'sum', 'ni', True), ('Attributable to noncontrolling interests', '', '-nci', False),
    ('Net income attributable to Disney', 'sum', 'dis', True), ('Diluted EPS ($)', '', 'EPS', True),
]
SEGMENTS = [  # (label, class, revenue expr, OI expr)
    ('Entertainment', 'sum', 'ent', 'entOI'), ('Linear Networks', 'sub', 'lin', 'oiLin-lossLin'),
    ('Direct-to-Consumer', 'sub', 'dtc', 'oiDtc-lossDtc'), ('Content Sales/Licensing and Other', 'sub', 'csl', 'oiCsl-lossCsl'),
    ('Sports', 'sum', 'spo', 'spoOI'), ('Affiliate and subscription fees', 'sub', 'saf', ''), ('Advertising', 'sub', 'sad', ''),
    ('Other (TV/VOD distribution, sub-licensing, intersegment fees)', 'sub', 'sot', ''),
    ('Experiences', 'sum', 'exp', 'expOI'), ('Theme park admissions', 'sub', 'tpa', ''), ('Resorts and vacations', 'sub', 'rv', ''),
    ('Retail and wholesale sales of merchandise, food and beverage', 'sub', 'mfb', ''), ('Merchandise licensing', 'sub', 'ml', ''),
    ('Parks licensing and other (incl. any Experiences advertising)', 'sub', 'plo', ''),
    ('Intersegment eliminations', '', '-elim', None), ('Total', 'sum', 'total', 'tsoi'),
]
RECON = [
    ('Total segment operating income', 'sum', 'tsoi'), ('Corporate and unallocated shared expenses', '', '-corp'),
    ('Equity in the loss of India joint venture', '', '-india'), ('Restructuring and impairment charges (as reconciled)', '', '-restrS'),
    ('Other income (expense), net', '', 'othin-othout'), ('Interest expense, net', '', '-int'),
    ('TFCF and Hulu acquisition amortization', '', '-tfcf'), ('Income before income taxes', 'sum', 'ptx'),
]
CASH = [
    ('Content spend (licensed programming and rights + produced content)', '', 'content'),
    ('Licensed programming and rights', 'sub', 'lic'), ('Produced content', 'sub', 'prod'),
    ('Cash provided by operations', 'sum', 'cfo'), ('New borrowings (incl. net commercial paper when positive)', '', 'borrow'),
    ('Cash drawn from balance', '', 'cashIn'), ('Other inflows (contributions, investment sales, FX, other)', '', 'othIn'),
    ('Cash available to allocate', 'sum', 'pool'),
    ('Capital expenditures', '', '-capex'), ('Experiences Domestic', 'sub', '-cxD'), ('Experiences International', 'sub', '-cxI'),
    ('Entertainment', 'sub', '-cxE'), ('Sports', 'sub', '-cxS'), ('Corporate', 'sub', '-cxC'),
    ('Acquisition of redeemable noncontrolling interests (Hulu / BAMTech)', '', '-hulu'), ('Purchase of investments', '', '-invest'),
    ('Share repurchases', '', '-buyb'), ('Dividends paid', '', '-divs'),
    ('Debt reduction (repayments + net commercial paper)', '', '-debt'), ('Other outflows', '', '-othOut'), ('Cash added to balance', '', '-cashOut'),
]
EQUITY = [
    ('Retained earnings, start of year', '', 'reBeg'), ('Net income attributable to Disney', 'sub', 'reNI'),
    ('Dividends declared', 'sub', '-reDiv'), ('Distributions and other', 'sub', 'reOth'), ('Retained earnings, end of year', 'sum', 'reEnd'),
    ('Treasury stock, start of year', '', '-tsBeg'), ('Common stock repurchases', 'sub', '-tsRep'), ('Other', 'sub', '-tsOth'),
    ('Treasury stock, end of year', 'sum', '-tsEnd'),
]
LINES = [  # (label, class, revenue, OI)
    ('Entertainment', 'sum', 'ent', 'entOI'), ('Linear Networks', 'sub', 'lin', 'oiLin-lossLin'),
    ('Direct-to-Consumer', 'sub', 'dtc', 'oiDtc-lossDtc'), ('Content Sales/Licensing and Other', 'sub', 'csl', 'oiCsl-lossCsl'),
    ('Sports', 'sum', 'spo', 'spoOI'), ('ESPN Domestic', 'sub', 'espnD', 'oiEspnD-lossEspnD'),
    ('ESPN International', 'sub', 'espnI', 'oiEspnI-lossEspnI'), ('Star India', 'sub', 'star', 'oiStar-lossStar'),
    ('Equity in the income of investees', 'sub', '', 'eqS'),
    ('Experiences', 'sum', 'exp', 'expOI'), ('Parks &amp; Experiences Domestic', 'sub', 'parksD', 'oiParksD-lossParksD'),
    ('Parks &amp; Experiences International', 'sub', 'parksI', 'oiParksI-lossParksI'), ('Consumer Products', 'sub', 'cp', 'oiCp-lossCp'),
]
CP = [('Consumer Products revenue', '', 'cp'), ('Consumer Products operating income', '', 'oiCp'), ('of which merchandise licensing revenue (revenue note)', '', 'ml')]
SEGEXP = [
    ('Entertainment', 'sum', 'cE'), ('Programming and production costs', 'sub', 'ppc'), ('Other segment operating expenses', 'sub', 'oox'),
    ('Selling, general, administrative and other', 'sub', 'esga'), ('Depreciation and amortization', 'sub', 'eda'),
    ('Equity in the income of investees (added to OI)', 'sub', 'eqE'),
    ('Sports', 'sum', 'cS'), ('Programming and production costs', 'sub', 'sppc'), ('Other segment operating expenses', 'sub', 'sox'),
    ('Selling, general, administrative and other', 'sub', 'ssga'), ('Depreciation and amortization', 'sub', 'sda'),
    ('Equity in the income of investees (added to OI)', 'sub', 'eqS'),
    ('Experiences', 'sum', 'cX'), ('Operating labor', 'sub', 'xlab'), ('Infrastructure costs', 'sub', 'xinf'),
    ('Costs of goods sold and distribution costs', 'sub', 'xcogs'), ('Other segment operating expenses', 'sub', 'xoth'),
    ('Selling, general, administrative and other', 'sub', 'xsga'), ('Depreciation and amortization', 'sub', 'xda'),
    ('Equity in the loss of investees (a cost)', 'sub', 'xeql'),
]


def table(head, rows, V, EPS, years, kind='simple'):
    ys = sorted(years, reverse=True)
    out = ['          <table>', '            <thead><tr>' + ''.join(f'<th>{h}</th>' for h in head) + '</tr></thead>', '            <tbody>']
    for row in rows:
        label, cls = row[0], row[1]
        tds = []
        if kind == 'income':
            expr, good = row[2], row[3]
            vals = [cell(V, y, expr, EPS) for y in ys]
            tds = [f'<td>{v:.2f}</td>' if expr == 'EPS' else f'<td>{fmt(v)}</td>' for v in vals]
            ch, c = pct(vals[0], vals[1]) if len(vals) > 1 else ('', '')
            if good is False and ch != '—':  # cost lines: show the change in magnitude, without a verdict colour
                ch, c = pct(abs(vals[0]), abs(vals[1]))[0], ''
            if good is None: c = ''
            tds.append(f'<td class="{c}">{ch}</td>' if c else f'<td>{ch}</td>')
        elif kind == 'segments':
            rev, oi = row[2], row[3]
            tds = [f'<td>{fmt(cell(V, y, rev, EPS))}</td>' for y in ys]
            tds += [f'<td>{"—" if oi is None else fmt(cell(V, y, oi, EPS)) if oi else ""}</td>' for y in ys]
        elif kind == 'lines':
            rev, oi = row[2], row[3]
            for y in ys:
                r, o = cell(V, y, rev, EPS), cell(V, y, oi, EPS)
                tds.append(f'<td>{"" if r is None else fmt(r)}</td><td>{"" if o is None else fmt(o)}</td>')
                if r and o is not None:
                    m = o / r * 100
                    tds.append(f'<td class="neg">−{abs(m):.1f}%</td>' if m < 0 else f'<td>{m:.1f}%</td>')
                else: tds.append('<td></td>')
        else:
            tds = [f'<td>{fmt(cell(V, y, row[2], EPS))}</td>' for y in ys]
        out.append(f'              <tr{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}><td>{label}</td>' + ''.join(tds) + '</tr>')
    out += ['            </tbody>', '          </table>']
    return '\n'.join(out)


def tables_html(values):
    V, EPS, years = values['values'], values['EPS'], [str(y) for y in values['years']]
    ys = sorted(years, reverse=True)
    fy = [f'FY{y}' for y in ys]
    T = lambda title, body, foot=None: (f'        <h4 style="margin-top:20px">{title}</h4>\n' if not title.startswith('Consolidated') and not title.startswith('Segment revenue') and not title.startswith('Segment operating income to') else f'        <h4>{title}</h4>\n') + '        <div class="tscroll">\n' + body + '\n        </div>\n' + (f'        <p class="tfoot">{foot}</p>\n' if foot else '')  # noqa: E731
    income = T('Consolidated statement of income · $ millions', table(['Line item'] + fy + [f'FY{ys[0][2:]} vs FY{ys[1][2:]}'] if len(ys) > 1 else ['Line item'] + fy, INCOME, V, EPS, years, 'income'),
               '† Derived for this chart; Disney does not report these subtotals. Operating income here excludes equity in the income of investees, which Disney includes in segment operating income.')
    seg = T('Segment revenue and operating income · $ millions', table(['Segment / revenue line'] + [f'Rev {f}' for f in fy] + [f'OI {f}' for f in fy], SEGMENTS, V, EPS, years, 'segments'),
            'Revenue lines follow the revenue note so every year is on one basis; the MD&amp;A groups part of retail sales with merchandise licensing. Sports line operating income (ESPN, Star) excludes equity in the income of investees, which is shown separately.')
    recon = T('Segment operating income to income before income taxes · $ millions', table(['Line item'] + fy, RECON, V, EPS, years))
    cash = T('Cash flow and capital allocation · $ millions', table(['Line item'] + fy, CASH, V, EPS, years),
             'From the consolidated statements of cash flows, the MD&amp;A production and programming activity tables and the capital-expenditure note. Each year is as presented in its own report; accepted differences between reports are listed in data/restatements.json.')
    eq = T('Retained earnings and treasury stock roll-forward · $ millions', table(['Line item'] + fy, EQUITY, V, EPS, years))
    head = ['Business']
    for f in fy: head += [f'Rev {f}', f'OI {f}', 'Margin']
    lines = T('Business line profitability · $ millions', table(head, LINES, V, EPS, years, 'lines'),
              'Entertainment lines include equity in the income of investees in their operating income; Sports shows it separately. From each report\'s MD&amp;A segment and supplemental tables.')
    cp = T('Consumer Products (inside Experiences, as reported) · $ millions', table(['Line item'] + fy, CP, V, EPS, years),
           'From the Experiences supplemental tables in the MD&amp;A. The FY2027 structure toggle moves the merchandise licensing revenue and a share of Consumer Products operating income from Experiences to Entertainment.')
    segexp = T('Segment expenses as reported · $ millions', table(['Segment / expense'] + fy, SEGEXP, V, EPS, years),
               'Significant segment expenses from the segment note of the first report that disclosed them (the 2025 report, for all three of its years).')
    cols = ['      <div>\n' + income + '      </div>', '      <div>\n' + seg + recon + cash + '      </div>', '      <div>\n' + eq + lines + cp + segexp + '      </div>']
    return '\n'.join(cols)


# ---------------------------------------------------------------------------------------------
def structure_check(template, values):
    """Every node the template draws has a value; every value the template cannot draw is zero."""
    drawn = set(re.findall(r"^    (\w+): \{.*\bv: D\('(\w+)'\)", template, re.M))
    drawn = {b for _, b in drawn} | set(re.findall(r"v: D\('(\w+)'\)", template))
    linked = set(re.findall(r"DL\('([\w>]+)'\)", template))
    problems = []
    for nid in drawn:
        if nid not in values['values']: problems.append(f'template draws {nid!r} but the pipeline produced no value for it')
    for key in linked:
        if key not in values['links']: problems.append(f'template needs link {key!r} but the pipeline produced no value for it')
    for nid, ys in values['values'].items():
        if nid not in drawn and any(ys.values()) and nid not in ('services', 'products', 'tsOth'):
            problems.append(f'{nid} is {ys} but the template has no block for it — add one before this can be drawn')
    return problems


def render_pages(sources):
    (SITE / 'pages').mkdir(parents=True, exist_ok=True)
    n = 0
    for key, info in sources['pages'].items():
        out = SITE / info['file']
        if out.exists(): continue
        pdf = ROOT / info['src']
        stem = out.with_suffix('')
        subprocess.run(['pdftoppm', '-r', '150', '-jpeg', '-jpegopt', 'quality=80', '-f', str(info['pdf']), '-l', str(info['pdf']), '-singlefile', str(pdf), str(stem)], check=True)
        n += 1
    return n


def main():
    values = json.load(open(ROOT / 'data' / 'values.json'))
    sources = json.load(open(ROOT / 'data' / 'sources.json'))
    template = (ROOT / 'template' / 'index.html').read_text()
    notes = (ROOT / 'template' / 'notes.html').read_text()
    problems = structure_check(template, values)
    if problems:
        print('build refused:\n  ' + '\n  '.join(problems)); sys.exit(1)
    years = values['years']; y0, yn = years[0], years[-1]
    data = {'years': years, 'fyEnd': values['fyEnd'], 'values': values['values'], 'links': values['links'], 'PF': values['PF'], 'EPS': values['EPS'],
            'sources': {'pages': {k: {kk: vv for kk, vv in v.items() if kk != 'src'} for k, v in sources['pages'].items()},
                        'src': {n: {y: [{'page': e['page'], 'rects': e['rects'], 'what': e['what']} for e in es] for y, es in ys.items()} for n, ys in sources['src'].items()},
                        'details': sources['details']}}
    blob = json.dumps(data, separators=(',', ':'), ensure_ascii=False).replace('</', '<\\/')
    html = template.replace('/*DATA*/{}/*END DATA*/', blob)
    buttons = '\n'.join(f'          <button type="button" data-y="{y}" tabindex="-1"{" aria-current=\"true\"" if y == yn else ""}>FY{y}</button>' for y in years)
    reps = {'{{Y0}}': str(y0), '{{YN}}': str(yn), '{{FY_END}}': values['fyEnd'][str(yn)], '{{NYEARS_WORD}}': WORDS.get(len(years), str(len(years))),
            '{{NMAX}}': str(len(years) - 1), '{{YEAR_BUTTONS}}': buttons.strip(),
            '{{YEARS_DESC_LIST}}': ', '.join(str(y) for y in years[::-1][:-1]) + ' and ' + str(y0) if len(years) > 1 else str(y0),
            '<!--TABLE SUMMARY-->': f'Table view: consolidated income statement, segment results, cash flow and segment expenses, FY{y0}–FY{yn}',
            '<!--TABLES-->': tables_html(values), '<!--NOTES-->': notes.rstrip('\n')}
    for k, v in reps.items():
        assert k in html, k
        html = html.replace(k, v)
    left = re.findall(r'\{\{\w+\}\}|<!--[A-Z ]+-->', html)
    if left: print('unfilled placeholders:', left); sys.exit(1)
    SITE.mkdir(exist_ok=True)
    (SITE / 'index.html').write_text(html)
    (SITE / '.nojekyll').write_text('')
    n = render_pages(sources)
    print(f'site/index.html ({len(html):,} bytes), {len(sources["pages"])} filing pages ({n} rendered now)')


if __name__ == '__main__':
    main()
