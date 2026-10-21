#!/usr/bin/env python3
"""xbrl.py — cross-check the consolidated figures against Disney's XBRL facts at the SEC.

    python3 tools/xbrl.py                 # fetch https://data.sec.gov/api/xbrl/companyfacts/CIK0001744489.json and compare
    python3 tools/xbrl.py --require       # exit 1 unless every comparable figure matches (CI)
    python3 tools/xbrl.py --offline       # compare against the facts cached in data/xbrl-cache.json

The SEC asks for a descriptive User-Agent with a contact address: set SEC_USER_AGENT="project (you@example.com)".
Segment and business-line figures carry XBRL dimensions that the companyfacts API does not expose, so this
check covers the consolidated statements (income, cash flow, equity); the segment figures are verified
against the filing text by verify.py. Writes data/xbrl-report.md.
"""
import json, os, re, sys, urllib.request
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CIK = '0001744489'
URL = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json'
CACHE = ROOT / 'data' / 'xbrl-cache.json'

# node -> (candidate us-gaap tags, instant?, sign)  sign: +1 compare as is, -1 the XBRL fact is the negative, 'abs' compare magnitudes
CHECKS = {
    'total': (['Revenues'], False, 1),
    'cos': (['CostOfServicesExcludingDepreciationDepletionAndAmortization', 'CostOfServices'], False, 1),
    'cop': (['CostOfGoodsSoldExcludingDepreciationDepletionAndAmortization', 'CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization', 'CostOfGoodsSold'], False, 1),
    'sga': (['SellingGeneralAndAdministrativeExpense'], False, 1),
    'da': (['DepreciationDepletionAndAmortization', 'DepreciationAndAmortization'], False, 1),
    'restr': (['RestructuringSettlementAndImpairmentProvisions', 'RestructuringCharges', 'RestructuringCostsAndAssetImpairmentCharges'], False, 1),
    'int': (['InterestIncomeExpenseNonoperatingNet', 'InterestExpenseNonoperating', 'InterestExpense'], False, 'abs'),
    'eq': (['IncomeLossFromEquityMethodInvestments'], False, 1),
    'ptx': (['IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest',
             'IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments'], False, 1),
    'taxNet': (['IncomeTaxExpenseBenefit'], False, 1),
    'ni': (['ProfitLoss'], False, 1),
    'nci': (['NetIncomeLossAttributableToNoncontrollingInterest'], False, 1),
    'dis': (['NetIncomeLoss'], False, 1),
    'eps': (['EarningsPerShareDiluted'], False, 1),
    'cfo': (['NetCashProvidedByUsedInOperatingActivitiesContinuingOperations', 'NetCashProvidedByUsedInOperatingActivities'], False, 1),
    'capex': (['PaymentsToAcquirePropertyPlantAndEquipment'], False, 1),
    'buyb': (['PaymentsForRepurchaseOfCommonStock'], False, 1),
    'divs': (['PaymentsOfDividendsCommonStock', 'PaymentsOfDividends'], False, 1),
    'hulu': (['PaymentsToAcquireAdditionalInterestInSubsidiaries', 'PaymentsForRepurchaseOfRedeemableNoncontrollingInterest'], False, 1),
    'reEnd': (['RetainedEarningsAccumulatedDeficit'], True, 1),
    'tsEnd': (['TreasuryStockValue', 'TreasuryStockCommonValue'], True, 1),
}
# site value to compare, per check (the XBRL facts are in dollars; the site is in $ millions)
def site_value(V, EPS, key, y):
    if key == 'eps': return EPS[y]
    if key == 'taxNet': return V['taxexp'][y] - V['tax'][y]
    return V[key][y]


def fetch():
    ua = os.environ.get('SEC_USER_AGENT') or 'disney-pnl-sankey build (set SEC_USER_AGENT to your contact address)'
    req = urllib.request.Request(URL, headers={'User-Agent': ua, 'Accept-Encoding': 'gzip, deflate'})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        if r.headers.get('Content-Encoding') == 'gzip':
            import gzip; raw = gzip.decompress(raw)
    return json.loads(raw)


def fy_end_date(s):
    return datetime.strptime(s, '%B %d, %Y').date()


