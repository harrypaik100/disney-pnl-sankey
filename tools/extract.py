#!/usr/bin/env python3
"""Extract the figures the model needs from a Disney Form 10-K PDF.

Usage:  python tools/extract.py filings/2025-10-K.pdf            (one filing)
        python tools/extract.py --all                             (every filings/*-10-K.pdf)

For each field in SPEC the script finds the table by its heading, the row by its label, and reads the
number in each fiscal-year column the table carries (a 10-K carries the current year and one or two
comparatives). It records the value, the PDF page and the bounding box of the printed number, so the
site can open the page with the figure highlighted. Output: extracted/<report>.json

Every field that cannot be found is reported; the exit code is non-zero if a required field is missing.
"""
import json, re, sys, os
from pathlib import Path
import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
NUM = re.compile(r'^\(?\$?-?\d{1,3}(,\d{3})*(\.\d+)?\)?$|^\(?\$?-?\d+(\.\d+)?\)?$')
DASH = {'—', '–'}  # em/en dash = zero; the ASCII hyphen is a separator ('operations - continuing')
YEAR = re.compile(r'^(20\d\d)$')


def to_number(tok):
    """'(52,677)' -> -52677 ; '12,003' -> 12003 ; '—' -> 0 ; '6.85' -> 6.85"""
    t = tok.replace('$', '').strip()
    if t in DASH: return 0
    neg = t.startswith('(') and t.endswith(')')
    t = t.strip('()').replace(',', '')
    v = float(t) if '.' in t else int(t)
    return -v if neg else v


class Doc:
    def __init__(self, path):
        self.path = str(path)
        self.pdf = pdfplumber.open(self.path)
        self._lines = {}
        self._text = {}
        self.n = len(self.pdf.pages)

    def text(self, pno):
        if pno not in self._text:
            self._text[pno] = re.sub(r'\s+', ' ', self.pdf.pages[pno - 1].extract_text() or '')
        return self._text[pno]

    def lines(self, pno):
        """Lines as lists of word dicts, top-to-bottom, left-to-right."""
        if pno not in self._lines:
            page = self.pdf.pages[pno - 1]
            ws = page.extract_words(x_tolerance=1.5, y_tolerance=2)
            ws.sort(key=lambda w: (round(w['top']), w['x0']))
            out, cur = [], []
            for w in ws:
                if cur and abs(w['top'] - cur[0]['top']) > 3:
                    out.append(cur); cur = []
                cur.append(w)
            if cur: out.append(cur)
            lines = []
            for l in out:
                l = sorted(l, key=lambda w: w['x0'])
                lines.append({'words': l, 'text': ' '.join(w['text'] for w in l), 'top': l[0]['top']})
            self._lines[pno] = lines
        return self._lines[pno]

    def printed_page(self, pno):
        t = (self.pdf.pages[pno - 1].extract_text() or '').strip().split('\n')
        last = t[-1].strip() if t else ''
        return int(last) if last.isdigit() else None

    def find_pages(self, anchors):
        """All pages containing every anchor; an anchor given as a tuple matches if any alternative is present."""
        anchors = [anchors] if isinstance(anchors, str) else anchors
        out = []
        for pno in range(1, self.n + 1):
            t = self.text(pno)
            if all((any(x in t for x in a) if isinstance(a, tuple) else a in t) for a in anchors):
                out.append(pno)
        return out

    def find_page(self, anchors):
        pages = self.find_pages(anchors)
        return pages[0] if pages else None

    def rect(self, pno, w, pad=2.5):
        p = self.pdf.pages[pno - 1]
        return [round((w['x0'] - pad) / p.width, 4), round((w['top'] - pad) / p.height, 4),
                round((w['x1'] - w['x0'] + 2 * pad) / p.width, 4), round((w['bottom'] - w['top'] + 2 * pad) / p.height, 4)]


def numeric_tokens(line):
    """Numeric word tokens on a line (with their word boxes), ignoring '$' and percent columns."""
    toks = []
    for w in line['words']:
        t = w['text']
        if t == '$' or t == '%' or t.endswith('%'): continue
        if t in DASH or NUM.match(t):
            toks.append((t, w))
    return toks


def label_of(line):
    """Text before the first numeric token (the row label)."""
    out = []
    for w in line['words']:
        t = w['text']
        if t == '$': break
        if t in DASH or NUM.match(t): break
        out.append(t)
    return ' '.join(out)


HEADER_WORDS = {'($', 'in', 'millions)', '(in', 'millions,', 'except', 'per', 'share', 'data)', 'Better', '(Worse)', '%', 'Change', 'As', 'of', 'Year', 'Ended', 'Fiscal'}


