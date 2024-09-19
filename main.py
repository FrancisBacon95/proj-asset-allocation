from src.static_allocation import StaticAllocationAgent
from src.logger import get_logger

logger = get_logger(__name__)

agent = StaticAllocationAgent()
agent.run()