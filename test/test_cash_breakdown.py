"""KIS 예수금 산정 단위 테스트.

거래 없이 모킹된 응답으로 다음을 검증한다:
- fetch_domestic_cash_breakdown: 7개 필드를 정수로 정확히 추출하는가
- fetch_domestic_cash_balance: prvs_rcdl_excc_amt(D+2 예수금)를 반환하는가
- fetch_buy_orderable_cash: inquire-psbl-order의 nrcvb_buy_amt를 정수로 반환하는가

실행: `uv run python -m test.test_cash_breakdown`
"""
import sys
from pathlib import Path
from unittest.mock import patch

current_dir = Path(__file__).resolve()
project_root = current_dir.parent.parent
sys.path.append(str(project_root))


SAMPLE_BALANCE_RESPONSE = {
    'output1': [],
    'output2': [{
        'dnca_tot_amt':       '490000',    # D+0
        'nxdy_excc_amt':      '700000',    # D+1
        'prvs_rcdl_excc_amt': '900000',    # D+2 ← 매수 기준
        'thdt_buy_amt':       '0',
        'thdt_sll_amt':       '0',
        'cma_evlu_amt':       '0',
        'scts_evlu_amt':      '5000000',   # 유가증권 평가금액
        'tot_evlu_amt':       '5900000',   # = scts_evlu_amt + prvs_rcdl_excc_amt
    }],
    'tr_cont': 'D',
}


def _make_client_skipping_init(acc_no_postfix: str = '01'):
    """KISClient.__init__의 인증·토큰 흐름을 우회한 인스턴스 생성.

    acc_no_postfix:
    - '01': ISA·일반 위탁 (기본)
    - '22': 연금저축(PPA) — KIS는 일반 위탁 엔드포인트로 처리 (IRP와 다름)
    - '29': IRP(개인형 퇴직연금) — KIS 퇴직연금 전용 엔드포인트 사용
    mock=False 기본.
    """
    from src.kis.client import KISClient
    c = KISClient.__new__(KISClient)
    c.acc_no_postfix = acc_no_postfix
    c.acc_no_prefix = '00000000'
    c.mock = False
    return c


def test_fetch_domestic_cash_breakdown_returns_all_fields():
    client = _make_client_skipping_init()
    with patch.object(type(client), '_domestic_balance_page', return_value=SAMPLE_BALANCE_RESPONSE):
        breakdown = client.fetch_domestic_cash_breakdown()
    assert breakdown == {
        'dnca_tot_amt': 490_000,
        'nxdy_excc_amt': 700_000,
        'prvs_rcdl_excc_amt': 900_000,
        'thdt_buy_amt': 0,
        'thdt_sll_amt': 0,
        'scts_evlu_amt': 5_000_000,
        'tot_evlu_amt': 5_900_000,
    }, f'breakdown 결과가 예상과 다름: {breakdown}'
    assert breakdown['tot_evlu_amt'] == breakdown['scts_evlu_amt'] + breakdown['prvs_rcdl_excc_amt'], (
        'KIS 항등식 위반: tot_evlu_amt = scts_evlu_amt + prvs_rcdl_excc_amt'
    )
    print('✅ fetch_domestic_cash_breakdown: 7개 필드 정확히 추출 + KIS 항등식 일치')


def test_fetch_domestic_cash_balance_returns_d2():
    client = _make_client_skipping_init()
    with patch.object(type(client), '_domestic_balance_page', return_value=SAMPLE_BALANCE_RESPONSE):
        balance = client.fetch_domestic_cash_balance()
    assert balance == 900_000, f'fetch_domestic_cash_balance가 D+2(900,000)를 반환해야 하나 {balance:,}원 반환'
    print('✅ fetch_domestic_cash_balance: prvs_rcdl_excc_amt(D+2 예수금) 반환')