def header_years(lines, i):
    """Fiscal-year columns of the table a row belongs to: the nearest line above it with 2+ years,
    or a line that is a single year (segment tables)."""
    for j in range(i - 1, -1, -1):
        ys = [int(w['text']) for w in lines[j]['words'] if YEAR.match(w['text'])]
        words = [w['text'] for w in lines[j]['words']]
        others = [t for t in words if not YEAR.match(t)]
        # a header: 2+ fiscal years, no other numbers, and only a short caption around them
        if len(ys) >= 2 and not any(NUM.match(t) or t in DASH for t in others) and len(others) <= 8:
            return ys
        if len(ys) == 1 and len(lines[j]['words']) == 1:
            return ys
    return None


# ---------------------------------------------------------------------------------------------
# Field specification. Each field: a list of alternatives, tried in order.
#   page   : phrase(s) that identify the page (first page containing all of them)
#   after  : only consider lines after the first line containing this phrase (optional)
#   before : stop at the first line containing this phrase (optional)
#   row    : regex matched against the row label (case-insensitive)
#   kind   : 'years' (default: one number per fiscal-year column),
#            'seg'   (revenue note: columns Entertainment/Sports/Experiences/Eliminations/Total; 'col' picks one),
#            'equity'(statement of equity: 'col' picks the column; the year comes from the balance rows)
#   sum    : list of row regexes whose values are added (e.g. affiliate + subscription fees)
# ---------------------------------------------------------------------------------------------
IS = {'page': ['CONSOLIDATED STATEMENTS OF INCOME', 'Total revenues'], 'title': 'Consolidated Statements of Income'}
SEGNOTE = {'page': ['Third parties', 'Total segment operating income', ('Amounts eliminated in consolidation', 'Intersegment')], 'title': 'Segment note — segment revenues and operating income'}
RECON = {'page': ['reconciliation of segment operating income to income'], 'title': 'Segment note — reconciliation of segment operating income to income before income taxes'}
SEGEXP = {'page': ['Supplemental information about significant segment expenses'], 'title': 'Segment note — significant segment expenses'}
REVNOTE = {'page': ['revenues by segment and major source'], 'title': 'Revenue note — revenues by segment and major source'}
CF = {'page': ['CONSOLIDATED STATEMENTS OF CASH FLOWS'], 'title': 'Consolidated Statements of Cash Flows'}
EQ = {'page': ['Balance at', 'Comprehensive income', 'Retained'], 'title': 'Consolidated Statements of Shareholders’ Equity'}
CAPEX = {'page': ['Capital expenditures, depreciation expense and amortization'], 'title': 'Segment note — capital expenditures by segment'}
ENTMD = {'page': ['Revenue and operating results for', 'Linear Networks', 'Direct-to-Consumer'], 'title': 'MD&A — Entertainment segment results'}
SPORTSMD = {'page': ['Operating results for', ('the Sports segment are as follows', 'Sports are as follows')], 'title': 'MD&A — Sports segment results'}
SPORTSSUP = {'page': ['Supplemental revenue detail ESPN', 'Star', 'Supplemental operating income'], 'title': 'MD&A — Sports supplemental detail (ESPN / Star)'}
EXPMD = {'page': ['Operating results for', ('the Experiences segment are as follows', 'Experiences are as follows')], 'title': 'MD&A — Experiences segment results'}
EXPSUP = {'page': ['Supplemental revenue detail', 'Consumer Products', 'Supplemental operating income'], 'title': 'MD&A — Experiences supplemental detail (Parks / Consumer Products)'}
EXPOPEX = {'page': ['Operating labor', 'Infrastructure costs', 'Cost of goods sold and distribution costs'], 'title': 'MD&A — Experiences operating expenses'}
CONTENT = {'page': ['Licensed programming and rights', 'Produced content', ('Spending:', 'Spending')], 'title': 'MD&A — production and programming activity (content spend)'}

