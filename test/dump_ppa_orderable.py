"""PPA 주문가능 silent failure 추적용 진단 스크립트.

배경: KIS 앱은 PPA 주문가능 191,209원이지만 verify_cash는 0원 → silent failure.
가설: ACNT_PRDT_CD에 acc_no_postfix('22')를 그대로 보내는 것이 잘못이고,
KIS 공식 doc의 "ACNT_PRDT_CD=29 고정"이 실제로 맞을 수 있음.

실험:
  E1) PPA 잔고조회 raw 응답 — ACNT_PRDT_CD='22' vs '29' 비교
  E2) PPA 매수가능조회 raw 응답 — ACNT_PRDT_CD='22' vs '29' 비교
  E3) 191,209원이 어떤 필드에 들어있는지 grep

코드 변경 없이 진단만 수행 (test/ 폴더 내 1회성).
실행: uv run python -m test.dump_ppa_orderable
"""
import sys
import json
from pathlib import Path

current_dir = Path(__file__).resolve()
project_root = current_dir.parent.parent
sys.path.append(str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

from src.kis.client import KISClient


TARGET_AMOUNT = 191_209  # KIS 앱이 보여주는 PPA 주문가능


def _to_int(v) -> int:
    try:
        return int(str(v))
    except (ValueError, TypeError):
        return 0


def _dump_dict(d, indent=2):
    """dict의 모든 키·값을 정렬 출력. 값이 dict/list면 재귀."""
    pad = ' ' * indent
    if isinstance(d, dict):
        if not d:
            print(f'{pad}<empty dict>')
            return
        for k, v in d.items():
            if isinstance(v, (dict, list)) and v:
                print(f'{pad}{k}:')
                _dump_dict(v, indent + 4)
            else:
                print(f'{pad}{k:30s}: {v!r}')
    elif isinstance(d, list):
        if not d:
            print(f'{pad}<empty list>')
            return
        for i, item in enumerate(d):
            print(f'{pad}[{i}]')
            _dump_dict(item, indent + 4)
    else:
        print(f'{pad}{d!r}')


def _hunt_value(payload, target: int, path='') -> list:
    """payload(dict/list) 안에서 target 값을 가진 모든 키 경로를 찾는다."""
    hits = []
    if isinstance(payload, dict):
        for k, v in payload.items():
            sub_path = f'{path}.{k}' if path else k
            if isinstance(v, (dict, list)):
                hits.extend(_hunt_value(v, target, sub_path))
            else:
                if _to_int(v) == target:
                    hits.append((sub_path, v))
    elif isinstance(payload, list):
        for i, item in enumerate(payload):
            hits.extend(_hunt_value(item, target, f'{path}[{i}]'))
    return hits


def call_balance_with_acnt_prdt_cd(c: KISClient, acnt_prdt_cd: str) -> dict:
    """PPA 잔고조회를 ACNT_PRDT_CD를 명시적으로 지정해 호출."""
    params = {
        'CANO': c.acc_no_prefix,
        'ACNT_PRDT_CD': acnt_prdt_cd,   # ← 실험 변수
        'ACCA_DVSN_CD': '00',
        'INQR_DVSN': '00',
        'CTX_AREA_FK100': '',
        'CTX_AREA_NK100': '',
    }
    res = c._get(c._balance_url(), c._balance_tr_id(), params)
    data = res.json()
    data['_http_status'] = res.status_code
    data['_tr_cont_header'] = res.headers.get('tr_cont', '')
    return data


def call_orderable_with_acnt_prdt_cd(c: KISClient, acnt_prdt_cd: str, ticker: str = '005930') -> dict:
    """PPA 매수가능조회를 ACNT_PRDT_CD를 명시적으로 지정해 호출."""
    params = {
        'CANO': c.acc_no_prefix,
        'ACNT_PRDT_CD': acnt_prdt_cd,   # ← 실험 변수
        'PDNO': ticker,
        'ORD_UNPR': '',                  # 시장가
        'ORD_DVSN': '01',
        'CMA_EVLU_AMT_ICLD_YN': 'Y',
        'ACCA_DVSN_CD': '00',
    }
    res = c._get(c._orderable_url(), c._orderable_tr_id(), params)
    data = res.json()
    data['_http_status'] = res.status_code
    return data


def experiment_e1_balance(c: KISClient) -> dict:
    """E1 — PPA 잔고조회 raw 응답을 ACNT_PRDT_CD='22'/'29' 두 값으로 호출."""
    print('\n' + '=' * 70)
    print('E1: PPA 잔고조회 raw 응답 — ACNT_PRDT_CD 변동 실험')
    print('=' * 70)
    print(f'  base_url     : {c.base_url}')
    print(f'  balance_url  : {c._balance_url()}')
    print(f'  balance_tr_id: {c._balance_tr_id()}')
    print(f'  acc_no_postfix(현재 코드가 보내는 값): {c.acc_no_postfix!r}')

    results = {}
    for cd in ('22', '29'):
        print(f'\n--- ACNT_PRDT_CD={cd!r} ---')
        try:
            data = call_balance_with_acnt_prdt_cd(c, cd)
        except Exception as e:
            print(f'  ERROR: {type(e).__name__}: {e}')
            results[cd] = None
            continue
        rt_cd = data.get('rt_cd', 'N/A')
        msg_cd = data.get('msg_cd', 'N/A')
        msg1 = data.get('msg1', 'N/A')
        print(f'  HTTP {data["_http_status"]}  rt_cd={rt_cd}  msg_cd={msg_cd}  msg1={msg1}')

        output1 = data.get('output1', [])
        output2 = data.get('output2', [])
        print(f'  output1 row count : {len(output1) if isinstance(output1, list) else "non-list"}')
        if isinstance(output2, list):
            print(f'  output2 list len  : {len(output2)}')
            o2 = output2[0] if output2 else {}
        else:
            print(f'  output2 type      : dict (single)')
            o2 = output2 or {}

        print(f'\n  output2 ({len(o2) if isinstance(o2, dict) else "?"} keys):')
        _dump_dict(o2 if isinstance(o2, dict) else {'raw': o2}, indent=4)

        if output1:
            print(f'\n  output1[0] sample:')
            _dump_dict(output1[0] if isinstance(output1, list) else output1, indent=4)

        # 191,209 hunt
        hits = _hunt_value(data, TARGET_AMOUNT)
        if hits:
            print(f'\n  >>>>> {TARGET_AMOUNT:,}원 발견된 경로:')
            for path, val in hits:
                print(f'        {path} = {val}')
        else:
            print(f'\n  ({TARGET_AMOUNT:,}원은 응답 어디에도 없음)')

        results[cd] = data
    return results


def experiment_e2_orderable(c: KISClient) -> dict:
    """E2 — PPA 매수가능조회 raw 응답을 ACNT_PRDT_CD='22'/'29'로 호출."""
    print('\n' + '=' * 70)
    print('E2: PPA 매수가능조회 raw 응답 — ACNT_PRDT_CD 변동 실험')
    print('=' * 70)
    print(f'  orderable_url  : {c._orderable_url()}')
    print(f'  orderable_tr_id: {c._orderable_tr_id()}')

    results = {}
    for cd in ('22', '29'):
        print(f'\n--- ACNT_PRDT_CD={cd!r} (ticker=005930, 시장가) ---')
        try:
            data = call_orderable_with_acnt_prdt_cd(c, cd, '005930')
        except Exception as e:
            print(f'  ERROR: {type(e).__name__}: {e}')
            results[cd] = None
            continue
        rt_cd = data.get('rt_cd', 'N/A')
        msg_cd = data.get('msg_cd', 'N/A')
        msg1 = data.get('msg1', 'N/A')
        print(f'  HTTP {data["_http_status"]}  rt_cd={rt_cd}  msg_cd={msg_cd}  msg1={msg1}')

        output = data.get('output', {})
        print(f'\n  output ({len(output) if isinstance(output, dict) else "?"} keys):')
        _dump_dict(output if isinstance(output, dict) else {'raw': output}, indent=4)

        # 191,209 hunt
        hits = _hunt_value(data, TARGET_AMOUNT)
        if hits:
            print(f'\n  >>>>> {TARGET_AMOUNT:,}원 발견된 경로:')
            for path, val in hits:
                print(f'        {path} = {val}')
        else:
            print(f'\n  ({TARGET_AMOUNT:,}원은 응답 어디에도 없음)')

        results[cd] = data
    return results


def experiment_e3_summary(e1_results: dict, e2_results: dict) -> None:
    """E3 — 두 실험의 비교 요약·결론."""
    print('\n' + '=' * 70)
    print('E3: 종합 결론')
    print('=' * 70)

    # E1 비교
    print('\n[잔고조회]')
    for cd in ('22', '29'):
        r = e1_results.get(cd)
        if r is None:
            print(f'  ACNT_PRDT_CD={cd}: 호출 실패')
            continue
        rt_cd = r.get('rt_cd', '?')
        o2 = r.get('output2', [])
        if isinstance(o2, list) and o2:
            o2 = o2[0]
        elif not isinstance(o2, dict):
            o2 = {}
        prvs = _to_int(o2.get('prvs_rcdl_excc_amt', 0))
        scts = _to_int(o2.get('scts_evlu_amt', 0))
        tot = _to_int(o2.get('tot_evlu_amt', 0))
        n_out1 = len(r.get('output1', []) or [])
        print(f'  ACNT_PRDT_CD={cd}: rt_cd={rt_cd}  '
              f'output1={n_out1}행  D+2={prvs:,}  scts={scts:,}  tot={tot:,}')

    # E2 비교
    print('\n[매수가능조회]')
    for cd in ('22', '29'):
        r = e2_results.get(cd)
        if r is None:
            print(f'  ACNT_PRDT_CD={cd}: 호출 실패')
            continue
        rt_cd = r.get('rt_cd', '?')
        out = r.get('output', {}) or {}
        nrcvb = _to_int(out.get('nrcvb_buy_amt', 0))
        max_buy = _to_int(out.get('max_buy_amt', 0))
        opc = _to_int(out.get('ord_psbl_cash', 0))
        print(f'  ACNT_PRDT_CD={cd}: rt_cd={rt_cd}  '
              f'nrcvb={nrcvb:,}  max_buy_amt={max_buy:,}  ord_psbl_cash={opc:,}')

    # 191,209 추적 종합
    print(f'\n[{TARGET_AMOUNT:,}원 발견 위치 종합]')
    for label, results in [('잔고조회', e1_results), ('매수가능조회', e2_results)]:
        for cd, r in results.items():
            if r is None:
                continue
            hits = _hunt_value(r, TARGET_AMOUNT)
            if hits:
                for path, val in hits:
                    print(f'  {label} ACNT_PRDT_CD={cd}: {path} = {val}')

    print('\n[판단]')
    print('  • output1 행수 차이가 크면 어느 ACNT_PRDT_CD가 \'진짜\' PPA 응답을 주는지 판가름.')
    print('  • 잔고/주문가능 응답에 191,209가 직접 보이면 그 필드가 정답.')
    print('  • 둘 다 안 보이면 KIS 앱 표시 = 우리가 호출 안 하는 다른 엔드포인트(예: 일별주문가능) 가능성.')


def call_balance_with_dvsn_combo(c: KISClient, acca: str, inqr: str) -> dict:
    """잔고조회를 ACCA_DVSN_CD/INQR_DVSN 조합으로 호출 (ACNT_PRDT_CD는 '22' 고정)."""
    params = {
        'CANO': c.acc_no_prefix,
        'ACNT_PRDT_CD': '22',
        'ACCA_DVSN_CD': acca,
        'INQR_DVSN': inqr,
        'CTX_AREA_FK100': '',
        'CTX_AREA_NK100': '',
    }
    res = c._get(c._balance_url(), c._balance_tr_id(), params)
    data = res.json()
    data['_http_status'] = res.status_code
    return data


def call_orderable_with_ticker(c: KISClient, ticker: str) -> dict:
    """매수가능조회를 저가/고가 ticker로 시도 (ACNT_PRDT_CD='22' 고정, 시장가)."""
    params = {
        'CANO': c.acc_no_prefix,
        'ACNT_PRDT_CD': '22',
        'PDNO': ticker,
        'ORD_UNPR': '',
        'ORD_DVSN': '01',
        'CMA_EVLU_AMT_ICLD_YN': 'Y',
        'ACCA_DVSN_CD': '00',
    }
    res = c._get(c._orderable_url(), c._orderable_tr_id(), params)
    return res.json()


def experiment_e4_dvsn_matrix(c: KISClient) -> None:
    """E4 — 잔고조회 ACCA_DVSN_CD × INQR_DVSN 매트릭스. KIOK0560 회피 시도."""
    print('\n' + '=' * 70)
    print('E4: PPA 잔고조회 — ACCA_DVSN_CD × INQR_DVSN 매트릭스')
    print('=' * 70)
    print('가설: KIOK0560 "조회할 내용이 없습니다"는 잘못된 DVSN 값일 수 있음.\n')

    accas = ['00', '01', '02']  # 00=전체, 01/02=적립금구분 후보
    inqrs = ['00', '01', '02']   # 00=전체, 01=매도이외, 02 등

    print(f'{"ACCA":<6}|{"INQR":<6}|{"rt_cd":<7}|{"msg_cd":<10}|{"out1":<6}|'
          f'{"D+0":>10}|{"D+2":>10}|{"scts":>12}|{"tot":>12}|{"hit_target":<10}')
    print('-' * 110)

    for acca in accas:
        for inqr in inqrs:
            try:
                data = call_balance_with_dvsn_combo(c, acca, inqr)
            except Exception as e:
                print(f'{acca:<6}|{inqr:<6}| ERR: {e}')
                continue
            rt_cd = data.get('rt_cd', '?')
            msg_cd = data.get('msg_cd', '?')
            o1 = data.get('output1', []) or []
            o2 = data.get('output2', {})
            if isinstance(o2, list):
                o2 = o2[0] if o2 else {}
            elif not isinstance(o2, dict):
                o2 = {}
            d0 = _to_int(o2.get('dnca_tot_amt', 0))
            d2 = _to_int(o2.get('prvs_rcdl_excc_amt', 0))
            scts = _to_int(o2.get('scts_evlu_amt', 0))
            tot = _to_int(o2.get('tot_evlu_amt', 0))
            hits = _hunt_value(data, TARGET_AMOUNT)
            hit_label = 'HIT' if hits else '-'
            print(f'{acca:<6}|{inqr:<6}|{rt_cd!s:<7}|{msg_cd:<10}|{len(o1):<6}|'
                  f'{d0:>10,}|{d2:>10,}|{scts:>12,}|{tot:>12,}|{hit_label:<10}')
            if hits:
                for path, val in hits:
                    print(f'         └─ {path} = {val}')


def experiment_e5_ticker_matrix(c: KISClient) -> None:
    """E5 — 매수가능조회 저가 ticker로 max_buy_amt 추적."""
    print('\n' + '=' * 70)
    print('E5: PPA 매수가능조회 — 다양한 ticker로 max_buy_amt 추적')
    print('=' * 70)
    print('가설: 005930(349,000원)은 191,209로 1주도 못 사니 max_buy_amt=0이 정답.')
    print('      저가 종목이면 max_buy_amt > 0 — 그게 KIS 앱 191,209의 후보.\n')

    # 저가→고가 mix. KIWOOM 200TR(151,565), TIGER KOFR(110,840), KODEX 미국S&P500(24,455),
    # 0085N0 ACE 미국10년국채(9,965), 069500 KODEX200(저가 추정)
    tickers = ['005930', '069500', '0085N0', '379800', '294400', '449170', '360750']

    print(f'{"ticker":<9}|{"rt_cd":<7}|{"msg_cd":<10}|{"calc_unpr":>10}|{"max_qty":>9}|'
          f'{"max_buy":>12}|{"ord_psbl":>10}|{"ruse":>10}|{"hit":<6}')
    print('-' * 95)

    for t in tickers:
        try:
            data = call_orderable_with_ticker(c, t)
        except Exception as e:
            print(f'{t:<9}| ERR: {e}')
            continue
        rt_cd = data.get('rt_cd', '?')
        msg_cd = data.get('msg_cd', '?')
        out = data.get('output', {}) or {}
        unpr = _to_int(out.get('psbl_qty_calc_unpr', 0))
        qty = _to_int(out.get('max_buy_qty', 0))
        mb = _to_int(out.get('max_buy_amt', 0))
        opc = _to_int(out.get('ord_psbl_cash', 0))
        ruse = _to_int(out.get('ruse_psbl_amt', 0))
        hits = _hunt_value(data, TARGET_AMOUNT)
        hit_label = 'HIT' if hits else '-'
        print(f'{t:<9}|{rt_cd!s:<7}|{msg_cd:<10}|{unpr:>10,}|{qty:>9}|'
              f'{mb:>12,}|{opc:>10,}|{ruse:>10,}|{hit_label:<6}')
        if hits:
            for path, val in hits:
                print(f'         └─ {path} = {val}')


def main() -> None:
    c = KISClient('PPA')
    print(f'PPA client 인증 완료. acc_no={c.acc_no}, postfix={c.acc_no_postfix!r}')
    print(f'_is_pension()={c._is_pension()}')

    e1 = experiment_e1_balance(c)
    e2 = experiment_e2_orderable(c)
    experiment_e3_summary(e1, e2)
    experiment_e4_dvsn_matrix(c)
    experiment_e5_ticker_matrix(c)


if __name__ == '__main__':
    main()
