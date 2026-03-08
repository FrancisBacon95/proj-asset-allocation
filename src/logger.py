import logging
import os
from datetime import datetime
import inspect
from functools import wraps

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

_logger = get_logger(__name__)

def log_method_call(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        method_name = func.__name__

        class_name = None
        if args:
            instance = args[0]
            class_name = instance.__class__.__name__

        signature = inspect.signature(func)
        bound_arguments = signature.bind(*args, **kwargs)
        bound_arguments.apply_defaults()

        if class_name is not None:
            _logger.info("EXECUTE: %s.%s", class_name, method_name)
        else:
            _logger.info("EXECUTE: %s", method_name)
        for arg, value in bound_arguments.arguments.items():
            if isinstance(value, (bool, int, float, str, datetime, list, dict)):
                _logger.info("    - %s: %s", arg, value)
        return func(*args, **kwargs)
    return wrapper