SPEC = {
    # consolidated statement of income
    'services': [dict(IS, row=r'^Services$')], 'products': [dict(IS, row=r'^Products$')],
    'total': [dict(IS, row=r'^Total revenues$')],
    'cos': [dict(IS, row=r'^Cost of services')], 'cop': [dict(IS, row=r'^Cost of products')],
    'sga': [dict(IS, row=r'^Selling, general, administrative and other$')],
    'da': [dict(IS, row=r'^Depreciation and amortization$')],
    'totalCosts': [dict(IS, row=r'^Total costs and expenses$')],
    'restr': [dict(IS, row=r'^Restructuring and impairment charges$')],
    'oth': [dict(IS, row=r'^Other income \(expense\), net$')],
    'int': [dict(IS, row=r'^Interest expense, net$')],
    'eq': [dict(IS, row=r'^Equity in the income of investees$')],
    'ptx': [dict(IS, row=r'^Income (from continuing operations )?before income taxes$')],
    'taxSigned': [dict(IS, row=r'^Income taxes( on continuing operations)?$')],
    'ni': [dict(IS, row=r'^Net income$')],
    'nci': [dict(IS, row=r'^Net income (from continuing operations )?attributable to noncontrolling'),
            dict(IS, after='Net income', row=r'^$', before='Net income attributable to The Walt')],   # label wraps to the next line in some years
    'dis': [dict(IS, row=r'^Net income attributable to The Walt Disney Company')],
    'epsDiluted': [dict(IS, after='Earnings (loss) per share attributable to Disney', row=r'^(Diluted|Continuing operations)$'),
                   dict(IS, after='Earnings per share attributable to Disney', row=r'^Diluted$')],
    # segment note
    'ent3p': [dict(SEGNOTE, label='Entertainment — revenue from third parties', after='Entertainment', row=r'^Third parties$')],
    'entInter': [dict(SEGNOTE, label='Entertainment — intersegment revenue', after='Entertainment', row=r'^(Amounts eliminated in consolidation|Intersegment)$')],
    'entRev': [dict(SEGNOTE, after='Entertainment', row=r'^$', before='Sports', label='Entertainment — total segment revenue')],
    'spo3p': [dict(SEGNOTE, label='Sports — revenue from third parties', after='Sports', row=r'^Third parties$')],
    'spoInter': [dict(SEGNOTE, label='Sports — intersegment revenue', after='Sports', row=r'^(Amounts eliminated in consolidation|Intersegment)$')],
    'spoRev': [dict(SEGNOTE, after='Sports', row=r'^$', before='Experiences', label='Sports — total segment revenue')],
    'expRev': [dict(SEGNOTE, label='Experiences — segment revenue', after='Third parties', row=r'^Experiences$', before='Segment operating income')],
    'elim': [dict(SEGNOTE, label='Eliminations of intersegment revenue', after='Third parties', row=r'^Eliminations$', before='Segment operating income (loss)')],
    'entOI': [dict(SEGNOTE, label='Entertainment — segment operating income', after='Segment operating income (loss)', row=r'^Entertainment$', before='Total segment operating income')],
    'spoOI': [dict(SEGNOTE, label='Sports — segment operating income', after='Segment operating income (loss)', row=r'^Sports$', before='Total segment operating income')],
    'expOI': [dict(SEGNOTE, label='Experiences — segment operating income', after='Segment operating income (loss)', row=r'^Experiences$', before='Total segment operating income')],
    'tsoi': [dict(SEGNOTE, row=r'^Total segment operating income')],
    'eqE': [dict(SEGNOTE, label='Entertainment — equity in the income of investees', after='Equity in the income of investees is included', row=r'^Entertainment$')],
    'eqS': [dict(SEGNOTE, label='Sports — equity in the income of investees', after='Equity in the income of investees is included', row=r'^Sports$')],
    'eqX': [dict(SEGNOTE, label='Experiences — equity in the income (loss) of investees', after='Equity in the income of investees is included', row=r'^Experiences$')],
    # reconciliation of segment OI to pre-tax income
    'corp': [dict(RECON, after='reconciliation of segment operating income', row=r'^Corporate and unallocated shared expenses$')],
    'india': [dict(RECON, after='reconciliation of segment operating income', row=r'^Equity in the loss of India joint venture$')],
    'restrS': [dict(RECON, after='reconciliation of segment operating income', row=r'^Restructuring and impairment charges')],
    'tfcf': [dict(RECON, after='reconciliation of segment operating income', row=r'^TFCF and Hulu acquisition amortization')],
    # significant segment expenses (first disclosed in the 2025 report)
    'ppc': [dict(SEGEXP, label='Entertainment — programming and production costs', after='Entertainment', row=r'^Programming and production costs$', before='Sports')],
    'oox': [dict(SEGEXP, label='Entertainment — other operating expense', after='Entertainment', row=r'^Other segment operating expenses', before='Sports')],
    'esga': [dict(SEGEXP, label='Entertainment — selling, general, administrative and other', after='Entertainment', row=r'^Selling, general, administrative and other$', before='Sports')],
    'eda': [dict(SEGEXP, label='Entertainment — depreciation and amortization', after='Entertainment', row=r'^Depreciation and amortization$', before='Sports')],
    'cE': [dict(SEGEXP, row=r'^Total Entertainment costs and expenses$')],
    'sppc': [dict(SEGEXP, label='Sports — programming and production costs', after='Total Entertainment costs', row=r'^Programming and production costs$', before='Experiences')],
    'sox': [dict(SEGEXP, label='Sports — other operating expense', after='Total Entertainment costs', row=r'^Other segment operating expenses', before='Experiences')],
    'ssga': [dict(SEGEXP, label='Sports — selling, general, administrative and other', after='Total Entertainment costs', row=r'^Selling, general, administrative and other$', before='Experiences')],
    'sda': [dict(SEGEXP, label='Sports — depreciation and amortization', after='Total Entertainment costs', row=r'^Depreciation and amortization$', before='Experiences')],
    'cS': [dict(SEGEXP, row=r'^Total Sports costs and expenses$')],
    'xlab': [dict(SEGEXP, label='Experiences — operating labor', after='Total Sports costs', row=r'^Operating labor$')],
    'xinf': [dict(SEGEXP, label='Experiences — infrastructure costs', after='Total Sports costs', row=r'^Infrastructure costs$')],
    'xcogs': [dict(SEGEXP, label='Experiences — cost of goods sold and distribution costs', after='Total Sports costs', row=r'^Costs of goods sold and distribution costs$')],
    'xoth': [dict(SEGEXP, label='Experiences — other operating expenses', after='Total Sports costs', row=r'^Other segment operating expenses')],
    'xsga': [dict(SEGEXP, label='Experiences — selling, general, administrative and other', after='Total Sports costs', row=r'^Selling, general, administrative and other$')],
    'xda': [dict(SEGEXP, label='Experiences — depreciation and amortization', after='Total Sports costs', row=r'^Depreciation and amortization$')],
    'cX': [dict(SEGEXP, row=r'^Total Experiences costs and expenses$')],
    # revenue note (segment columns: 0 Entertainment, 1 Sports, 2 Experiences, 3 Eliminations, 4 Total)
    'subAffS': [dict(REVNOTE, label='Subscription and affiliate fees (Sports column)', kind='seg', row=r'^Subscription and affiliate fees$', col=1),
                dict(REVNOTE, kind='seg', sum=[r'^Subscription fees$', r'^Affiliate fees$'], col=1)],
    'advS': [dict(REVNOTE, label='Advertising (Sports column)', kind='seg', row=r'^Advertising$', col=1)],
    'tvvodS': [dict(REVNOTE, label='TV/VOD distribution (Sports column)', kind='seg', row=r'^TV/VOD', col=1)],
    'otherS': [dict(REVNOTE, label='Other (Sports column)', kind='seg', row=r'^Other$', col=1)],
    'advX': [dict(REVNOTE, label='Advertising (Experiences column)', kind='seg', row=r'^Advertising$', col=2)],
    'tpa': [dict(REVNOTE, label='Theme park admissions (Experiences column)', kind='seg', row=r'^Theme park admissions$', col=2)],
    'mfb': [dict(REVNOTE, label='Retail and wholesale sales of merchandise, food and beverage (Experiences column)', kind='seg', row=r'^Retail and wholesale sales of merchandise', col=2)],
    'rv': [dict(REVNOTE, label='Resorts and vacations (Experiences column)', kind='seg', row=r'^Resort and vacations$', col=2)],
    'ml': [dict(REVNOTE, label='Merchandise licensing (Experiences column)', kind='seg', row=r'^Merchandise licensing$', col=2)],
    'otherX': [dict(REVNOTE, label='Other (Experiences column)', kind='seg', row=r'^Other$', col=2)],
    # MD&A: Entertainment lines
    'linRev': [dict(ENTMD, label='Linear Networks — revenue', after='Revenues', row=r'^Linear Networks$', before='operating income')],
    'dtcRev': [dict(ENTMD, label='Direct-to-Consumer — revenue', after='Revenues', row=r'^Direct-to-Consumer$', before='operating income')],
    'cslRev': [dict(ENTMD, label='Content Sales/Licensing and Other — revenue', after='Revenues', row=r'^Content Sales/Licensing and Other$', before='operating income')],
    'linOI': [dict(ENTMD, label='Linear Networks — operating income', after='Segment operating income', row=r'^Linear Networks$')],
    'dtcOI': [dict(ENTMD, label='Direct-to-Consumer — operating income (loss)', after='Segment operating income', row=r'^Direct-to-Consumer$')],
    'cslOI': [dict(ENTMD, label='Content Sales/Licensing and Other — operating income (loss)', after='Segment operating income', row=r'^Content Sales/Licensing and Other$')],
    # MD&A: Sports table and supplemental detail
    'safM': [dict(SPORTSMD, after='Operating results for', row=r'^Affiliate and subscription fees$'),
             dict(SPORTSMD, after='Operating results for', sum=[r'^Affiliate fees$', r'^Subscription fees$'], before='Operating expenses')],
    'sadM': [dict(SPORTSMD, after='Operating results for', row=r'^Advertising$', before='Operating expenses')],
    'sotM': [dict(SPORTSMD, after='Operating results for', row=r'^Other$', before='Operating expenses')],
    'espnDRev': [dict(SPORTSSUP, label='ESPN Domestic — revenue', after='Supplemental revenue detail', row=r'^Domestic$', before='Supplemental operating income')],
    'espnIRev': [dict(SPORTSSUP, label='ESPN International — revenue', after='Supplemental revenue detail', row=r'^International$', before='Supplemental operating income')],
    'starRev': [dict(SPORTSSUP, label='Star India — revenue', after='Supplemental revenue detail', row=r'^Star( \(India\)| India)$', before='Supplemental operating income')],
    'espnDOI': [dict(SPORTSSUP, label='ESPN Domestic — operating income', after='Supplemental operating income', row=r'^Domestic$')],
    'espnIOI': [dict(SPORTSSUP, label='ESPN International — operating income (loss)', after='Supplemental operating income', row=r'^International$')],
    'starOI': [dict(SPORTSSUP, label='Star India — operating income (loss)', after='Supplemental operating income', row=r'^Star( \(India\)| India)$')],
    # MD&A: Experiences table, operating expenses and supplemental detail
    'tpaM': [dict(EXPMD, row=r'^Theme park admissions$')],
    'rvM': [dict(EXPMD, row=r'^Resorts and vacations$')],
    'ploM': [dict(EXPMD, row=r'^Parks licensing and other$')],
    'xlabM': [dict(EXPOPEX, row=r'^Operating labor$')],
    'xinfM': [dict(EXPOPEX, row=r'^Infrastructure costs$')],
    'xcogsM': [dict(EXPOPEX, row=r'^Cost of goods sold and distribution costs$')],
    'xothM': [dict(EXPOPEX, row=r'^Other operating expenses$')],
    'parksDRev': [dict(EXPSUP, label='Parks & Experiences Domestic — revenue', after='Supplemental revenue detail', row=r'^Domestic$', before='Supplemental operating income')],
    'parksIRev': [dict(EXPSUP, label='Parks & Experiences International — revenue', after='Supplemental revenue detail', row=r'^International$', before='Supplemental operating income')],
    'cpRev': [dict(EXPSUP, label='Consumer Products — revenue', after='Supplemental revenue detail', row=r'^Consumer Products$', before='Supplemental operating income')],
    'parksDOI': [dict(EXPSUP, label='Parks & Experiences Domestic — operating income', after='Supplemental operating income', row=r'^Domestic$')],
    'parksIOI': [dict(EXPSUP, label='Parks & Experiences International — operating income', after='Supplemental operating income', row=r'^International$')],
    'cpOI': [dict(EXPSUP, label='Consumer Products — operating income', after='Supplemental operating income', row=r'^Consumer Products$')],
    # statement of cash flows
    'cfo': [dict(CF, row=r'^Cash provided by operations( - continuing operations)?$')],
    'cfi': [dict(CF, row=r'^Cash (used in|provided by|\(used in\) provided by|provided by \(used in\)) investing activities( - continuing operations)?$')],
    'cff': [dict(CF, row=r'^Cash (used in|provided by|\(used in\) provided by|provided by \(used in\)) financing activities( - continuing operations)?$')],
    'cfDisc': [dict(CF, row=r'^Cash (used in|provided by|\(used in\) provided by|provided by \(used in\)) discontinued operations$')],
    'capexCF': [dict(CF, row=r'^Investments in parks, resorts and other property$')],
    'proceedsInv': [dict(CF, row=r'^Proceeds from sales of investments$')],
    'purchaseInv': [dict(CF, row=r'^Purchase of investments$')],
    'othInv': [dict(CF, after='INVESTING ACTIVITIES', row=r'^Other, net$', before='FINANCING ACTIVITIES', label='Investing activities — Other, net')],
    'cpNet': [dict(CF, row=r'^Commercial paper (borrowings \(payments\)|payments), net$')],
    'borrowings': [dict(CF, row=r'^Borrowings$')],
    'reduction': [dict(CF, row=r'^Reduction of borrowings$')],
    'dividendsCF': [dict(CF, after='FINANCING ACTIVITIES', row=r'^Dividends$')],
    'repurchasesCF': [dict(CF, after='FINANCING ACTIVITIES', row=r'^Repurchases of common stock$')],
    'contributions': [dict(CF, row=r'^Contributions from( / sales of)? noncontrolling interests$')],
    'acqRNCI': [dict(CF, row=r'^Acquisition of redeemable noncontrolling interests$')],
    'optionProceeds': [dict(CF, after='FINANCING ACTIVITIES', row=r'^Proceeds from exercise of stock options$')],
    'othFin': [dict(CF, after='FINANCING ACTIVITIES', row=r'^Other, net$', label='Financing activities — Other, net')],
    'fx': [dict(CF, row=r'^Impact of exchange rates on cash')],
    'changeCash': [dict(CF, row=r'^Change in cash, cash equivalents and restricted cash$')],
    # content spend (MD&A liquidity)
    'licSpend': [dict(CONTENT, after='Spending', row=r'^Licensed programming and rights$', before='Amortization')],
    'prodSpend': [dict(CONTENT, after='Spending', row=r'^Produced content$', before='Amortization')],
    # capital expenditures by segment (note)
    'cxE': [dict(CAPEX, label='Entertainment — capital expenditures', after='Capital expenditures', row=r'^Entertainment$', before='Depreciation expense')],
    'cxS': [dict(CAPEX, label='Sports — capital expenditures', after='Capital expenditures', row=r'^Sports$', before='Depreciation expense')],
    'cxD': [dict(CAPEX, label='Experiences Domestic — capital expenditures', after='Capital expenditures', row=r'^Domestic$', before='Depreciation expense')],
    'cxI': [dict(CAPEX, label='Experiences International — capital expenditures', after='Capital expenditures', row=r'^International$', before='Depreciation expense')],
    'cxC': [dict(CAPEX, label='Corporate — capital expenditures', after='Capital expenditures', row=r'^Corporate$', before='Depreciation expense')],
    'capexTotal': [dict(CAPEX, row=r'^Total capital expenditures$')],
    # statement of equity (columns: 0 shares, 1 common stock, 2 retained earnings, 3 AOCI, 4 treasury stock, 5 total Disney, 6 NCI, 7 total)
    'reEnd': [dict(EQ, kind='equity', row=r'^Balance at', col=2)],
    'reNI': [dict(EQ, kind='equity', row=r'^Comprehensive income', col=2)],
    'reDiv': [dict(EQ, kind='equity', row=r'^Dividends$', col=2)],
    'reOth': [dict(EQ, kind='equity', row=r'^Distributions and other$', col=2, label='Distributions and other — retained earnings column')],
    'tsEnd': [dict(EQ, kind='equity', row=r'^Balance at', col=4)],
    'tsRep': [dict(EQ, kind='equity', row=r'^Common stock repurchases$', col=4)],
    'tsOth': [dict(EQ, kind='equity', row=r'^Distributions and other$', col=4, label='Distributions and other — treasury stock column')],
}
OPTIONAL = {'india', 'cfDisc', 'purchaseInv', 'optionProceeds', 'dividendsCF', 'repurchasesCF', 'reDiv', 'tsRep', 'tsOth', 'advX',
            'ppc', 'oox', 'esga', 'eda', 'cE', 'sppc', 'sox', 'ssga', 'sda', 'cS', 'xlab', 'xinf', 'xcogs', 'xoth', 'xsga', 'xda', 'cX'}  # absent in some years/reports