def test_fetch_buy_orderable_cash_returns_nrcvb():
    """fetch_buy_orderable_cash는 inquire-psbl-order의 nrcvb_buy_amt를 정수로 반환한다.

    실험으로 nrcvb_buy_amt가 종목 무관 계좌 단위 상수임이 확인됐다 (test/dump_isa_orderable.py).
    cash_balance(D+2)와는 의도적으로 다른 값을 반환할 수 있다.
    """
    sample_psbl_response = {
        'ord_psbl_cash':       '327867',
        'ord_psbl_sbst':       '52210610',
        'ruse_psbl_amt':       '189501',
        'fund_rpch_chgs':      '0',
        'psbl_qty_calc_unpr':  '349000',
        'nrcvb_buy_amt':       '510632',   # ← 본 함수가 반환해야 할 값
        'nrcvb_buy_qty':       '1',
        'max_buy_amt':         '510632',
        'max_buy_qty':         '1',
        'cma_evlu_amt':        '0',
    }
    client = _make_client_skipping_init()
    with patch.object(type(client), 'fetch_domestic_enable_buy', return_value=sample_psbl_response):
        orderable = client.fetch_buy_orderable_cash()
    assert orderable == 510_632, (
        f'fetch_buy_orderable_cash는 nrcvb_buy_amt(510,632)를 반환해야 함, 실제 {orderable:,}원'
    )
    assert isinstance(orderable, int), f'반환 타입은 int여야 함, 실제 {type(orderable).__name__}'
    print('✅ fetch_buy_orderable_cash: nrcvb_buy_amt를 int로 정확히 반환')


def test_buy_orderable_cash_independent_of_balance():
    """fetch_buy_orderable_cash와 fetch_domestic_cash_balance는 서로 다른 API를 호출하며,
    값이 달라도 정상이다 (D+2 예수금 vs 미수없는 매수 한도)."""
    sample_psbl_response = {
        'psbl_qty_calc_unpr': '349000',
        'nrcvb_buy_amt':      '510632',
        'max_buy_amt':        '510632',
        'max_buy_qty':        '1',
    }
    client = _make_client_skipping_init()
    with patch.object(type(client), '_domestic_balance_page', return_value=SAMPLE_BALANCE_RESPONSE), \
         patch.object(type(client), 'fetch_domestic_enable_buy', return_value=sample_psbl_response):
        balance = client.fetch_domestic_cash_balance()
        orderable = client.fetch_buy_orderable_cash()
    assert balance == 900_000, f'cash_balance는 D+2(900,000) 반환, 실제 {balance:,}'
    assert orderable == 510_632, f'orderable은 nrcvb(510,632) 반환, 실제 {orderable:,}'
    assert balance != orderable, 'D+2와 nrcvb가 서로 다른 값을 반환할 수 있어야 함'
    print('✅ cash_balance(D+2)와 orderable(nrcvb)는 독립적 — 다른 값 반환 가능')


def test_today_with_sells_and_buys_uses_d2_only():
    """오늘 매도/매수가 있어도 D+2 예수금을 그대로 반환 (이중 가산 없음)."""
    response = {
        'output1': [],
        'output2': [{
            'dnca_tot_amt':       '490000',
            'nxdy_excc_amt':      '600000',
            'prvs_rcdl_excc_amt': '950000',   # API가 이미 매도/매수 반영해 산정
            'thdt_buy_amt':       '50000',
            'thdt_sll_amt':       '500000',
            'scts_evlu_amt':      '5000000',
            'tot_evlu_amt':       '5950000',
        }],
        'tr_cont': 'D',
    }
    client = _make_client_skipping_init()
    with patch.object(type(client), '_domestic_balance_page', return_value=response):
        balance = client.fetch_domestic_cash_balance()
    assert balance == 950_000, f'D+2 단독값이 반환돼야 함, 실제 {balance:,}'
    print('✅ 오늘 매수/매도 발생 케이스: D+2 단독 반환 (이중 가산 없음)')


