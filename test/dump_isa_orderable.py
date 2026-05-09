"""ISA 주문가능 510,632원 추적용 진단 스크립트 (확장판).

실험 A: 종목별 nrcvb_buy_amt 변동성
실험 B: 주문구분(시장가/지정가) 변동성
실험 C: 9,053 분해 검산

코드 변경 없이 진단만 수행 (test/ 폴더 내 1회성).
"""
import sys
from pathlib import Path

current_dir = Path(__file__).resolve()
project_root = current_dir.parent.parent
sys.path.append(str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

from src.kis.client import KISClient


def _fmt(n) -> str:
    try:
        return f'{int(n):>12,}'
    except (ValueError, TypeError):
        return f'{str(n):>12}'


def _to_int(v) -> int:
    try:
        return int(str(v))
    except (ValueError, TypeError):
        return 0


def baseline_dump(c: KISClient) -> dict:
    """[1] 잔고조회 output2 + 시장가 매수가능 (기준 ticker) 풀 덤프."""
    print('=== inquire-balance output2 (full) ===')
    page = c._domestic_balance_page()
    output2 = page['output2'][0]
    for k, v in output2.items():
        print(f'  {k:30s}: {v}')

    output1 = page['output1']
    enable_market = None
    sample_ticker = None
    if output1:
        sample_ticker = output1[0]['pdno']
        print(f'\n=== inquire-psbl-order (TTTC8908R) for ticker={sample_ticker} ===')
        enable_market = c.fetch_domestic_enable_buy(ticker=sample_ticker, ord_dvsn='01')
        for k, v in enable_market.items():
            print(f'  {k:30s}: {v}')

    return {
        'output1': output1,
        'output2': output2,
        'enable_market': enable_market,
        'sample_ticker': sample_ticker,
    }


def experiment_a(c: KISClient, output1: list) -> list:
    """실험 A — 여러 종목 시장가 매수가능 변동성."""
    print('\n\n=== 실험 A: 종목별 nrcvb_buy_amt 변동성 (시장가 ord_dvsn=01) ===')
    holding_tickers = [row['pdno'] for row in output1[:5]]
    extra_tickers = ['005930', '069500', '360750']
    tickers = holding_tickers + extra_tickers

    print(f'대상 ticker ({len(tickers)}개): {tickers}\n')

    rows = []
    header = (f'{"ticker":<8} | {"calc_unpr":>10} | {"max_qty":>7} | '
              f'{"nrcvb_buy_amt":>14} | {"max_buy_amt":>12} | {"qty×unpr":>11} | '
              f'{"nrcvb−qty×unpr":>15} | {"ord_psbl_cash":>13} | {"ruse_psbl_amt":>13}')
    print(header)
    print('-' * len(header))

    for t in tickers:
        try:
            r = c.fetch_domestic_enable_buy(ticker=t, ord_dvsn='01')
        except Exception as e:
            print(f'{t:<8} | ERROR: {e}')
            continue
        calc_unpr = _to_int(r.get('psbl_qty_calc_unpr', 0))
        max_qty = _to_int(r.get('max_buy_qty', 0))
        nrcvb = _to_int(r.get('nrcvb_buy_amt', 0))
        max_buy = _to_int(r.get('max_buy_amt', 0))
        opc = _to_int(r.get('ord_psbl_cash', 0))
        ruse = _to_int(r.get('ruse_psbl_amt', 0))
        qty_x_unpr = calc_unpr * max_qty
        residual = nrcvb - qty_x_unpr
        rows.append({
            'ticker': t,
            'calc_unpr': calc_unpr,
            'max_qty': max_qty,
            'nrcvb_buy_amt': nrcvb,
            'max_buy_amt': max_buy,
            'qty_x_unpr': qty_x_unpr,
            'residual': residual,
            'ord_psbl_cash': opc,
            'ruse_psbl_amt': ruse,
        })
        print(f'{t:<8} | {_fmt(calc_unpr)} | {max_qty:>7} | '
              f'{_fmt(nrcvb)} | {_fmt(max_buy)} | {_fmt(qty_x_unpr)} | '
              f'{_fmt(residual)} | {_fmt(opc)} | {_fmt(ruse)}')

    return rows


def experiment_b(c: KISClient) -> list:
    """실험 B — ord_dvsn 변동성 (시장가 vs 지정가 95/100/105%)."""
    print('\n\n=== 실험 B: ord_dvsn 변동성 (ticker=005930) ===')

    # 현재가 조회
    price_resp = c.fetch_domestic_price('J', '005930')
    cur_price = _to_int(price_resp.get('output', {}).get('stck_prpr', 0))
    print(f'005930 현재가 (stck_prpr): {cur_price:,}원\n')

    if cur_price == 0:
        print('현재가 조회 실패 → 실험 B 스킵')
        return []

    cases = [
        ('01-시장가', '01', -1),
        ('00-지정가-095%', '00', int(cur_price * 0.95)),
        ('00-지정가-100%', '00', cur_price),
        ('00-지정가-105%', '00', int(cur_price * 1.05)),
    ]

    header = (f'{"case":<18} | {"price":>8} | {"calc_unpr":>10} | {"max_qty":>7} | '
              f'{"nrcvb_buy_amt":>14} | {"qty×unpr":>11} | {"residual":>11}')
    print(header)
    print('-' * len(header))

    rows = []
    for label, dvsn, p in cases:
        try:
            r = c.fetch_domestic_enable_buy(ticker='005930', ord_dvsn=dvsn, price=p)
        except Exception as e:
            print(f'{label:<18} | ERROR: {e}')
            continue
        calc_unpr = _to_int(r.get('psbl_qty_calc_unpr', 0))
        max_qty = _to_int(r.get('max_buy_qty', 0))
        nrcvb = _to_int(r.get('nrcvb_buy_amt', 0))
        qty_x_unpr = calc_unpr * max_qty
        residual = nrcvb - qty_x_unpr
        rows.append({
            'case': label, 'price': p, 'calc_unpr': calc_unpr, 'max_qty': max_qty,
            'nrcvb': nrcvb, 'qty_x_unpr': qty_x_unpr, 'residual': residual,
            'full': r,
        })
        print(f'{label:<18} | {p if p > 0 else 0:>8,} | {_fmt(calc_unpr)} | {max_qty:>7} | '
              f'{_fmt(nrcvb)} | {_fmt(qty_x_unpr)} | {_fmt(residual)}')

    # 지정가 시 ord_psbl_cash, ruse_psbl_amt 도 출력
    print('\n  (보너스) 각 case의 ord_psbl_cash / ruse_psbl_amt / max_buy_amt:')
    for r in rows:
        full = r['full']
        print(f'    {r["case"]:<18}: ord_psbl_cash={_to_int(full.get("ord_psbl_cash",0)):>10,}, '
              f'ruse_psbl_amt={_to_int(full.get("ruse_psbl_amt",0)):>10,}, '
              f'max_buy_amt={_to_int(full.get("max_buy_amt",0)):>10,}')

    return rows


def experiment_c(output2: dict, exp_a: list, exp_b: list) -> None:
    """실험 C — 9,053 분해 검산."""
    print('\n\n=== 실험 C: 분해 검산 ===')

    dnca = _to_int(output2.get('dnca_tot_amt', 0))
    nxdy = _to_int(output2.get('nxdy_excc_amt', 0))
    prvs = _to_int(output2.get('prvs_rcdl_excc_amt', 0))
    print(f'기준값: D+0={dnca:,}, D+1={nxdy:,}, D+2={prvs:,}\n')

    # C1: nrcvb_buy_amt가 ticker마다 다른가?
    nrcvb_set = {r['nrcvb_buy_amt'] for r in exp_a}
    print(f'[C1] 종목별 nrcvb_buy_amt 고유값 개수: {len(nrcvb_set)}')
    if len(nrcvb_set) == 1:
        print(f'  ✅ 모든 종목에서 동일 ({list(nrcvb_set)[0]:,}) → 계좌 단위 상수')
    else:
        print(f'  ❌ ticker마다 다름 → 종목·단가 의존')
        sorted_vals = sorted(nrcvb_set)
        print(f'  min={sorted_vals[0]:,}, max={sorted_vals[-1]:,}, '
              f'spread={sorted_vals[-1]-sorted_vals[0]:,}')

    # C2: residual (nrcvb - qty×unpr) 패턴
    print('\n[C2] residual = nrcvb_buy_amt − (max_qty × calc_unpr) 패턴:')
    residuals = [(r['ticker'], r['residual'], r['calc_unpr'], r['max_qty']) for r in exp_a]
    for t, res, unpr, qty in residuals:
        # residual / unpr (다음 1주가 안 들어간 이유)
        ratio = res / unpr if unpr else 0
        print(f'  {t:<8}: residual={res:>10,}원  (= {ratio:.4f} × calc_unpr; 1주 부족분 직관)')
    print('  → residual이 항상 < calc_unpr 이면 \'1주 추가 매수 시 nrcvb 초과\' 라는 정수 라운딩 잔여')

    # C3: nrcvb + ruse 가 의미 있는 합계인지
    print('\n[C3] nrcvb_buy_amt + ruse_psbl_amt 합계 검사:')
    for r in exp_a:
        s = r['nrcvb_buy_amt'] + r['ruse_psbl_amt']
        flags = []
        if s == dnca: flags.append('= D+0')
        if s == nxdy: flags.append('= D+1')
        if s == prvs: flags.append('= D+2')
        flag_str = '  ' + ', '.join(flags) if flags else ''
        print(f'  {r["ticker"]:<8}: {r["nrcvb_buy_amt"]:>10,} + {r["ruse_psbl_amt"]:>10,} '
              f'= {s:>10,}{flag_str}')

    # C4: ord_psbl_cash 가 항상 dnca_tot_amt와 같은가
    print('\n[C4] ord_psbl_cash vs dnca_tot_amt(D+0):')
    all_eq = all(r['ord_psbl_cash'] == dnca for r in exp_a)
    if all_eq:
        print(f'  ✅ 모든 종목에서 ord_psbl_cash == D+0 ({dnca:,}) — 종목 무관, 계좌 D+0')
    else:
        for r in exp_a:
            mark = '✅' if r['ord_psbl_cash'] == dnca else '❌'
            print(f'  {mark} {r["ticker"]:<8}: ord_psbl_cash={r["ord_psbl_cash"]:,}')

    # C5: 지정가/시장가 비교 — nrcvb의 종속성
    print('\n[C5] 실험 B — nrcvb_buy_amt가 ord_dvsn/price 에 따라 변하는가:')
    if exp_b:
        nrcvb_b_set = {r['nrcvb'] for r in exp_b}
        print(f'  case별 nrcvb 고유값: {len(nrcvb_b_set)}개')
        for r in exp_b:
            print(f'    {r["case"]:<18}: nrcvb={r["nrcvb"]:>10,}, '
                  f'calc_unpr={r["calc_unpr"]:>8,}, qty={r["max_qty"]}')

    # C6: 510,632 / 9,053 재확인
    target_a = 510632
    target_b = 9053
    print(f'\n[C6] 기준값 재매칭 (ISA 시점 510,632 / 9,053):')
    matches = [(r['ticker'], r['nrcvb_buy_amt']) for r in exp_a if r['nrcvb_buy_amt'] == target_a]
    print(f'  실험 A에서 nrcvb=={target_a} 인 종목: {matches}')

    # 최종 가설 판단
    print('\n[C-결론] 데이터 기반 판단:')
    if len(nrcvb_set) > 1:
        print('  • nrcvb_buy_amt는 종목별로 다르다 → 계좌 단위 \'주문가능\' 단일값이 아님.')
        print('  • KIS 앱이 보여주는 510,632는 그 시점에 사용자가 조회한 ticker 기준.')
        print('  • 9,053은 \'특정 ticker × 시장가 보수단가 × 정수 라운딩 잔여\'에 해당.')
    if all_eq:
        print('  • ord_psbl_cash는 D+0 예수금 그 자체 (검증됨).')


def main() -> None:
    c = KISClient('ISA')
    base = baseline_dump(c)
    if not base['output1']:
        print('보유 종목 없음 → 실험 A 스킵')
        return
    exp_a = experiment_a(c, base['output1'])
    exp_b = experiment_b(c)
    experiment_c(base['output2'], exp_a, exp_b)


if __name__ == '__main__':
    main()