PAGE_LABELS = [
    ('CONSOLIDATED STATEMENTS OF INCOME', 'Consolidated Statements of Income'),
    ('CONSOLIDATED STATEMENTS OF CASH FLOWS', 'Consolidated Statements of Cash Flows'),
    ('Balance at', 'Consolidated Statements of Equity'),
    ('Segment revenues and segment operating income are as follows', 'Note 1 · Segment revenues and operating income'),
    ('Supplemental information about significant segment expenses', 'Note 1 · Significant segment expenses'),
    ('reconciliation of segment operating income to income', 'Note 1 · Reconciliation of segment operating income to income before income taxes'),
    ('Capital expenditures, depreciation expense and amortization', 'Note 1 · Capital expenditures, depreciation and amortization by segment'),
    ('revenues by segment and major source', 'Note 3 · Revenues by segment and major source'),
    ('Revenue and operating results for the Entertainment segment', 'MD&A · Entertainment segment results'),
    ('Operating results for the Sports segment', 'MD&A · Sports segment results'),
    ('supplemental revenue and operating income detail for the Sports segment', 'MD&A · Sports supplemental revenue and operating income'),
    ('Operating results for the Experiences segment', 'MD&A · Experiences segment results'),
    ('supplemental revenue and operating income detail for the Experiences segment', 'MD&A · Experiences supplemental revenue and operating income'),
    ('Operating labor', 'MD&A · Experiences operating expenses'),
    ('production and programming activity', 'MD&A · Liquidity: production and programming activity (content spend)'),
]


