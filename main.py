from src.config.env import GOOGLE_SHEET_URL
from src.static_allocation import StaticAllocationAgent
from src.logger import get_logger

logger = get_logger(__name__)
StaticAllocationAgent(account_type='ISA', gs_url=GOOGLE_SHEET_URL).run()