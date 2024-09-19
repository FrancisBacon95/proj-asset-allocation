from datetime import datetime
import inspect
from functools import wraps
from src.logger import get_logger

logger = get_logger(__name__)

def log_method_call(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        # 현재 메서드 이름 가져오기
        method_name = func.__name__
        
        class_name = None
        if args:
            instance = args[0]
            class_name = instance.__class__.__name__

        # 현재 메서드의 파라미터와 값 가져오기
        signature = inspect.signature(func)
        bound_arguments = signature.bind(*args, **kwargs)
        bound_arguments.apply_defaults()

        # 메서드 이름과 파라미터 출력
        if class_name != None:
            logger.info("EXECUTE: %s.%s", class_name, method_name)
        else:
            logger.info("EXECUTE: %s", method_name)
        for arg, value in bound_arguments.arguments.items():
            if isinstance(value, (bool, int, float, str, datetime, list, dict)):
                logger.info("    - %s: %s", arg, value)
        return func(*args, **kwargs)
    return wrapper