def test_total_balance_with_empty_holdings():
    """빈 계좌(보유종목 0)에서도 fetch_domestic_total_balance가 KeyError 없이 CASH 행만 반환."""
    empty_balance_response = {
        'output1': [],
        'output2': [{
            'dnca_tot_amt':       '0',
            'nxdy_excc_amt':      '0',
            'prvs_rcdl_excc_amt': '0',
            'thdt_buy_amt':       '0',
            'thdt_sll_amt':       '0',
            'scts_evlu_amt':      '0',
            'tot_evlu_amt':       '0',
        }],
        'tr_cont': 'D',
    }
    client = _make_client_skipping_init()
    with patch.object(type(client), 'fetch_domestic_stock_balance',
                      return_value={'output1': [], 'output2': []}), \
         patch.object(type(client), '_domestic_balance_page',
                      return_value=empty_balance_response):
        df = client.fetch_domestic_total_balance()
    assert (df['ticker'] == 'CASH').sum() == 1, 'CASH 행은 정확히 1개여야 함'
    assert (df['ticker'] != 'CASH').sum() == 0, '보유종목 행은 0개여야 함'
    cash_row = df[df['ticker'] == 'CASH'].iloc[0]
    assert cash_row['current_value'] == 0.0, f'CASH 평가금액 0이어야 함, 실제 {cash_row["current_value"]}'
    assert cash_row['currency_type'] == 'domestic'
    print('✅ 빈 계좌: fetch_domestic_total_balance 안전하게 CASH 행만 반환')


def test_irp_helpers_return_pension_endpoints():
    """postfix='29'(IRP)만 퇴직연금 TR_ID·URL을 반환. 연금저축(22)은 ISA와 동일."""
    client = _make_client_skipping_init(acc_no_postfix='29')
    assert client._is_pension() is True, 'IRP(29)는 _is_pension() True'
    assert client._balance_tr_id() == 'TTTC2208R'
    assert client._orderable_tr_id() == 'TTTC0503R'
    assert client._balance_url() == 'uapi/domestic-stock/v1/trading/pension/inquire-balance'
    assert client._orderable_url() == 'uapi/domestic-stock/v1/trading/pension/inquire-psbl-order'
    # 주문 TR_ID는 현재 ISA·IRP 동일 추정
    assert client._order_tr_id('buy') == 'TTTC0802U'
    assert client._order_tr_id('sell') == 'TTTC0801U'
    print('✅ IRP(postfix=29): 헬퍼들이 pension TR_ID·URL 반환')


def test_pension_savings_uses_general_endpoints():
    """postfix='22'(PPA — 연금저축)는 ISA와 동일한 일반 엔드포인트 사용.

    KIS 퇴직연금 전용 엔드포인트는 IRP('29')에만 응답. 연금저축은 일반 위탁으로 처리.
    """
    client = _make_client_skipping_init(acc_no_postfix='22')
    assert client._is_pension() is False, 'PPA(22)는 _is_pension() False (IRP만 True)'
    assert client._balance_tr_id() == 'TTTC8434R', '연금저축은 ISA TR_ID 사용'
    assert client._orderable_tr_id() == 'TTTC8908R', '연금저축은 ISA TR_ID 사용'
    assert client._balance_url() == 'uapi/domestic-stock/v1/trading/inquire-balance'
    assert client._orderable_url() == 'uapi/domestic-stock/v1/trading/inquire-psbl-order'
    print('✅ 연금저축(postfix=22): ISA와 동일한 일반 TR_ID·URL 반환')


def test_isa_helpers_return_general_endpoints():
    """postfix='01'(ISA·일반) 클라이언트가 일반 TR_ID·URL을 반환하는지 검증."""
    client = _make_client_skipping_init(acc_no_postfix='01')
    assert client._is_pension() is False
    assert client._balance_tr_id() == 'TTTC8434R'
    assert client._orderable_tr_id() == 'TTTC8908R'
    assert client._balance_url() == 'uapi/domestic-stock/v1/trading/inquire-balance'
    assert client._orderable_url() == 'uapi/domestic-stock/v1/trading/inquire-psbl-order'
    assert client._order_tr_id('buy') == 'TTTC0802U'
    assert client._order_tr_id('sell') == 'TTTC0801U'

    # 비정상 transaction_type → ValueError
    try:
        client._order_tr_id('cancel')
        assert False, '_order_tr_id가 ValueError 발생시켜야 함'
    except ValueError:
        pass
    print('✅ ISA·일반(postfix=01): 헬퍼들이 일반 TR_ID·URL 반환')