def page_label(doc, pno):
    t = doc.text(pno)
    labels = [lab for key, lab in PAGE_LABELS if key in t]
    return ' / '.join(labels[:2]) if labels else 'Form 10-K'


def extract_field(doc, name, alts):
    """Returns {year: {'value', 'page', 'rect'}} or None."""
    for spec in alts:
        for pno in doc.find_pages(spec['page']):
            if spec.get('kind') == 'seg':
                res = extract_seg(doc, pno, spec)
            elif spec.get('kind') == 'equity':
                res = extract_equity(doc, pno, spec)
            else:
                res = extract_years(doc, pno, spec)
            if res:
                for v in res.values():
                    v['table'] = spec.get('title', '')
                    if spec.get('label'): v['label'] = spec['label']
                    v.setdefault('label', re.sub(r'[\^$()?]', '', spec.get('row', '') or ' + '.join(spec.get('sum', []))))
                return res
    return None


def row_values(doc, pno, lines, i, years):
    """Values and rects for the numeric tokens of line i, one per year column."""
    toks = numeric_tokens(lines[i])
    if len(toks) < len(years): return None
    out = {}
    for y, (t, w) in zip(years, toks[:len(years)]):
        out[y] = {'value': to_number(t), 'page': pno, 'rect': doc.rect(pno, w), 'label': label_of(lines[i])}
    return out


