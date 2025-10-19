# monitor_config.py - 应用程序的配置和常量

import datetime

class MonitorConfig:
    """
    集中管理应用程序的所有配置常量和阈值。
    """
    # --- 核心警报阈值 (VRAM/显存) ---
    MEMORY_WARN_THRESHOLD_GB = 8
    
    # 连续警报次数阈值 (用于实现警报延迟触发)
    WARN_COUNT_THRESHOLD = 7
    
    # 虚拟内存风险提醒阈值 (仅用于橙色提醒)
    VIRTUAL_MEMORY_WARN_THRESHOLD_GB = 80
    
    # 监控更新间隔 (毫秒)
    UPDATE_INTERVAL_MS = 1500  # 1.5秒
    
    # 自定义警报声音文件 (Windows Only)
    ALARM_WAV_FILE = "7 you.wav"
    
    # --- UI 配置 (便于调整 UI 布局) ---
    INITIAL_WINDOW_SIZE = "450x610" # 窗口初始大小
    BAR_WIDTH = 250
    BAR_HEIGHT = 15
    
    # --- 网络监控配置 ---
    # 最大的预期带宽（MB/s），用于进度条的百分比计算 (100MB/s 约为 800Mbps)
    MAX_BANDWIDTH_MBPS = 100 

    # --- Webui 文件监控配置 (高频修改点) ---
    # Webui 输出目录的基路径 (用户自定义路径)
    WEBUI_OUTPUT_BASE_DIR = r'C:\stable-diffusion-webui\outputs\txt2img-images'
    # 监测 Webui 文件数量的周期 (秒)
    WEBUI_CHECK_INTERVAL_SECONDS = 30
    # 连续未增加文件数量的警报周期阈值 (连续 2 个 30 秒周期未增加，则警报)
    WEBUI_WARN_CYCLE_THRESHOLD = 2 
    
    # --- 内部使用的常量 (不常修改) ---
    # 显存警告阈值 (转换为Bytes)
    MEMORY_WARN_THRESHOLD_BYTES = MEMORY_WARN_THRESHOLD_GB * 1024**3
    # 虚拟内存风险提醒阈值 (转换为Bytes)
    VIRTUAL_MEMORY_WARN_THRESHOLD_BYTES = VIRTUAL_MEMORY_WARN_THRESHOLD_GB * 1024**3
    # VM 记录周期 (秒)
    VM_LOG_INTERVAL_SECONDS = 1800 # 30 分钟

# 将配置转换为方便访问的常量
CONFIG = MonitorConfig()