def test_irp_buy_orderable_uses_max_buy_amt():
    """IRP(postfix='29')에서 fetch_buy_orderable_cash가 max_buy_amt를 반환하는지.

    IRP 응답에는 nrcvb_buy_amt 필드가 부재하므로 _is_pension() 분기로 max_buy_amt 사용.
    실험 검증: docs/kis_cash_guide.md §4.2.
    """
    client = _make_client_skipping_init(acc_no_postfix='29')
    pension_response = {
        'ord_psbl_cash': '0',
        'ruse_psbl_amt': '0',
        'psbl_qty_calc_unpr': '70000',
        'max_buy_amt': '850000',     # ← 본 함수가 반환해야 할 값
        'max_buy_qty': '12',
        # 'nrcvb_buy_amt' 부재 — IRP 실응답과 동일
    }
    with patch.object(type(client), 'fetch_domestic_enable_buy', return_value=pension_response):
        orderable = client.fetch_buy_orderable_cash()
    assert orderable == 850_000, f'IRP는 max_buy_amt(850,000) 반환해야 함, 실제 {orderable:,}'
    assert isinstance(orderable, int)
    print('✅ IRP: fetch_buy_orderable_cash가 max_buy_amt 반환 (nrcvb_buy_amt 부재 케이스)')


# ---------------------------------------------------------------------- #
# tester 추가: implementer 단위 테스트가 놓친 외부 시각 케이스                  #
# ---------------------------------------------------------------------- #
# 기존 테스트는 TR_ID·URL은 검증하지만 실제 KIS API로 전달되는 params/data dict는
# 검증하지 않음. ACCA_DVSN_CD 같은 키가 빠지거나 오타나도 unit test는 통과하지만
# 실 API에서 silent failure가 나는 리스크. 아래 케이스로 paramsdict까지 잠가둠.


def _make_mock_response(json_payload, headers=None):
    """requests.Response를 모킹하기 위한 헬퍼. .json()·.headers를 갖는 객체 반환."""
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.json.return_value = json_payload
    resp.headers = headers or {'tr_cont': 'D'}
    return resp


def test_irp_buy_orderable_missing_max_buy_amt_raises_keyerror():
    """IRP 응답에 max_buy_amt가 없으면 KeyError로 명시적 실패해야 한다.

    silent하게 0원을 반환하면 자산배분 로직이 잘못된 한도로 매수 시도할 위험.
    KeyError가 surface되면 운영자가 알아차리고 KIS 응답 변경에 대응 가능.
    """
    client = _make_client_skipping_init(acc_no_postfix='29')
    broken_response = {
        'ord_psbl_cash': '0',
        'psbl_qty_calc_unpr': '70000',
        # max_buy_amt 부재 (IRP 분기에서 직접 접근하는 키)
    }
    with patch.object(type(client), 'fetch_domestic_enable_buy', return_value=broken_response):
        try:
            client.fetch_buy_orderable_cash()
            assert False, 'IRP에서 max_buy_amt 부재 시 KeyError가 발생해야 함 (silent 0 반환 X)'
        except KeyError as e:
            assert 'max_buy_amt' in str(e), f'max_buy_amt 누락이 명시돼야 함, 실제 {e}'
    print('✅ IRP: max_buy_amt 부재 시 KeyError로 명시적 실패 (silent 0 방지)')