def match_rows(lines, spec):
    """Indices of lines whose label matches spec['row'] within the after/before window."""
    start, end = 0, len(lines)
    if spec.get('after'):
        for k, l in enumerate(lines):
            if spec['after'].lower() in l['text'].lower(): start = k + 1; break
        else:
            return []
    if spec.get('before'):
        for k in range(start, len(lines)):
            if spec['before'].lower() in lines[k]['text'].lower(): end = k; break
    rx = re.compile(spec['row'], re.I)
    return [k for k in range(start, end) if rx.search(label_of(lines[k])) and numeric_tokens(lines[k])]


def extract_years(doc, pno, spec):
    lines = doc.lines(pno)
    if spec.get('sum'):
        parts = []
        for rx in spec['sum']:
            idx = match_rows(lines, dict(spec, row=rx))
            if not idx: return None
            years = header_years(lines, idx[0])
            if not years: return None
            rv = row_values(doc, pno, lines, idx[0], years)
            if not rv: return None
            parts.append(rv)
        out = {}
        for y in parts[0]:
            if all(y in p for p in parts):
                out[y] = {'value': sum(p[y]['value'] for p in parts), 'page': pno, 'rect': parts[0][y]['rect'], 'rects': [p[y]['rect'] for p in parts], 'values': [p[y]['value'] for p in parts]}
        return out or None
    idx = match_rows(lines, spec)
    if not idx: return None
    i = idx[0]
    years = header_years(lines, i)
    if not years: return None
    return row_values(doc, pno, lines, i, years)