def pick(facts, tags, fy_end, instant):
    """The 10-K fact for a fiscal year: ends on the fiscal year-end date and, for durations, spans about a year."""
    for tag in tags:
        units = facts.get('us-gaap', {}).get(tag, {}).get('units', {})
        for unit in ('USD', 'USD/shares'):
            cands = []
            for f in units.get(unit, []):
                if f.get('form') != '10-K' or f.get('end') != fy_end.isoformat(): continue
                if instant:
                    if 'start' in f and f['start']: continue
                else:
                    if not f.get('start'): continue
                    days = (date.fromisoformat(f['end']) - date.fromisoformat(f['start'])).days
                    if not 350 <= days <= 380: continue
                cands.append(f)
            if cands:
                # prefer the fact from the filing of that fiscal year (fy == year), then the latest filed
                cands.sort(key=lambda f: (f.get('fy') == fy_end.year, f.get('filed', '')), reverse=True)
                return tag, unit, cands[0]
    return None


def compare(facts, values):
    V, EPS = values['values'], values['EPS']
    rows, problems, missing = [], [], []
    for y in values['years']:
        y = str(y); fy_end = fy_end_date(values['fyEnd'][y])
        for key, (tags, instant, sign) in CHECKS.items():
            want = site_value(V, EPS, key, y)
            hit = pick(facts, tags, fy_end, instant)
            if not hit:
                missing.append(f'FY{y} {key}: no 10-K fact for {tags} ending {fy_end}')
                rows.append((y, key, want, None, None, 'no fact')); continue
            tag, unit, f = hit
            got = f['val'] if unit == 'USD/shares' else f['val'] / 1e6
            if sign == 'abs': ok = abs(round(got, 2)) == abs(want)
            else: ok = round(sign * got, 2) == want
            rows.append((y, key, want, round(got, 2), tag, 'match' if ok else 'MISMATCH'))
            if not ok: problems.append(f'FY{y} {key}: site {want:,} vs XBRL {tag} {got:,.2f} (filed {f.get("filed")}, accession {f.get("accn")})')
    return rows, problems, missing


def main():
    args = sys.argv[1:]
    values = json.load(open(ROOT / 'data' / 'values.json'))
    if '--offline' in args:
        if not CACHE.exists(): sys.exit('no data/xbrl-cache.json — run without --offline first')
        facts = json.load(open(CACHE)); fetched = f'cache ({CACHE.name})'
    else:
        try:
            full = fetch()
        except Exception as e:  # noqa: BLE001
            msg = f'could not fetch {URL}: {e}'
            if '--require' in args: sys.exit('XBRL check FAILED: ' + msg)
            print('XBRL check skipped: ' + msg); return
        # keep only the tags the checks use, so the cache stays small
        tags = {t for tags, _, _ in CHECKS.values() for t in tags}
        facts = {'us-gaap': {t: full['us-gaap'][t] for t in tags if t in full.get('us-gaap', {})}, 'entityName': full.get('entityName'), 'fetched': date.today().isoformat()}
        CACHE.parent.mkdir(exist_ok=True)
        json.dump(facts, open(CACHE, 'w'))
        fetched = f'{URL} on {facts["fetched"]}'
    rows, problems, missing = compare(facts, values)
    n_match = sum(1 for r in rows if r[5] == 'match')
    lines = ['# XBRL cross-check', '', f'Facts: {fetched}', '',
             f"**Result: {'PASS' if not problems else f'FAIL ({len(problems)} mismatches)'}** — {n_match} figures match, {len(missing)} not comparable", '',
             '| FY | figure | site ($M) | XBRL ($M) | tag | result |', '|---|---|---:|---:|---|---|']
    for y, key, want, got, tag, res in rows:
        lines.append(f'| {y} | {key} | {want:,} | {"" if got is None else f"{got:,}"} | {tag or ""} | {res} |')
    if missing: lines += ['', '## Not comparable', ''] + [f'- {m}' for m in missing]
    if problems: lines += ['', '## Mismatches', ''] + [f'- {p}' for p in problems]
    (ROOT / 'data' / 'xbrl-report.md').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines[:5]))
    if problems or (('--require' in args) and n_match == 0):
        sys.exit(1)


if __name__ == '__main__':
    main()
