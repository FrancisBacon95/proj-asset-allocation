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
