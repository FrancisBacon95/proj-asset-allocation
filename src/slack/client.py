from datetime import datetime

import pandas as pd
import pytz
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from src.config.env import SLACK_BOT_TOKEN, SLACK_CHANNEL_ID
from src.logger import get_logger

logger = get_logger(__name__)


class SlackClient:
    client = WebClient(token=SLACK_BOT_TOKEN, timeout=90)

    def upload_files(self, file: str, msg: str = None):
        try:
            result = self.client.files_upload_v2(
                channels=SLACK_CHANNEL_ID,
                initial_comment=msg,
                file=file,
            )
            logger.info(result)
        except SlackApiError as e:
            logger.error('Error uploading file: %s', e)

    def chat_postMessage(self, title: str, contents: str):
        slack_msg_blocks = [
            {
                'type': 'header',
                'text': {'type': 'plain_text', 'text': title, 'emoji': True},
            },
            {'type': 'section', 'text': {'type': 'mrkdwn', 'text': contents}},
        ]
        self.client.chat_postMessage(
            channel=SLACK_CHANNEL_ID,
            blocks=slack_msg_blocks,
            text=title,
        )


def slack_notify(title: str, contents: str) -> None:
    try:
        SlackClient().chat_postMessage(title, contents)
    except Exception as e:
        logger.error('Slack notify failed: %s', e)


def _format_stock_header(row) -> str:
    return (
        f"*[{row['stock_nm']}: `{row['ticker']}`]*\n"
        f"- current_price: `{row['current_price']:,.0f}`\n"
        f"- current_quantity: `{row['current_quantity']:,.0f}`\n"
        f"- current_value: `{row['current_value']:,.0f}` (`{row['current_pct']:.2f}%` → 목표: `{row['weight']*100:.2f}%`)"
    )


def _format_result_for_slack(df: pd.DataFrame) -> str:
    """리밸런싱 실행 결과 DataFrame을 Slack 메시지 형식의 문자열로 변환한다.

    ARCH-004 이후 requested_quantity / filled_quantity 둘 다 노출.
    transaction_quantity는 deprecated alias이므로 표시에서 제외.
    """
    lines = []
    for _, row in df.iterrows():
        # ARCH-004 컬럼 부재 시 transaction_quantity로 폴백 (legacy DataFrame 호환)
        requested = row.get('requested_quantity', row.get('transaction_quantity'))
        filled = row.get('filled_quantity', row.get('transaction_quantity'))
        lines.append(
            _format_stock_header(row) + "\n"
            f"- required_transaction: `{row['required_transaction']}`\n"
            f"- required_quantity: `{row['required_quantity']}`\n"
            f"- enable_quantity: `{row['enable_quantity']}`\n"
            f"- requested / filled: `{requested}` / `{filled}`\n"
            f"- skipped_reason: `{row.get('skipped_reason')}`\n"
            f"- is_success: `{row['is_success']}`\n"
            f"- response_msg: `{row['response_msg']}`"
        )
    return '\n\n'.join(lines)


def format_rebalancing_summary(
    result_df: pd.DataFrame,
    remaining_cash: int,
    account_type: str,
    dt,
    trade_log_url: str = None,
) -> str:
    """리밸런싱 결과를 Slack mrkdwn 형식의 표로 포맷팅 (ARCH-006).

    설계:
    - 전·후 분모를 분리: 전 = (전 주식 평가금 + 전 예수금), 후 = (후 주식 평가금 + 잔여 예수금).
      현금을 분모 구성 요소로 명시 포함해 buy-only / sell-only 시나리오에서도 일관.
    - 후 비중은 주문 요청 수량이 아니라 확인된 체결 수량(filled_quantity) 기준.
      ARCH-004 이전 호출자 호환을 위해 filled_quantity 부재 시 transaction_quantity로 폴백.
    - planner가 계산한 원래 current_pct는 덮어쓰지 않고 보존. 표시는 별도 _before_pct 컬럼.
      (planner의 current_pct는 buffer 미차감 총자산 기준 — 동일 분모이므로 본 함수의 _before_pct와 일치.)
    """
    # dt가 date/datetime 모두 허용
    if hasattr(dt, 'strftime'):
        dt_str = dt.strftime('%Y-%m-%d %H:%M KST') if hasattr(dt, 'hour') else dt.strftime('%Y-%m-%d') + ' KST'
    else:
        dt_str = str(dt)

    # 전 예수금: planner가 만든 CASH 행의 current_value (체결 전 기준).
    # 부재 시 0으로 폴백 — 현금 미보유 케이스 또는 비표준 결과셋 대비.
    cash_row = result_df[result_df['ticker'] == 'CASH']
    cash_before = float(cash_row['current_value'].iloc[0]) if not cash_row.empty else 0.0

    df = result_df[result_df['ticker'] != 'CASH'].copy()

    # 체결 수량 — ARCH-004 분리 이후 filled_quantity 사용. 부재(legacy/외부 호출) 시 transaction_quantity 폴백.
    if 'filled_quantity' not in df.columns:
        df['filled_quantity'] = df.get('transaction_quantity', 0)

    # 숫자 변환 통일
    for col in ('filled_quantity', 'current_value', 'current_price', 'weight', 'current_pct'):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # 방향: buy=+1, sell=-1, 그 외 0. filled_quantity가 0이면 _after_value에 영향 없으므로 안전.
    direction_map = {'buy': 1, 'sell': -1}
    df['_direction'] = df['required_transaction'].map(direction_map).fillna(0)

    # 후 평가금액 = 전 평가금액 + 방향 × 체결수량(filled) × 현재가
    df['_after_value'] = df['current_value'] + df['_direction'] * df['filled_quantity'] * df['current_price']

    # 전·후 분모 분리 (현금 명시 포함). 리밸런싱 자체는 자산 규모를 바꾸지 않지만 수수료·매수단가 변동 등으로
    # 미세 차이가 있을 수 있으므로 같은 합계여도 명시적으로 분리한다.
    stocks_before_value = float(df['current_value'].sum())
    stocks_after_value = float(df['_after_value'].sum())
    before_total = stocks_before_value + cash_before
    after_total = stocks_after_value + float(remaining_cash)

    # 표시용 비중 — planner의 current_pct는 보존 (덮어쓰지 않음)
    df['_before_pct'] = (df['current_value'] / before_total * 100) if before_total > 0 else 0.0
    df['_after_pct'] = (df['_after_value'] / after_total * 100) if after_total > 0 else 0.0

    # 헤더 — 전·후 총자산과 현금을 명시해 분모 분리를 운영자에게 가시화.
    lines = [
        f'*[{account_type}] 리밸런싱 완료* · {dt_str}',
        f'전 총자산: *{before_total:,.0f}원* (현금 {cash_before:,.0f})',
        f'후 총자산: *{after_total:,.0f}원* (현금 {remaining_cash:,.0f})',
    ]
    if trade_log_url:
        lines.append(f'<{trade_log_url}|trade_log 보기 →>')
    lines.append('')

    def _fmt_diff(diff: float) -> str:
        sign = '+' if diff >= 0 else '−'
        return f'{sign}{abs(diff):.1f}'

    for _, row in df.iterrows():
        nm       = str(row.get('stock_nm', row.get('ticker', '')))
        ticker   = str(row.get('ticker', ''))
        tgt_pct  = float(row['weight']) * 100
        bef_pct  = float(row['_before_pct'])
        aft_pct  = float(row['_after_pct'])
        bef_diff = bef_pct - tgt_pct
        aft_diff = aft_pct - tgt_pct

        lines.append(
            f'• *{nm}* ({ticker})\n'
            f'  목표 {tgt_pct:.1f}%\n'
            f'  전 {bef_pct:.1f}% ({_fmt_diff(bef_diff)}) → 후 {aft_pct:.1f}% ({_fmt_diff(aft_diff)})'
        )

    return '\n'.join(lines)


