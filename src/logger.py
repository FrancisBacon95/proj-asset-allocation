import logging
import os

# 로그 레벨 설정 (기본: INFO)
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()

# 로깅 포맷 설정
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

def get_logger(name: str) -> logging.Logger:
    # 로거 생성
    logger = logging.getLogger(name)
    logger.setLevel(LOG_LEVEL)

    # 콘솔 핸들러 설정
    console_handler = logging.StreamHandler()
    console_handler.setLevel(LOG_LEVEL)

    # 로그 포맷 설정
    formatter = logging.Formatter(LOG_FORMAT)
    console_handler.setFormatter(formatter)

    # 핸들러가 없는 경우 추가 (중복 방지)
    if not logger.hasHandlers():
        logger.addHandler(console_handler)

    return logger