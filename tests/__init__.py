import logging

# Тесты специально вызывают ошибки сети/TG — их предупреждения в выводе не нужны
logging.disable(logging.CRITICAL)