_IRP_PLAN_REQUIRED_COLUMNS = (
    'ticker', 'required_transaction', 'required_quantity', 'weight', 'current_pct',
)


def format_irp_plan_summary(
    plan_df: pd.DataFrame,
    account_type: str,
    dt,
    sheet_url: str = None,
) -> str:
    """IRP 리밸런싱 플랜 생성 알림.

    IRP는 KIS API로 자동 매매가 불가능해 거래 실행 없이 플랜만 생성된다.
    사용자는 시트의 IRP_action_plan을 보고 KIS 앱에서 수동 매수/매도한다.
    포맷: 헤더 + 매수/매도 카운트 + 종목별 액션 + 시트 URL.

    필수 컬럼이 누락되면 KeyError로 즉시 실패 (silent failure 방지).
    required_quantity가 0인 행은 출력에서 제외 (KOFR buy 0주 같은 헷갈리는 표기 방지).
    """
    # 필수 컬럼 검증 — 누락 시 KeyError로 즉시 폭발 (silent 위장 방지)
    missing = [c for c in _IRP_PLAN_REQUIRED_COLUMNS if c not in plan_df.columns]
    if missing:
        raise KeyError(f'format_irp_plan_summary 필수 컬럼 누락: {missing}')

    if hasattr(dt, 'strftime'):
        dt_str = dt.strftime('%Y-%m-%d %H:%M KST') if hasattr(dt, 'hour') else dt.strftime('%Y-%m-%d') + ' KST'
    else:
        dt_str = str(dt)

    df = plan_df[plan_df['ticker'] != 'CASH'].copy()
    # required_transaction이 buy/sell이고 required_quantity > 0인 행만 (변동 0인 종목 제외)
    df['required_quantity'] = pd.to_numeric(df['required_quantity'], errors='coerce').fillna(0).astype(int)
    actionable = df[
        df['required_transaction'].isin(['buy', 'sell']) & (df['required_quantity'] > 0)
    ]

    buy_rows = actionable[actionable['required_transaction'] == 'buy']
    sell_rows = actionable[actionable['required_transaction'] == 'sell']

    lines = [
        f'*[{account_type}] IRP 리밸런싱 플랜 생성* · {dt_str}',
        f'IRP는 자동 매매 불가 — KIS 앱에서 수동으로 매수/매도 진행하세요.',
        f'매수 {len(buy_rows)}건 · 매도 {len(sell_rows)}건 (CASH·변동 없음 제외)',
    ]
    if sheet_url:
        lines.append(f'<{sheet_url}|IRP_action_plan 시트 보기 →>')
    lines.append('')

    if actionable.empty:
        lines.append('_플랜 변동 사항 없음 — 현재 비중이 목표와 일치._')
        return '\n'.join(lines)

    for _, row in actionable.iterrows():
        nm     = str(row.get('stock_nm', row['ticker']))
        ticker = str(row['ticker'])
        action = str(row['required_transaction']).upper()
        qty    = int(row['required_quantity'])
        tgt_pct = float(row['weight']) * 100
        cur_pct = float(row['current_pct'])
        action_emoji = '🟢' if action == 'BUY' else '🔴'
        lines.append(
            f'{action_emoji} *{nm}* ({ticker}) — {action} `{qty}`주\n'
            f'  목표 {tgt_pct:.1f}% · 현재 {cur_pct:.1f}%'
        )

    return '\n'.join(lines)
