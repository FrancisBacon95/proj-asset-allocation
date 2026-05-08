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
    """리밸런싱 실행 결과 DataFrame을 Slack 메시지 형식의 문자열로 변환한다."""
    lines = []
    for _, row in df.iterrows():
        lines.append(
            _format_stock_header(row) + "\n"
            f"- required_transaction: `{row['required_transaction']}`\n"
            f"- required_quantity: `{row['required_quantity']}`\n"
            f"- enable_quantity: `{row['enable_quantity']}`\n"
            f"- transaction_quantity: `{row['transaction_quantity']}`\n"
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
    """리밸런싱 결과를 Slack mrkdwn 형식의 표로 포맷팅."""
    # dt가 date/datetime 모두 허용
    if hasattr(dt, 'strftime'):
        dt_str = dt.strftime('%Y-%m-%d %H:%M KST') if hasattr(dt, 'hour') else dt.strftime('%Y-%m-%d') + ' KST'
    else:
        dt_str = str(dt)

    df = result_df[result_df['ticker'] != 'CASH'].copy()

    # direction 계산: required_transaction 컬럼 기준
    def _direction(val):
        if pd.isna(val) or val is None:
            return 0
        v = str(val).strip().lower()
        if v == 'buy':
            return 1
        if v == 'sell':
            return -1
        return 0

    # 숫자 변환을 방향 계산 전에 통일
    for col in ('transaction_quantity', 'current_value', 'current_price', 'weight', 'current_pct'):
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # is_success=True인 경우만 실제 체결로 인정
    df['_direction'] = df.apply(
        lambda r: _direction(r['required_transaction']) if r.get('is_success') is True else 0,
        axis=1,
    )

    df['_after_value'] = df['current_value'] + df['_direction'] * df['transaction_quantity'] * df['current_price']

    # 리밸런싱은 총 자산 규모를 바꾸지 않으므로 분모를 고정값으로 사용
    total_portfolio = df['current_value'].sum() + remaining_cash
    df['_after_pct'] = df['_after_value'] / total_portfolio * 100 if total_portfolio > 0 else 0.0
    df['current_pct'] = df['current_value'] / total_portfolio * 100

    # 헤더
    lines = [
        f'*[{account_type}] 리밸런싱 완료* · {dt_str}',
        f'잔여 예수금: *{remaining_cash:,}원*',
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
        bef_pct  = float(row['current_pct'])
        aft_pct  = float(row['_after_pct'])
        bef_diff = bef_pct - tgt_pct
        aft_diff = aft_pct - tgt_pct

        lines.append(
            f'• *{nm}* ({ticker})\n'
            f'  목표 {tgt_pct:.1f}%\n'
            f'  전 {bef_pct:.1f}% ({_fmt_diff(bef_diff)}) → 후 {aft_pct:.1f}% ({_fmt_diff(aft_diff)})'
        )

    return '\n'.join(lines)
