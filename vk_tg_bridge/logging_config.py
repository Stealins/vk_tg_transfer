import logging
import logging.handlers
import os
import re

from . import config

# Секреты, которые не должны попадать в лог (в т.ч. в трассировки исключений):
# токен TG-бота в URL api.telegram.org/bot<токен>/ и VK access_token в параметрах запросов
SECRET_PATTERNS = [
    (re.compile(r'bot\d+:[A-Za-z0-9_-]+'), 'bot<TG_TOKEN>'),
    (re.compile(r'(access_token=)[^&\s"\']+'), r'\1<VK_TOKEN>'),
    (re.compile(r'(["\']access_token["\']\s*:\s*["\'])[^"\']+'), r'\1<VK_TOKEN>'),
]


def redact(text):
    for pattern, repl in SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFormatter(logging.Formatter):
    """Форматтер, вырезающий токены из итоговой строки (сообщение + traceback)."""

    def format(self, record):
        return redact(super().format(record))


def setup_logging():
    """Вызывается только из точек входа (__main__, vk_auth как скрипт); модули лишь берут logging.getLogger."""
    root_logger = logging.getLogger()
    # Защита от повторного вызова — обработчики добавляем только один раз
    if getattr(root_logger, '_vk_tg_configured', False):
        return root_logger

    log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
    log_file = os.getenv('LOG_FILE') or config.LOG_FILE

    formatter = RedactingFormatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger.setLevel(log_level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    root_logger._vk_tg_configured = True

    return root_logger