def seg_col(toks, cols, col):
    """The numeric token sitting under column `col` (nearest right edge); None if the row has no figure there."""
    best = None
    for t, w in toks:
        j = min(range(len(cols)), key=lambda i: abs(w['x1'] - cols[i]) if cols[i] is not None else 1e9)
        if j == col and (best is None or abs(w['x1'] - cols[j]) < abs(best[1]['x1'] - cols[j])):
            best = (t, w)
    return best


def extract_seg(doc, pno, spec):
    """Revenue note: one table per year, each headed by a lone year line; columns are segments."""
    out = {}
    pages = [pno] + ([pno + 1] if pno + 1 <= doc.n and 'major source' not in doc.text(pno + 1) and any(YEAR.match(w['text']) and len(l['words']) == 1 for l in doc.lines(pno + 1) for w in l['words']) else [])
    for p in pages:
        lines = doc.lines(p)
        year, cols = None, None
        for k, l in enumerate(lines):
            ws = [w['text'] for w in l['words']]
            if 'Experiences' in ws and 'Sports' in ws and 'Entertainment' in ws:
                # column header: right edges of Entertainment / Sports / Experiences / Eliminations / Total
                xs = {w['text']: w['x1'] for w in l['words']}
                cols = [xs['Entertainment'], xs['Sports'], xs['Experiences'], xs.get('Eliminations', xs.get('Other')), xs['Total']]
                continue
            if len(l['words']) == 1 and YEAR.match(l['text']):
                year = int(l['text']); continue
            if year is None or cols is None: continue
            if spec.get('sum'):
                vals, rects = [], []
                for rx in spec['sum']:
                    for m in range(k, min(k + 15, len(lines))):
                        if re.search(rx, label_of(lines[m]), re.I):
                            tw = seg_col(numeric_tokens(lines[m]), cols, spec['col'])
                            if tw:
                                vals.append(to_number(tw[0])); rects.append(doc.rect(p, tw[1])); break
                if len(vals) == len(spec['sum']) and year not in out:
                    out[year] = {'value': sum(vals), 'page': p, 'rect': rects[0], 'rects': rects, 'values': vals}
                continue
            if re.search(spec['row'], label_of(l), re.I):
                tw = seg_col(numeric_tokens(l), cols, spec['col'])
                if tw and year not in out:
                    out[year] = {'value': to_number(tw[0]), 'page': p, 'rect': doc.rect(p, tw[1])}
    return out or None