def test_irp_breakdown_output2_dict_form():
    """IRP 응답은 output2가 dict 단일 (ISA는 list of dict). 양쪽 모두 정상 파싱돼야 한다.

    fetch_domestic_cash_breakdown은 raw_o2가 list면 [0], 아니면 그대로 사용.
    IRP의 dict-form 분기와 누락 필드 안전 처리(.get(key, 0))를 동시 검증.
    """
    pension_response = {
        'output1': [],
        'output2': {  # ← list가 아닌 단일 dict (IRP 실응답 형태)
            'dnca_tot_amt': '100000',
            'nxdy_excc_amt': '110000',
            'prvs_rcdl_excc_amt': '120000',
            # IRP에서 일부 필드 부재 가능 (scts_evlu_amt, tot_evlu_amt 등)
        },
        'tr_cont': 'D',
    }
    client = _make_client_skipping_init(acc_no_postfix='29')
    with patch.object(type(client), '_domestic_balance_page', return_value=pension_response):
        breakdown = client.fetch_domestic_cash_breakdown()
    assert breakdown['dnca_tot_amt'] == 100_000
    assert breakdown['nxdy_excc_amt'] == 110_000
    assert breakdown['prvs_rcdl_excc_amt'] == 120_000
    assert breakdown['scts_evlu_amt'] == 0, '부재 필드는 0으로 안전 처리'
    assert breakdown['tot_evlu_amt'] == 0, '부재 필드는 0으로 안전 처리'
    print('✅ IRP: output2 dict-form 파싱 + 부재 필드 0 처리')


def test_domestic_balance_page_pension_request_params():
    """PPA `_domestic_balance_page`가 _get에 정확한 URL/TR_ID/params를 전달하는지 검증.

    paramsdict 자체가 잘못되면 KIS는 silent하게 빈 응답을 줄 수 있음.
    - PPA: ACCA_DVSN_CD='00', INQR_DVSN='00', ISA 전용 키 부재
    - ACNT_PRDT_CD는 acc_no_postfix 그대로 (KIS doc "29 고정"은 예시)
    """
    # IRP postfix='29' 케이스
    client = _make_client_skipping_init(acc_no_postfix='29')
    mock_resp = _make_mock_response({'output1': [], 'output2': {}})

    with patch.object(type(client), '_get', return_value=mock_resp) as mock_get:
        client._domestic_balance_page()

    args, kwargs = mock_get.call_args[0], mock_get.call_args[1]
    path, tr_id, params = args[0], args[1], args[2]

    assert path == 'uapi/domestic-stock/v1/trading/pension/inquire-balance'
    assert tr_id == 'TTTC2208R'
    assert params['CANO'] == '00000000'
    assert params['ACNT_PRDT_CD'] == '29', f'IRP postfix는 그대로 전달, 실제 {params["ACNT_PRDT_CD"]}'
    assert params['ACCA_DVSN_CD'] == '00', 'PPA/IRP 필수 파라미터'
    assert params['INQR_DVSN'] == '00'
    # ISA 전용 키 부재 확인 (있으면 KIS가 reject하거나 무시)
    isa_only_keys = ('AFHR_FLPR_YN', 'OFL_YN', 'UNPR_DVSN',
                     'FUND_STTL_ICLD_YN', 'FNCG_AMT_AUTO_RDPT_YN', 'PRCS_DVSN')
    for key in isa_only_keys:
        assert key not in params, f'PPA params에 ISA 전용 키 {key} 포함됨'

    # ISA postfix='01' 비교 — ACCA_DVSN_CD 부재 + ISA 전용 키 존재
    isa_client = _make_client_skipping_init(acc_no_postfix='01')
    with patch.object(type(isa_client), '_get', return_value=mock_resp) as mock_get_isa:
        isa_client._domestic_balance_page()
    isa_params = mock_get_isa.call_args[0][2]
    assert 'ACCA_DVSN_CD' not in isa_params, 'ISA에는 ACCA_DVSN_CD 부재여야 함'
    assert isa_params['AFHR_FLPR_YN'] == 'N'
    assert isa_params['INQR_DVSN'] == '01', 'ISA INQR_DVSN은 01'
    print('✅ _domestic_balance_page params: PPA/IRP=ACCA_DVSN_CD+INQR_DVSN=00, ISA=ISA 전용 셋')


