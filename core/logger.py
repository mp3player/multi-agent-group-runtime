import logging
from typing import Final
from pathlib import Path

# 配置日志目录
LOG_DIR: Final[Path] = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

def setup_logging(level: int = logging.INFO) -> None:
    """初始化全局日志配置。"""
    log_format = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 文件处理器
    file_handler = logging.FileHandler(LOG_DIR / "mas.log", encoding='utf-8')
    file_handler.setFormatter(log_format)

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_format)

    # 根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # 清除已有的 handler 防止重复
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
        
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

def get_logger(name: str) -> logging.Logger:
    """获取指定名称的 logger。"""
    return logging.getLogger(name)
