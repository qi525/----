# intel_arc_monitor_with_beep.py - 核心功能: 持续监控 VRAM 使用情况，确保 Webui 等任务持续运行

import tkinter as tk
import time
import sys
import subprocess
import psutil # <-- 用于获取系统CPU和内存信息
from just_playback import Playback
import platform
from loguru import logger
import winsound
# import random # <-- [移除] 不再需要模拟数据

# --- Loguru 配置 (完美的日志输出) ---
logger.remove()
# 完美的日志输出格式
logger.add(sys.stderr, level="INFO", format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>", enqueue=True)

# --------------------------------------------------------------------------

class IntelArcMonitorApp:
    """
    Intel Arc A770 实时监控应用程序，使用系统命令行工具获取GPU信息，
    并用tkinter显示和警报，现已增强数据条和颜色警报功能。
    
    数据顺序：CPU -> 物理内存 -> 虚拟内存 -> GPU 性能占用 -> 专有显存 -> 下载速度 -> 上传速度
    
    【程序核心功能】
    ------------------------------------------------------------------------------------
    核心目标: 持续监控专有显存（VRAM）使用情况，当其低于设定阈值（VRAM < 8GB）时，
    通过声音和UI进行警报，以确认Webui等任务是否意外中断。
    次要功能: 监控虚拟内存（VM）使用量，超过 80GB 时给出橙色风险提示，并周期性记录其增长量。
    ------------------------------------------------------------------------------------
    """
    
    # 显存警告阈值：8GB (转换为Bytes)
    MEMORY_WARN_THRESHOLD_GB = 8
    MEMORY_WARN_THRESHOLD_BYTES = MEMORY_WARN_THRESHOLD_GB * 1024**3
    
    # 连续警报次数阈值 (用于实现警报延迟触发)
    WARN_COUNT_THRESHOLD = 7
    
    # 虚拟内存风险提醒阈值：80GB (仅用于橙色提醒，不触发铃声警报)
    VIRTUAL_MEMORY_WARN_THRESHOLD_GB = 80
    VIRTUAL_MEMORY_WARN_THRESHOLD_BYTES = VIRTUAL_MEMORY_WARN_THRESHOLD_GB * 1024**3

    # 监控更新间隔 (毫秒)
    UPDATE_INTERVAL_MS = 1500  # 1.5秒
    
    # 自定义警报声音文件
    ALARM_WAV_FILE = "7 you.wav"
    
    # 数据条尺寸常量
    BAR_WIDTH = 250
    BAR_HEIGHT = 15

    def __init__(self, master):
        """
        初始化应用程序和tkinter界面。
        """
        # just_playback 播放器对象
        self.playback = None 
        
        self.master = master
        master.title("Intel Arc A770 实时监控 (数据条增强)")
        # 增大窗口以容纳数据条
        master.geometry("450x550") 
        
        self.os_type = platform.system()
        if self.os_type != "Windows":
             logger.warning(f"当前操作系统为 {self.os_type}。注意：本应用的核心功能和声音警报主要在 Windows 上有效。")

        # 初始化计数器
        self.total_checks = 0
        self.success_count = 0
        self.failure_count = 0
        # 警报状态标志。True 表示警报条件已满足阈值且音乐已启动。
        self.is_alarm_active = False 
        # 连续触发警报条件的次数计数器
        self.consecutive_warn_count = 0
        
        # 【新增】：正式报警开始时间（首次播放时间）
        self.alarm_start_time = None 
        # 【新增】：歌曲循环播放次数计数器
        self.playback_count = 0 
        
        # --- 周期性 VM 记录变量 ---
        # 第一次记录
        self.first_vm_record_time = None
        self.first_vm_used_gb = None
        # 上次记录
        self.last_vm_record_time = None
        self.last_vm_used_gb = None
        
        # --- 新增网络状态追踪变量 ---
        self.last_net_bytes_sent = 0
        self.last_net_bytes_recv = 0
        self.last_update_time = time.time() # 记录上次更新的时间戳，用于计算速度

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

    def _setup_progress_bar(self, name):
        """
        为指定的指标设置进度条组件。
        进度条由一个灰色的背景Label和一个动态宽度的填充Label组成，通过 place() 方法进行控制，
        以确保像素级的准确对齐。
        """
        # 创建一个Frame来容纳背景和填充，以便对齐
        frame = tk.Frame(self.master, height=self.BAR_HEIGHT, bg='SystemButtonFace')
        frame.pack(fill='x', padx=10)
        
        # 灰色背景 (整体宽度, 使用 place 确保像素定位和宽度)
        bg_bar = tk.Label(frame, bg='#CCCCCC')
        bg_bar.place(x=0, y=0, width=self.BAR_WIDTH, height=self.BAR_HEIGHT) 
        
        # 颜色填充条 (动态宽度，初始宽度为 0)
        fill_bar = tk.Label(frame, bg='green', height=1)
        # 使用 place() 保证它能叠加在 bg_bar 上，并可以独立控制宽度
        fill_bar.place(x=0, y=0, width=0, height=self.BAR_HEIGHT)
        
        # 将组件存入实例变量，以便在 update_gpu_info 中访问和更新
        setattr(self, f'{name}_fill_bar', fill_bar)
        setattr(self, f'{name}_bg_bar', bg_bar)

    def _get_color(self, percentage):
        """
        根据百分比返回颜色代码（绿、橙、红）实现视觉警报。
        < 50%: 绿色 | 50% - 75%: 橙色 | > 75%: 红色
        """
        if percentage >= 75:
            return 'red'
        elif percentage >= 50:
            return 'orange'
        else:
            return 'green'


    def _setup_gui(self):
        """
        配置GUI界面元素，并按照指定顺序设置标签和数据条。
        """
        # 调整窗口大小以容纳新增的两个网络指标
        self.master.geometry("450x550")
        
        # GPU 名称标签 (保持不变，作为标题)
        self.name_label = tk.Label(self.master, text="GPU: Intel Arc A770 16GB", font=('Arial', 12, 'bold'))
        self.name_label.pack(pady=5)
        
        # 1. CPU 利用率 (字体统一为 14)
        self.cpu_label = tk.Label(self.master, text="CPU 利用率: N/A", font=('Arial', 14), anchor='w')
        self.cpu_label.pack(fill='x', padx=10, pady=(10, 0))
        self._setup_progress_bar('cpu')

        # 2. 物理内存占用
        self.ram_label = tk.Label(self.master, text="物理内存占用: N/A", font=('Arial', 14), anchor='w')
        self.ram_label.pack(fill='x', padx=10, pady=(10, 0))
        self._setup_progress_bar('ram')

        # 3. 虚拟内存占用
        self.shared_memory_label = tk.Label(self.master, text="虚拟内存占用 (已提交): N/A", font=('Arial', 14), anchor='w')
        self.shared_memory_label.pack(fill='x', padx=10, pady=(10, 0))
        self._setup_progress_bar('vram_system')
        
        # 4. GPU 性能占用 (改为通用性能)
        self.utilization_label = tk.Label(self.master, text="GPU 性能占用: N/A", font=('Arial', 14), anchor='w')
        self.utilization_label.pack(fill='x', padx=10, pady=(10, 0))
        self._setup_progress_bar('gpu_util')

        # 5. 专有显存占用
        self.memory_label = tk.Label(self.master, text="专有显存占用: N/A", font=('Arial', 14), anchor='w')
        self.memory_label.pack(fill='x', padx=10, pady=(10, 0))
        self._setup_progress_bar('vram_local')

        # --- 新增：网络传输速度 ---
        # 6. 下载速度
        self.net_recv_label = tk.Label(self.master, text="下载速度: N/A", font=('Arial', 14), anchor='w')
        self.net_recv_label.pack(fill='x', padx=10, pady=(10, 0))
        self._setup_progress_bar('net_recv')
        
        # 7. 上传速度
        self.net_sent_label = tk.Label(self.master, text="上传速度: N/A", font=('Arial', 14), anchor='w')
        self.net_sent_label.pack(fill='x', padx=10, pady=(10, 0))
        self._setup_progress_bar('net_sent')
        # ---------------------------

        # --- VRAM/VM 状态独立显示 (两行，不同颜色) ---
        # 第一行：专有显存状态（用于确认程序运行）
        self.status_vram_label = tk.Label(self.master, text="状态: 监控正常", font=('Arial', 12, 'bold'), fg="green")
        self.status_vram_label.pack(pady=(5, 0)) # 上方留白
        
        # 第二行：虚拟内存风险提示
        self.status_vm_label = tk.Label(self.master, text="", font=('Arial', 12), fg="SystemButtonFace")
        self.status_vm_label.pack(pady=(0, 5)) # 下方留白

        # 日志计数标签
        self.log_count_label = tk.Label(self.master, text="总次数: 0 | 正常: 0 | 警报触发: 0", font=('Arial', 10), anchor='w')
        self.log_count_label.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)


    def _get_windows_commit_charge(self):
        """
        [Windows 平台专用]
        通过 PowerShell 性能计数器获取系统的 “已提交” 内存（Commit Charge）总数 (Bytes)。
        """
        try:
            # 命令获取 \Memory\Committed Bytes (总提交电荷)
            cmd = r'powershell -ExecutionPolicy Bypass -Command "(Get-Counter \"\Memory\Committed Bytes\").CounterSamples | Select-Object -ExpandProperty CookedValue"'
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            
            # 返回 Committed Bytes (Bytes)
            # 使用 or 0.0 处理 PowerShell 偶尔返回空值的情况
            return float(result.stdout.strip() or 0)
        
        except Exception as e:
            logger.error(f"通过 PowerShell 获取 Committed Bytes 失败: {e}")
            return 0.0

    def _get_system_stats_psutil(self):
        """
        使用 psutil 库获取系统 CPU、物理内存和虚拟内存数据及其占用百分比。
        """
        try:
            # 获取 CPU 利用率 (非阻塞)
            cpu_percent = psutil.cpu_percent(interval=None) 
            
            # 获取 物理内存 (RAM)
            ram_stats = psutil.virtual_memory()
            ram_used_gb = ram_stats.used / 1024**3
            ram_total_gb = ram_stats.total / 1024**3 # 物理内存总量
            ram_percent = ram_stats.percent # 获取物理内存占用百分比
            
            # --- 关键改动：获取 Windows 任务管理器中的 “已提交” 虚拟内存 ---
            swap_stats = psutil.swap_memory()
            
            # 1. 虚拟内存 (已提交使用量 - Commit Charge)
            # 使用 PowerShell 获取 Committed Bytes (与任务管理器保持一致)
            if self.os_type == "Windows":
                 vram_system_used_bytes = self._get_windows_commit_charge()
            else:
                 # 非 Windows 系统，回退到 psutil 的 RAM + Swap 使用量
                 vram_system_used_bytes = swap_stats.used + ram_stats.used

            # 2. 虚拟内存 (总可提交量 - Commit Limit)
            # 总可提交量 = 物理内存总量 + 交换文件总量
            vram_system_total_bytes = swap_stats.total + ram_stats.total
            
            # 返回新的虚拟内存（已提交）数据
            return cpu_percent, ram_used_gb, ram_total_gb, ram_percent, vram_system_used_bytes, vram_system_total_bytes
        
        except Exception as e:
            logger.error(f"通过 psutil 获取系统数据失败: {e}")
            # 失败时返回 None 确保程序不中断
            return None, None, None, None, None, None


    def _get_gpu_stats_windows(self):
        """
        [Windows 平台专用]
        通过 PowerShell 性能计数器获取 GPU **综合性能占用**和专有显存占用。
        
        【注意】：GPU 性能占用已改为获取所有 GPU 引擎的平均利用率，以匹配任务管理器的大纲性能。
        """
        if self.os_type != "Windows":
            raise NotImplementedError("非 Windows 操作系统，无法执行 PowerShell 命令。")
            
        try:
            # --- 1. 获取 GPU 综合性能占用 (接近任务管理器大纲值) ---
            # 命令：获取所有 GPU 引擎的平均利用率 (3D, Compute, Copy, etc.)
            util_cmd = r'powershell -ExecutionPolicy Bypass -Command "((Get-Counter \"\GPU Engine(*)\Utilization Percentage\").CounterSamples | Select-Object -ExpandProperty CookedValue | Measure-Object -Average).Average"'
            result = subprocess.run(util_cmd, capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            
            # 使用 or 0.0 处理 PowerShell 偶尔返回空值的情况
            gpu_util = float(result.stdout.strip() or 0.0) 
            
            logger.debug(f"GPU Util (实际值): {gpu_util:.2f}%")
            
            # --- 2. 获取 专有显存占用 (Local Usage) ---
            mem_cmd = r'powershell -ExecutionPolicy Bypass -Command "((Get-Counter \"\GPU Process Memory(*)\Local Usage\").CounterSamples | Select-Object -ExpandProperty CookedValue | Measure-Object -Sum).Sum"'
            result = subprocess.run(mem_cmd, capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            mem_used_bytes = float(result.stdout.strip() or 0)
            
            # 硬编码总显存 (Intel Arc A770 16GB)
            mem_total_bytes = 16 * 1024**3
            
            # 计算专有显存占用百分比
            vram_local_percent = (mem_used_bytes / mem_total_bytes) * 100 if mem_total_bytes > 0 else 0
            
            return gpu_util, mem_used_bytes, mem_total_bytes, vram_local_percent

        except subprocess.CalledProcessError as e:
            logger.error(f"PowerShell 命令执行失败，错误代码: {e.returncode}，输出: {e.stderr.strip()}")
            # 警告用户需要手动检查性能计数器路径，但此处不中断程序，允许 VM/VRAM 继续工作
            logger.warning("无法获取 GPU 性能计数器。请检查 PowerShell 命令和权限。")
            # 失败时返回 0 或 None，避免程序崩溃
            return 0.0, 0.0, 16 * 1024**3, 0.0 # 返回 0.0% 和默认总显存
        except Exception as e:
            logger.error(f"获取 GPU 数据时发生未知错误: {e}")
            return 0.0, 0.0, 16 * 1024**3, 0.0


    def _play_beep_alarm(self):
        """
        播放自定义 WAV 文件（使用 just_playback）作为警报。
        """
        # 【新增】：记录并打印本次播放是第几次循环
        self.playback_count += 1
        logger.info(f"尝试播放声音警报 (第 {self.playback_count} 次循环)...")
        if self.os_type == "Windows":
            if self.playback:
                try:
                    # 确保停止上一次的播放（防止警报堆叠）
                    if self.playback.active:
                        self.playback.stop() 
                    
                    # 重置播放位置到文件开头，确保每次都完整播放
                    self.playback.seek(0) 
                    # 播放预加载的文件 (非阻塞，恢复单次播放)
                    self.playback.play()
                except Exception as e:
                    logger.error(f"播放自定义声音文件 '{self.ALARM_WAV_FILE}' 失败（just_playback）：{e}。")
                    # 播放失败时，回退到系统警报音
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            else:
                # 如果播放器初始化失败，回退到系统警报音
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        else:
             # 非 Windows 系统备用方案
             print('\a', end='', flush=True) 
             logger.warning("非 Windows 系统，使用终端铃声作为替代。")

    def _update_progress_bar(self, name, percentage):
        """
        根据百分比更新指定指标的数据条 Label 的宽度和颜色。
        """
        # 获取动态填充条的引用
        fill_bar = getattr(self, f'{name}_fill_bar')
        
        # 确保百分比在 0 到 100 之间，防止计算错误
        percentage = max(0, min(100, percentage))
        
        # 计算新的宽度：总宽度 * 百分比。由于 bg_bar 宽度是 BAR_WIDTH 像素，此处计算结果也是像素。
        new_width = int(self.BAR_WIDTH * (percentage / 100))
        # 获取颜色（基于 50%/75% 阈值）
        color = self._get_color(percentage)
        
        # 更新填充条的宽度和背景色
        fill_bar.place(width=new_width)
        fill_bar.config(bg=color)
        
    def _log_vm_usage_periodically(self, current_time, vram_system_used_gb):
        """
        周期性记录虚拟内存（VM）使用量和增量。每 30 分钟记录一次。
        """
        
        # 第一次运行时记录初始值
        if self.first_vm_record_time is None:
            self.first_vm_record_time = current_time
            self.first_vm_used_gb = vram_system_used_gb
            self.last_vm_record_time = current_time
            self.last_vm_used_gb = vram_system_used_gb
            
            log_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_time))
            logger.info("--- 虚拟内存 (VM) 周期记录启动 ---")
            # 控制台写清楚年月日时分秒多少虚拟内存大小
            logger.info(f"【首次记录】{log_time_str} | VM 大小: {vram_system_used_gb:.1f} GB")
            return

        # 检查是否已经过了 30 分钟 (1800 秒)
        time_since_last_record = current_time - self.last_vm_record_time
        
        # 考虑到 UPDATE_INTERVAL_MS = 1.5秒，允许一定的浮动
        if time_since_last_record >= 1800 - 1.0: 
            # 计算增加量
            increase_gb = vram_system_used_gb - self.last_vm_used_gb
            
            log_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_time))
            
            # 打印日志 (年月日时分秒多少虚拟内存大小，增加量)
            logger.info(f"【周期记录】{log_time_str} | VM 大小: {vram_system_used_gb:.1f} GB | 增加量: {increase_gb:.1f} GB (相比上次记录)")
            
            # 更新上次记录值
            self.last_vm_record_time = current_time
            self.last_vm_used_gb = vram_system_used_gb


    def update_gpu_info(self):
        """
        获取GPU和系统信息，更新界面数据和进度条，并检查警报条件。
        """
        
        self.total_checks += 1
        # 记录本次更新的时间
        current_time = time.time()
        
        try:
            # --- 1. 获取 GPU 专有数据 (现为综合性能) ---
            gpu_util, mem_used_bytes, mem_total_bytes, vram_local_percent = self._get_gpu_stats_windows()

            # --- 2. 获取 系统数据 ---
            cpu_percent, ram_used_gb, ram_total_gb, ram_percent, vram_system_used_bytes, vram_system_total_bytes = self._get_system_stats_psutil()
            
            # --- 3. 获取网络 I/O 统计并计算速度 ---
            net_io = psutil.net_io_counters()
            current_bytes_sent = net_io.bytes_sent
            current_bytes_recv = net_io.bytes_recv
            
            time_diff = current_time - self.last_update_time
            
            # 最大的预期带宽（例如 1Gbps / 8 = 125 MB/s），用于进度条的百分比计算
            MAX_BANDWIDTH_MBPS = 100 
            
            # 首次运行时 time_diff 可能为 0 或接近 0，或者 last_bytes 为 0，不进行计算或避免除以零
            if time_diff > 0 and self.last_net_bytes_sent != 0:
                 # 计算下载和上传速度 (Bytes/秒)
                 recv_speed_bps = (current_bytes_recv - self.last_net_bytes_recv) / time_diff
                 sent_speed_bps = (current_bytes_sent - self.last_net_bytes_sent) / time_diff
                 
                 # 转换为 MB/秒
                 recv_speed_mbps = recv_speed_bps / 1024**2
                 sent_speed_mbps = sent_speed_bps / 1024**2
                 
                 # 进度条的百分比计算
                 recv_percent = (recv_speed_mbps / MAX_BANDWIDTH_MBPS) * 100
                 sent_percent = (sent_speed_mbps / MAX_BANDWIDTH_MBPS) * 100
            else:
                 # 初始或计算失败时设置为 0
                 recv_speed_mbps = 0.0
                 sent_speed_mbps = 0.0
                 recv_percent = 0.0
                 sent_percent = 0.0
                 
            # 更新上次的计数器和时间戳
            self.last_net_bytes_sent = current_bytes_sent
            self.last_net_bytes_recv = current_bytes_recv
            self.last_update_time = current_time


            # 格式化 GPU 显存数据
            mem_used_gb = mem_used_bytes / 1024**3
            mem_total_gb = mem_total_bytes / 1024**3
            
            vram_system_used_gb = vram_system_used_bytes / 1024**3
            vram_system_total_gb = vram_system_total_bytes / 1024**3
            
            # =======================================================
            # 更新数据和数据条
            # =======================================================
            if cpu_percent is not None:
                 # 1. CPU 利用率
                 self.cpu_label.config(text=f"CPU 利用率: {cpu_percent:.1f}%")
                 self._update_progress_bar('cpu', cpu_percent)
                 
                 # 2. 物理内存占用
                 self.ram_label.config(text=f"物理内存占用: {ram_used_gb:.1f} GB / {ram_total_gb:.1f} GB ({ram_percent:.1f}%)")
                 self._update_progress_bar('ram', ram_percent)
                 
                 # 3. 虚拟内存占用 (已提交/Committed)
                 # 计算百分比
                 vram_system_percent = (vram_system_used_bytes / vram_system_total_bytes) * 100 if vram_system_total_bytes > 0 else 0
                 
                 self.shared_memory_label.config(text=f"虚拟内存占用 (已提交): {vram_system_used_gb:.1f} GB / {vram_system_total_gb:.1f} GB ({vram_system_percent:.1f}%)")
                 self._update_progress_bar('vram_system', vram_system_percent)
            else:
                 # 系统数据获取失败时，显示错误信息并清空进度条
                 self.cpu_label.config(text="CPU 利用率: N/A (PSUTIL ERROR)")
                 self.ram_label.config(text="物理内存占用: N/A (PSUTIL ERROR)")
                 self.shared_memory_label.config(text="虚拟内存占用 (已提交): N/A (PSUTIL ERROR)")
                 self._update_progress_bar('cpu', 0)
                 self._update_progress_bar('ram', 0)
                 self._update_progress_bar('vram_system', 0)
                 # 确保警报逻辑不依赖 None
                 vram_system_used_bytes = 0
                 vram_system_used_gb = 0 # 确保在 VM 记录时不会因为 None 报错
            
            # 4. GPU 性能占用
            self.utilization_label.config(text=f"GPU 性能占用: {gpu_util:.2f}%")
            self._update_progress_bar('gpu_util', gpu_util)
            
            # 5. 专有显存占用
            self.memory_label.config(text=f"专有显存占用: {mem_used_gb:.2f} GB / {mem_total_gb:.2f} GB ({vram_local_percent:.1f}%)")
            self._update_progress_bar('vram_local', vram_local_percent)
            
            # 6. 下载速度
            self.net_recv_label.config(text=f"下载速度: {recv_speed_mbps:.2f} MB/s (上限 {MAX_BANDWIDTH_MBPS} MB/s)")
            # 进度条使用 recv_percent，颜色使用 _get_color()
            self._update_progress_bar('net_recv', recv_percent)
            
            # 7. 上传速度
            self.net_sent_label.config(text=f"上传速度: {sent_speed_mbps:.2f} MB/s (上限 {MAX_BANDWIDTH_MBPS} MB/s)")
            # 进度条使用 sent_percent，颜色使用 _get_color()
            self._update_progress_bar('net_sent', sent_percent)
            
            # --- 检查警报条件 (仅 VRAM 触发铃声警报) ---
            
            # 警报条件触发标志：Webui 中断警报 (仅 VRAM < 8GB)
            is_interrupt_warn_met = False
            vram_status_msg = ""
            vm_status_msg = ""
            
            # 1. 专有显存 (VRAM) 警报: < 8GB 则中断警报 (触发铃声)
            if mem_used_bytes < self.MEMORY_WARN_THRESHOLD_BYTES: 
                vram_status_msg = f"!!! 警报: VRAM {mem_used_gb:.2f} GB (低于 {self.MEMORY_WARN_THRESHOLD_GB} GB) !!!"
                is_interrupt_warn_met = True
            # 专有显存 > 8GB，确认程序正常运行状态
            else:
                vram_status_msg = f"VRAM 状态: 达标 ({mem_used_gb:.2f} GB)"

            # 2. 虚拟内存 (Committed) 状态更新 (仅用于显示风险提醒，不触发铃声警报)
            if vram_system_used_gb >= self.VIRTUAL_MEMORY_WARN_THRESHOLD_GB:
                 # 风险提示 (橙色字符提醒)
                 vm_status_msg = f"风险: VM {vram_system_used_gb:.1f} GB (高于 {self.VIRTUAL_MEMORY_WARN_THRESHOLD_GB} GB 存在爆内存风险!)"
                 # 不设置 is_interrupt_warn_met = True，仅视觉提醒
            else:
                 # VM 正常提示
                 vm_status_msg = f"VM 状态: 正常 ({vram_system_used_gb:.1f} GB)"
                
            
            # --- 根据警报状态更新两个 Label ---
            if is_interrupt_warn_met:
                # 触发 VRAM 中断警报时：所有警报相关的 Label 都显示红色
                self.name_label.config(text="!!! 警报: Webui 可能已中断 !!!", fg="red")
                self.status_vram_label.config(text=vram_status_msg, fg="red")
                # VM Label 在 VRAM 警报时也显示红色，强调风险，但警报触发是 VRAM 决定的
                self.status_vm_label.config(text=vm_status_msg, fg="red") 
                
                # 【核心逻辑 1: 延迟触发】如果警报未激活，则累加计数器
                if not self.is_alarm_active:
                    self.consecutive_warn_count += 1
                    
                    # 如果连续计数达到阈值，则启动警报
                    if self.consecutive_warn_count >= self.WARN_COUNT_THRESHOLD:
                        
                        # 【新增】：记录正式报警开始时间
                        if self.alarm_start_time is None:
                             self.alarm_start_time = time.time()
                             start_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.alarm_start_time))
                             logger.critical(f"正式警报已启动！报警开始时间: {start_time_str}")
                        
                        self.is_alarm_active = True
                        self.failure_count += 1
                        self.consecutive_warn_count = 0
                        self._play_beep_alarm()
                    else:
                         # 记录详细的警报信息
                         current_warn_parts = []
                         if mem_used_bytes < self.MEMORY_WARN_THRESHOLD_BYTES:
                              current_warn_parts.append("VRAM 低于 8GB")
                         logger.warning(f"中断警报条件满足 ({', '.join(current_warn_parts)})，连续计数: {self.consecutive_warn_count}/{self.WARN_COUNT_THRESHOLD}。未达警报阈值。")
                         
                # 【警报循环播放 Bug 逻辑】：如果警报已启动 (is_alarm_active=True)，但音乐已停止 (playback.active=False)，则重新启动音乐。
                elif self.is_alarm_active and self.playback and not self.playback.active:
                     # 播放计数和日志打印已在 _play_beep_alarm 内部处理
                     self._play_beep_alarm()
                         
            else: # 警报条件不满足 (VRAM >= 8 GB)
                
                # 恢复标题颜色
                self.name_label.config(text="GPU: Intel Arc A770 16GB", fg="black")

                # 第一行：专有显存状态 (VRAM)
                self.status_vram_label.config(text=vram_status_msg, fg="green")
                
                # 第二行：虚拟内存状态 (VM) - 根据阈值设置颜色
                if vram_system_used_gb >= self.VIRTUAL_MEMORY_WARN_THRESHOLD_GB:
                     # VM 风险，橙色提醒
                     self.status_vm_label.config(text=vm_status_msg, fg="orange")
                else:
                     # VM 正常，恢复默认颜色或绿色
                     self.status_vm_label.config(text=vm_status_msg, fg="SystemButtonFace") 
                
                # 【核心逻辑 2: 立即停止】如果警报状态是 True，强制停止音乐 (解决警报循环 Bug)
                if self.is_alarm_active:
                    self.is_alarm_active = False
                    
                    # 记录和重置警报计时器和播放次数
                    if self.alarm_start_time is not None:
                         # 计算持续时间
                         duration = time.time() - self.alarm_start_time
                         logger.critical(f"警报已解除。警报持续时间: {duration:.1f} 秒，歌曲循环播放总次数: {self.playback_count} 次。")
                         self.alarm_start_time = None
                         self.playback_count = 0
                    
                    # 警报解除时，停止播放器（包括循环播放的音乐）
                    if self.playback and self.playback.active:
                        self.playback.stop()
                        logger.info("中断警报条件解除，已强制停止警报音乐。")
                        
                
                # 【核心逻辑 3: 状态重置】只要不满足警报条件，就重置连续计数器
                if self.consecutive_warn_count > 0:
                    logger.info(f"所有警报条件解除，连续计数器重置 (原值: {self.consecutive_warn_count})。")
                    self.consecutive_warn_count = 0
                
                self.success_count += 1
                
                # 记录正常日志
                if cpu_percent is not None:
                     log_msg = f"状态正常 | GPU Util: {gpu_util:.2f}% | VRAM: {mem_used_gb:.2f} GB | VM: {vram_system_used_gb:.1f} GB | CPU Util: {cpu_percent:.1f}% | Net Recv: {recv_speed_mbps:.2f} MB/s"
                else:
                     log_msg = f"状态正常 | GPU Util: {gpu_util:.2f}% | VRAM: {mem_used_gb:.2f} GB | 系统数据获取失败"
                logger.info(log_msg)
                
            # --- 周期性 VM 使用量记录 ---
            if cpu_percent is not None:
                 self._log_vm_usage_periodically(current_time, vram_system_used_gb)

        except Exception as e:
            error_msg = f"数据获取失败: {e}"
            logger.error(error_msg)
            # 在错误情况下，两个 Label 都显示错误信息
            self.status_vram_label.config(text=f"错误: VRAM 数据获取失败", fg="red")
            self.status_vm_label.config(text=f"详细信息: {e}", fg="red")
            self.name_label.config(text="!!! 致命错误: 数据获取中断 !!!", fg="red")
            self.failure_count += 1
        
        # 更新日志计数器
        self.log_count_label.config(text=f"总次数: {self.total_checks} | 正常: {self.success_count} | 警报触发: {self.failure_count}")

        # 设置定时器，再次调用自身，实现实时更新
        self.master.after(self.UPDATE_INTERVAL_MS, self.update_gpu_info)

# --------------------------------------------------------------------------

if __name__ == '__main__':
    try:
        root = tk.Tk()
        print("=" * 70)
        print("程序核心功能: 持续监控 VRAM 使用情况，确保 Webui 等任务持续运行 (VRAM >= 8GB)。")
        print("次要功能: 监控 VM 使用量，超过 80GB 时给出橙色风险提醒，并周期性记录其增长量。")
        print("=" * 70)
        app = IntelArcMonitorApp(root)
        root.mainloop()
            
    except Exception as main_e:
        logger.critical(f"程序主循环发生致命错误: {main_e}")