def extract_equity(doc, pno, spec):
    """Statement of equity: rows between 'Balance at <date>' lines belong to the fiscal year of the closing balance."""
    pages = [pno] + ([pno + 1] if pno + 1 <= doc.n and 'Balance at' in doc.text(pno + 1) else [])
    out, pending = {}, []
    bal = re.compile(r'^Balance at \w+ \d+, (20\d\d)')
    for p in pages:
        for l in doc.lines(p):
            lab = label_of(l); toks = numeric_tokens(l)
            m = bal.match(l['text'])
            if m:
                fy = int(m.group(1))
                if toks and YEAR.match(toks[0][0]): toks = toks[1:]  # the date's year is not a column
                if re.search(spec['row'], 'Balance at', re.I) and len(toks) > spec['col']:
                    t, w = toks[spec['col']]; out[fy] = {'value': to_number(t), 'page': p, 'rect': doc.rect(p, w), 'label': m.group(0) + (' — retained earnings' if spec['col'] == 2 else ' — treasury stock' if spec['col'] == 4 else '')}
                for (lab2, toks2, p2) in pending:
                    if fy not in out and len(toks2) > spec['col']:
                        t, w = toks2[spec['col']]; out[fy] = {'value': to_number(t), 'page': p2, 'rect': doc.rect(p2, w)}
                pending = []
                continue
            if re.search(spec['row'], lab, re.I) and toks and not spec['row'].startswith('^Balance'):
                pending.append((lab, toks, p))
    return out or None


def extract(path):
    doc = Doc(path)
    report = Path(path).stem
    m = re.search(r'fiscal year ended (\w+ \d+, (20\d\d))', ' '.join(doc.text(p) for p in range(1, min(7, doc.n) + 1)))
    fy = int(m.group(2)) if m else None
    fields, missing = {}, []
    for name, alts in SPEC.items():
        res = extract_field(doc, name, alts)
        if res:
            fields[name] = {str(y): v for y, v in res.items()}
        elif name not in OPTIONAL:
            missing.append(name)
    pages = sorted({v['page'] for f in fields.values() for v in f.values()})
    pageinfo = {str(p): {'printed': doc.printed_page(p), 'label': page_label(doc, p), 'w': round(float(doc.pdf.pages[p - 1].width)), 'h': round(float(doc.pdf.pages[p - 1].height))} for p in pages}
    return {'report': report, 'fiscalYear': fy, 'fiscalYearEnd': m.group(1) if m else None, 'fields': fields, 'pages': pageinfo}, missing


def main():
    args = sys.argv[1:]
    paths = sorted((ROOT / 'filings').glob('*-10-K.pdf')) if '--all' in args else [Path(a) for a in args if not a.startswith('--')]
    if not paths:
        print(__doc__); sys.exit(2)
    (ROOT / 'extracted').mkdir(exist_ok=True)
    bad = False
    for p in paths:
        data, missing = extract(p)
        out = ROOT / 'extracted' / (data['report'] + '.json')
        json.dump(data, open(out, 'w'), indent=1)
        nvals = sum(len(f) for f in data['fields'].values())
        print(f"{data['report']}: fiscal {data['fiscalYear']} (ended {data['fiscalYearEnd']}), {len(data['fields'])} fields, {nvals} figures, {len(data['pages'])} pages -> {out.relative_to(ROOT)}")
        if missing:
            bad = True
            print('  MISSING required fields: ' + ', '.join(missing))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
