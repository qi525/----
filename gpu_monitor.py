# intel_arc_monitor_with_beep.py

import tkinter as tk
import time
import sys
import subprocess
from just_playback import Playback # <-- 使用 just_playback
import platform
from loguru import logger
import winsound # <-- 用于错误回退，以防 just_playback 播放失败

# --- Loguru 配置 (完美的日志输出) ---
logger.remove()
# 完美的日志输出格式
logger.add(sys.stderr, level="INFO", format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>", enqueue=True)

# --------------------------------------------------------------------------

class IntelArcMonitorApp:
    """
    Intel Arc A770 实时监控应用程序，使用系统命令行工具获取GPU信息，
    并用tkinter显示和警报（使用 just_playback 确保 WAV 文件完整播放，并在状态正常时强制停止）。
    """
    
    # 显存警告阈值：8GB (转换为Bytes)
    MEMORY_WARN_THRESHOLD_GB = 8
    MEMORY_WARN_THRESHOLD_BYTES = MEMORY_WARN_THRESHOLD_GB * 1024**3
    
    # 监控更新间隔 (毫秒)
    UPDATE_INTERVAL_MS = 1500  # 1.5秒
    
    # 蜂鸣声参数 (已不再使用，但保留常量)
    BEEP_FREQUENCY = 1500  # 频率 1500 Hz
    BEEP_DURATION = 300    # 持续 300 毫秒
    
    # 自定义警报声音文件
    ALARM_WAV_FILE = "7 you.wav"

    def __init__(self, master):
        """
        初始化应用程序和tkinter界面。
        """
        # just_playback 播放器对象
        self.playback = None 
        
        self.master = master
        master.title("Intel Arc A770 实时监控 (自定义警报 - just_playback)")
        master.geometry("400x220")
        
        self.os_type = platform.system()
        if self.os_type != "Windows":
             logger.warning(f"当前操作系统为 {self.os_type}。注意：本应用的核心功能和声音警报主要在 Windows 上有效。")

        # 初始化计数器
        self.total_checks = 0
        self.success_count = 0
        self.failure_count = 0
        
        # 【核心改动】：警报状态标志。True 表示警报条件已触发且音乐已启动。
        self.is_alarm_active = False 

        # 创建并配置tkinter界面
        self._setup_gui()
        
        # 启动定时更新
        self.update_gpu_info()
        
        # 【重要】：初始化播放器并预加载 WAV 文件
        if self.os_type == "Windows":
            try:
                self.playback = Playback()
                self.playback.load_file(self.ALARM_WAV_FILE)
                logger.success(f"警报文件 '{self.ALARM_WAV_FILE}' 加载成功。")
            except Exception as e:
                logger.error(f"初始化或加载警报文件失败（just_playback）：{e}。将使用系统默认警报音作为回退。")
        
        logger.info("Intel Arc GPU 监控应用启动成功。")

    def _setup_gui(self):
        """
        配置GUI界面元素。
        """
        # GPU 名称标签
        self.name_label = tk.Label(self.master, text="GPU: Intel Arc A770 16GB", font=('Arial', 12, 'bold'))
        self.name_label.pack(pady=5)
        
        # 算力利用率标签
        self.utilization_label = tk.Label(self.master, text="GPU 综合利用率: N/A", font=('Arial', 14))
        self.utilization_label.pack(pady=5)

        # 显存占用标签
        self.memory_label = tk.Label(self.master, text="显存占用: N/A", font=('Arial', 14))
        self.memory_label.pack(pady=5)

        # 警告标签
        self.warning_label = tk.Label(self.master, text="监控正常", font=('Arial', 12), fg="green")
        self.warning_label.pack(pady=5)

        # 日志计数标签
        self.log_count_label = tk.Label(self.master, text="总次数: 0 | 正常: 0 | 警报: 0", font=('Arial', 10), anchor='w')
        self.log_count_label.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)


    def _get_gpu_stats_windows(self):
        """
        [Windows 平台专用]
        通过 PowerShell 性能计数器获取 GPU 综合利用率和显存占用。
        """
        if self.os_type != "Windows":
            # 如果不是 Windows，则不执行 PowerShell 命令，避免报错
            raise NotImplementedError("非 Windows 操作系统，无法执行 PowerShell 命令。")
            
        try:
            # --- 1. 获取 GPU 综合利用率 ---
            # 使用 "\GPU Engine(*)\Utilization Percentage" 获取所有引擎的利用率，并计算平均值，
            # 以最大程度地接近任务管理器显示的“总利用率”。
            util_cmd = r'powershell -ExecutionPolicy Bypass -Command "((Get-Counter \"\GPU Engine(*)\Utilization Percentage\").CounterSamples | Select-Object -ExpandProperty CookedValue | Measure-Object -Average).Average"'
            result = subprocess.run(util_cmd, capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            gpu_util = float(result.stdout.strip())
            
            # --- 2. 获取 显存占用 (Local Usage) ---
            mem_cmd = r'powershell -ExecutionPolicy Bypass -Command "((Get-Counter \"\GPU Process Memory(*)\Local Usage\").CounterSamples | Select-Object -ExpandProperty CookedValue | Measure-Object -Sum).Sum"'
            result = subprocess.run(mem_cmd, capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            mem_used_bytes = float(result.stdout.strip() or 0)
            
            # 硬编码总显存
            mem_total_bytes = 16 * 1024**3
            
            return gpu_util, mem_used_bytes, mem_total_bytes

        except subprocess.CalledProcessError as e:
            logger.error(f"PowerShell 命令执行失败，错误代码: {e.returncode}，输出: {e.stderr.strip()}")
            raise RuntimeError("无法通过 PowerShell 获取 GPU 数据。请检查系统权限。")
        except Exception as e:
            logger.error(f"获取 GPU 数据时发生未知错误: {e}")
            raise RuntimeError("未知错误：无法获取 GPU 数据。")


    def _play_beep_alarm(self):
        """
        【核心警报功能】播放自定义 WAV 文件（使用 just_playback）。
        此函数只负责启动播放，不负责停止或状态管理。
        """
        logger.info("尝试播放声音警报...")
        if self.os_type == "Windows":
            if self.playback:
                try:
                    # 确保停止上一次的播放（防止警报堆叠）
                    if self.playback.active:
                        self.playback.stop() 
                    
                    # 关键步骤：重置播放位置到文件开头，确保每次都完整播放
                    self.playback.seek(0) 
                    
                    # 播放预加载的文件 (非阻塞)
                    self.playback.play() 
                except Exception as e:
                    logger.error(f"播放自定义声音文件 '{self.ALARM_WAV_FILE}' 失败（just_playback）：{e}。")
                    # 播放失败时，回退到播放系统警报音
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            else:
                # 如果播放器初始化失败，回退到系统警报音
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        else:
             # 针对非 Windows 系统 (如 Linux/macOS) 的备用方案：
             # 打印 ASCII 铃声字符 '\a' (BEL)，尝试在终端发出声响。
             print('\a', end='', flush=True) 
             logger.warning("非 Windows 系统，使用终端铃声作为替代。")


    def update_gpu_info(self):
        """
        获取GPU信息并更新界面，同时检查警报条件和控制音乐播放状态。
        """
        
        self.total_checks += 1
        
        try:
            gpu_util, mem_used_bytes, mem_total_bytes = self._get_gpu_stats_windows()
            
            mem_used_gb = mem_used_bytes / 1024**3
            mem_total_gb = mem_total_bytes / 1024**3
            
            # 更新界面标签
            self.utilization_label.config(text=f"GPU 综合利用率: {gpu_util:.2f}%")
            self.memory_label.config(text=f"显存占用: {mem_used_gb:.2f} GB / {mem_total_gb:.2f} GB")
            
            # --- 检查警报条件 ---
            if mem_used_bytes < self.MEMORY_WARN_THRESHOLD_BYTES: # 警报条件满足
                # 显存占用低于8GB阈值，触发警报
                self.failure_count += 1
                alarm_msg = f"!!! 警报: Webui 可能已中断 !!! 显存占用: {mem_used_gb:.2f} GB (低于 {self.MEMORY_WARN_THRESHOLD_GB} GB)"
                self.warning_label.config(text=alarm_msg, fg="red")
                logger.warning(alarm_msg)
                
                # 只有当警报状态首次激活时才播放音乐，防止反复中断
                if not self.is_alarm_active:
                    self.is_alarm_active = True
                    # --- 触发声音警告 (开始循环播放) ---
                    self._play_beep_alarm() 
                
            else: # 警报条件不满足 (显存高于 8 GB)
                # 显存占用正常
                self.success_count += 1
                normal_msg = "监控正常 (显存高于 8 GB)"
                
                # 如果警报状态是 True，表示警报刚刚解除，需要强制停止音乐
                if self.is_alarm_active:
                    self.is_alarm_active = False
                    if self.playback and self.playback.active:
                        self.playback.stop()
                        logger.info("警报条件解除，已强制停止警报音乐。")

                self.warning_label.config(text=normal_msg, fg="green")
                logger.info(f"状态正常 | 综合利用率: {gpu_util:.2f}% | 显存: {mem_used_gb:.2f} GB")

        except Exception as e:
            error_msg = f"数据获取失败: {e}"
            logger.error(error_msg)
            self.warning_label.config(text=f"错误: {error_msg}", fg="red")
            self.failure_count += 1
        
        # 更新日志计数器
        self.log_count_label.config(text=f"总次数: {self.total_checks} | 正常: {self.success_count} | 警报: {self.failure_count}")

        # 设置定时器，再次调用自身，实现实时更新
        self.master.after(self.UPDATE_INTERVAL_MS, self.update_gpu_info)

# --------------------------------------------------------------------------

if __name__ == '__main__':
    try:
        root = tk.Tk()
        app = IntelArcMonitorApp(root)
        root.mainloop()
            
    except Exception as main_e:
        logger.critical(f"程序主循环发生致命错误: {main_e}")