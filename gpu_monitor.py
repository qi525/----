# intel_arc_monitor.py

import tkinter as tk
import time
import sys
import subprocess
import platform
import re
from loguru import logger

# --- Loguru 配置 (完美的日志输出) ---
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>", enqueue=True)

# --------------------------------------------------------------------------

class IntelArcMonitorApp:
    """
    Intel Arc A770 实时监控应用程序，使用系统命令行工具获取GPU信息，并用tkinter显示和警报。
    """
    
    # 显存警告阈值：8GB (转换为Bytes)
    # 用户要求：如果显存占用一直少于8GB，则警告webui中断
    MEMORY_WARN_THRESHOLD_GB = 8
    MEMORY_WARN_THRESHOLD_BYTES = MEMORY_WARN_THRESHOLD_GB * 1024**3
    
    # 监控更新间隔 (毫秒)
    UPDATE_INTERVAL_MS = 1500  # PowerShell命令较慢，间隔设长一些

    def __init__(self, master):
        """
        初始化应用程序和tkinter界面。
        """
        self.master = master
        master.title("Intel Arc A770 实时监控")
        master.geometry("400x200")
        
        # 检查操作系统
        self.os_type = platform.system()
        if self.os_type != "Windows":
             logger.warning(f"当前操作系统为 {self.os_type}，本程序主要针对 Windows/PowerShell 设计。")
             logger.warning("在 Linux 上，可能需要修改 _get_gpu_stats_windows 方法，使用 'xpumcli' 或 'intel_gpu_top' 等工具。")


        # 初始化计数器
        self.total_checks = 0
        self.success_count = 0
        self.failure_count = 0

        # 创建并配置tkinter界面
        self._setup_gui()
        
        # 启动定时更新
        self.update_gpu_info()
        
        logger.info("Intel Arc GPU 监控应用启动成功。")

    def _setup_gui(self):
        """
        配置GUI界面元素。
        """
        # GPU 名称标签
        self.name_label = tk.Label(self.master, text="GPU: Intel Arc A770 16GB", font=('Arial', 12, 'bold'))
        self.name_label.pack(pady=5)
        
        # 算力利用率标签
        self.utilization_label = tk.Label(self.master, text="GPU 算力利用率: N/A", font=('Arial', 14))
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
        通过 PowerShell 性能计数器获取 GPU 算力利用率和显存占用。
        由于 PowerShell 调用较慢，且显存值可能包含多个进程，这里进行汇总。
        """
        if self.os_type != "Windows":
            raise NotImplementedError("非 Windows 操作系统，请使用 _get_gpu_stats_linux 方法实现。")
            
        try:
            # --- 1. 获取 GPU 算力利用率 (3D 引擎) ---
            # 命令：获取 'GPU Engine(*engtype_3D)\Utilization Percentage' 的 CookedValue
            # 我们假设3D引擎的利用率代表了Stable Diffusion的算力利用率
            util_cmd = r'powershell -ExecutionPolicy Bypass -Command "((Get-Counter \"\GPU Engine(*engtype_3D)\Utilization Percentage\").CounterSamples | Select-Object -ExpandProperty CookedValue | Measure-Object -Average).Average"'
            result = subprocess.run(util_cmd, capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            gpu_util = float(result.stdout.strip())
            
            # --- 2. 获取 显存占用 (Local Usage) ---
            # 命令：获取所有 'GPU Process Memory(*)\Local Usage' 的 CookedValue 并求和
            # Local Usage 是指 GPU 本地显存的使用量（字节）
            mem_cmd = r'powershell -ExecutionPolicy Bypass -Command "((Get-Counter \"\GPU Process Memory(*)\Local Usage\").CounterSamples | Select-Object -ExpandProperty CookedValue | Measure-Object -Sum).Sum"'
            result = subprocess.run(mem_cmd, capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            mem_used_bytes = float(result.stdout.strip() or 0) # 结果可能是空字符串 '0' 或 None
            
            # 硬编码总显存 (Intel A770 16GB)
            mem_total_bytes = 16 * 1024**3
            
            return gpu_util, mem_used_bytes, mem_total_bytes

        except subprocess.CalledProcessError as e:
            logger.error(f"PowerShell 命令执行失败，错误代码: {e.returncode}，输出: {e.stderr.strip()}")
            raise RuntimeError("无法通过 PowerShell 获取 GPU 数据。请检查系统权限或性能计数器是否正常。")
        except ValueError:
            logger.error(f"PowerShell 返回非数值结果，可能性能计数器未启用或不可用。")
            raise RuntimeError("解析 GPU 数据失败。")
        except Exception as e:
            logger.error(f"获取 GPU 数据时发生未知错误: {e}")
            raise RuntimeError("未知错误：无法获取 GPU 数据。")


    def update_gpu_info(self):
        """
        获取GPU信息并更新界面，同时检查警报条件。
        """
        
        self.total_checks += 1
        
        try:
            # 调用 Windows 平台的数据获取方法
            gpu_util, mem_used_bytes, mem_total_bytes = self._get_gpu_stats_windows()
            
            mem_used_gb = mem_used_bytes / 1024**3
            mem_total_gb = mem_total_bytes / 1024**3
            
            # 更新界面标签
            self.utilization_label.config(text=f"GPU 算力利用率: {gpu_util:.2f}%")
            self.memory_label.config(text=f"显存占用: {mem_used_gb:.2f} GB / {mem_total_gb:.2f} GB")
            
            # --- 3. 检查警报条件 ---
            if mem_used_bytes < self.MEMORY_WARN_THRESHOLD_BYTES:
                # 显存占用低于8GB阈值，触发警报
                self.failure_count += 1
                alarm_msg = f"!!! 警报: Webui 可能已中断 !!! 显存占用: {mem_used_gb:.2f} GB (低于 {self.MEMORY_WARN_THRESHOLD_GB} GB)"
                self.warning_label.config(text=alarm_msg, fg="red")
                logger.warning(alarm_msg)
            else:
                # 显存占用正常
                self.success_count += 1
                normal_msg = "监控正常 (显存高于 8 GB)"
                self.warning_label.config(text=normal_msg, fg="green")
                logger.info(f"状态正常 | 算力: {gpu_util:.2f}% | 显存: {mem_used_gb:.2f} GB")

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
        # 启动主程序
        root = tk.Tk()
        app = IntelArcMonitorApp(root)
        
        root.mainloop()
            
    except Exception as main_e:
        logger.critical(f"程序主循环发生致命错误: {main_e}")