def test_fetch_domestic_enable_buy_irp_request_params():
    """IRP `fetch_domestic_enable_buy`가 _get에 정확한 URL/TR_ID/params 전달 검증.

    - IRP(postfix='29'): ACCA_DVSN_CD='00' 추가, OVRS_ICLD_YN 부재
    - ISA(postfix='01'): OVRS_ICLD_YN='N', ACCA_DVSN_CD 부재
    - 연금저축(postfix='22')은 ISA와 동일한 일반 엔드포인트 사용 (_is_pension() False)
    """
    # IRP 케이스
    client = _make_client_skipping_init(acc_no_postfix='29')
    mock_resp = _make_mock_response({'output': {'max_buy_amt': '0'}})

    with patch.object(type(client), '_get', return_value=mock_resp) as mock_get:
        client.fetch_domestic_enable_buy(ticker='005930', ord_dvsn='01', price=-1)

    path, tr_id, params = mock_get.call_args[0][:3]
    assert path == 'uapi/domestic-stock/v1/trading/pension/inquire-psbl-order'
    assert tr_id == 'TTTC0503R'
    assert params['ACCA_DVSN_CD'] == '00', 'IRP 필수 파라미터'
    assert 'OVRS_ICLD_YN' not in params, 'IRP에는 OVRS_ICLD_YN 부재여야 함'
    assert params['CMA_EVLU_AMT_ICLD_YN'] == 'Y', '양 doc 모두 Required'
    assert params['ORD_UNPR'] == '', '시장가(ord_dvsn=01)일 때 ORD_UNPR 빈 문자열'
    assert params['ACNT_PRDT_CD'] == '29', 'IRP postfix 그대로'

    # ISA 케이스
    isa_client = _make_client_skipping_init(acc_no_postfix='01')
    with patch.object(type(isa_client), '_get', return_value=mock_resp) as mock_get_isa:
        isa_client.fetch_domestic_enable_buy(ticker='005930', ord_dvsn='01', price=-1)
    isa_params = mock_get_isa.call_args[0][2]
    assert 'ACCA_DVSN_CD' not in isa_params, 'ISA에는 ACCA_DVSN_CD 부재여야 함'
    assert isa_params['OVRS_ICLD_YN'] == 'N', 'ISA 전용 파라미터'

    # 연금저축(PPA, postfix='22') 케이스 — ISA와 동일 동작
    ppa_client = _make_client_skipping_init(acc_no_postfix='22')
    with patch.object(type(ppa_client), '_get', return_value=mock_resp) as mock_get_ppa:
        ppa_client.fetch_domestic_enable_buy(ticker='005930', ord_dvsn='01', price=-1)
    ppa_path, ppa_tr_id, ppa_params = mock_get_ppa.call_args[0][:3]
    assert ppa_path == 'uapi/domestic-stock/v1/trading/inquire-psbl-order', '연금저축은 일반 URL'
    assert ppa_tr_id == 'TTTC8908R', '연금저축은 ISA TR_ID'
    assert 'ACCA_DVSN_CD' not in ppa_params
    assert ppa_params['ACNT_PRDT_CD'] == '22'
    print('✅ fetch_domestic_enable_buy params: IRP=ACCA_DVSN_CD+no OVRS, ISA=OVRS_ICLD_YN+no ACCA, PPA=ISA와 동일')


