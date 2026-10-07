import logging
import os

_DEFAULT_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# 逐函数打印签名/参数/返回值的调试切面。它信息量极大（一次 store 调用能打 20 行），
# 开着会把真正有用的业务日志淹掉，所以默认静音，需要时用 DEBUG_ASPECT=true 打开。
_NOISY_LOGGERS = {"debug_aspect"}


def _env_true(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _env_level(default: int = logging.INFO) -> int:
    raw = (os.getenv("LOG_LEVEL") or "").strip().upper()
    if not raw:
        return default
    level = logging.getLevelName(raw)
    return level if isinstance(level, int) else default


def get_logger(name="default", level=None):
    """配置日志系统

    Args:
        name:  logger 名称
        level: 日志级别。不传则取 LOG_LEVEL 环境变量，再退回 INFO。
    """
    if level is None:
        level = _env_level()

    # 只配置一次根日志器：重复 basicConfig 是 no-op，但每次调用都算一遍格式串很浪费
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=_env_level(), format=_DEFAULT_FORMAT,
                            handlers=[logging.StreamHandler()])

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 噪声日志器：没显式开启就压到 WARNING
    if name in _NOISY_LOGGERS and not _env_true("DEBUG_ASPECT"):
        logger.setLevel(logging.WARNING)

    return logger