def test_create_domestic_order_irp_includes_acca_dvsn_cd():
    """IRP `create_domestic_order` data에 ACCA_DVSN_CD='00' 포함, ISA·PPA에는 부재인지 검증.

    실주문 호출 경로의 paramsdict 검증 — 빠지면 KIS가 reject할 가능성.
    실API 호출 없이 _post 모킹으로 data 검증.
    """
    # IRP 매수 케이스
    pension = _make_client_skipping_init(acc_no_postfix='29')
    mock_resp = _make_mock_response({'rt_cd': '0', 'msg1': 'OK'})

    with patch.object(type(pension), 'issue_hashkey', return_value='HASHKEY'), \
         patch.object(type(pension), '_post', return_value=mock_resp) as mock_post:
        pension.create_domestic_order(
            transaction_type='buy', ticker='005930',
            ord_qty=1, ord_dvsn='01',
        )

    args, kwargs = mock_post.call_args[0], mock_post.call_args[1]
    path, tr_id, data = args[0], args[1], args[2]

    assert path == 'uapi/domestic-stock/v1/trading/order-cash'
    assert tr_id == 'TTTC0802U', 'IRP 매수도 ISA와 동일 TR_ID 추정'
    assert data['ACCA_DVSN_CD'] == '00', 'IRP 주문 data 필수'
    assert data['ACNT_PRDT_CD'] == '29', 'IRP postfix 그대로'
    assert data['ORD_UNPR'] == '0', '시장가(ord_dvsn=01)는 ORD_UNPR=0 문자열'
    assert kwargs['custtype'] == 'P'
    assert kwargs['hashkey'] == 'HASHKEY'

    # ISA 매도 케이스
    isa = _make_client_skipping_init(acc_no_postfix='01')
    with patch.object(type(isa), 'issue_hashkey', return_value='HASHKEY'), \
         patch.object(type(isa), '_post', return_value=mock_resp) as mock_post_isa:
        isa.create_domestic_order(
            transaction_type='sell', ticker='005930',
            ord_qty=1, ord_dvsn='01',
        )

    isa_args = mock_post_isa.call_args[0]
    isa_tr_id, isa_data = isa_args[1], isa_args[2]
    assert isa_tr_id == 'TTTC0801U'
    assert 'ACCA_DVSN_CD' not in isa_data, 'ISA 주문 data에는 ACCA_DVSN_CD 부재'
    assert isa_data['ACNT_PRDT_CD'] == '01'

    # 연금저축(PPA, postfix='22') 매수 — ISA와 동일 (ACCA_DVSN_CD 부재)
    ppa = _make_client_skipping_init(acc_no_postfix='22')
    with patch.object(type(ppa), 'issue_hashkey', return_value='HASHKEY'), \
         patch.object(type(ppa), '_post', return_value=mock_resp) as mock_post_ppa:
        ppa.create_domestic_order(
            transaction_type='buy', ticker='005930',
            ord_qty=1, ord_dvsn='01',
        )
    ppa_data = mock_post_ppa.call_args[0][2]
    assert 'ACCA_DVSN_CD' not in ppa_data, '연금저축은 ISA와 동일 — ACCA_DVSN_CD 부재'
    assert ppa_data['ACNT_PRDT_CD'] == '22'
    print('✅ create_domestic_order data: IRP=ACCA_DVSN_CD+TR_ID, ISA·PPA=no ACCA+TR_ID')


if __name__ == '__main__':
    test_fetch_domestic_cash_breakdown_returns_all_fields()
    test_fetch_domestic_cash_balance_returns_d2()
    test_fetch_buy_orderable_cash_returns_nrcvb()
    test_buy_orderable_cash_independent_of_balance()
    test_today_with_sells_and_buys_uses_d2_only()
    test_total_balance_with_empty_holdings()
    test_irp_helpers_return_pension_endpoints()
    test_pension_savings_uses_general_endpoints()
    test_isa_helpers_return_general_endpoints()
    test_irp_buy_orderable_uses_max_buy_amt()
    # tester 추가 (IRP/PPA 분리 갱신 후)
    test_irp_buy_orderable_missing_max_buy_amt_raises_keyerror()
    test_irp_breakdown_output2_dict_form()
    test_domestic_balance_page_pension_request_params()
    test_fetch_domestic_enable_buy_irp_request_params()
    test_create_domestic_order_irp_includes_acca_dvsn_cd()
    print('\n전체 테스트 통과')
