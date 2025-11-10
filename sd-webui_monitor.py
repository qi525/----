import tkinter as tk
import time
import sys
import subprocess
import psutil # <-- 用于获取系统CPU和内存信息
from playsound3 import playsound # @@ 5-5,1/+1 @@
import platform
from loguru import logger
import datetime # <-- 【新增】用于获取实时时间
import winsound
# 【新增】引入线程池模块，用于后台数据采集
import concurrent.futures 
# 【新增】引入 os 模块，用于文件系统操作和计数
import os # <-- ADDED

# 【新增】引入 win32pdh 模块用于 GPU 引擎性能计数器
try:
    import win32pdh
except ImportError:
    logger.error("未安装 'pywin32' 模块，无法使用 PDH 性能计数器。GPU 引擎细分功能将不可用。请运行 'pip install pywin32'。")
    # 为了防止全局变量未定义，设定一个默认值
    win32pdh = None


# TODO 功能选择里面添加给转化后的图片添加前缀“创建文件时间的前缀”   2025-10-20---图片名称.jpg/webp


# --- Loguru 配置 (完美的日志输出) ---
logger.remove()
# 完美的日志输出格式
logger.add(sys.stderr, level="INFO", format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>", enqueue=True)

# --------------------------------------------------------------------------

# ----------------------------------------------------
# 【新增】PDH (Performance Data Helper) 初始化和全局变量
# ----------------------------------------------------
PDH_AVAILABLE = False
QUERY_HANDLE = None
ENGINE_COUNTERS = {} 
CORE_ENGINES_TO_MONITOR = ["Compute", "Copy", "3D"] 
ENGINE_TRANSLATIONS = {
    "Compute": "计算着色器 (AI/挖矿/并行)",
    "Copy": "数据复制 (显存/内存传输)",
    "3D": "3D 渲染 (游戏/图形加速)",
}
# 硬编码总显存 (Intel Arc A770 16GB)
INTEL_ARC_A770_TOTAL_BYTES = 16 * 1024**3 

def init_pdh_resources():
    """
    初始化 PDH 查询句柄和 GPU 引擎计数器。
    【改动点 1/5】: 整合 PDH 初始化逻辑。
    """
    global PDH_AVAILABLE, QUERY_HANDLE, ENGINE_COUNTERS
    
    # 防止重复初始化
    if PDH_AVAILABLE:
        logger.info("PDH 已经初始化，跳过。")
        return

    if platform.system() != "Windows" or win32pdh is None:
        logger.warning("非 Windows 系统或缺少 pywin32 模块，PDH 监控不可用。")
        PDH_AVAILABLE = False
        return

    try:
        # 1. 清理旧资源，以防万一
        cleanup_pdh_resources()
        
        QUERY_HANDLE = win32pdh.OpenQuery()
        ENGINE_COUNTERS = {} # 清空计数器
        
        # 通用 GPU 引擎利用率路径
        COUNTER_PATH = r"\GPU Engine(*)\Utilization Percentage" 
        counter_paths = win32pdh.ExpandCounterPath(COUNTER_PATH)
        
        for path in counter_paths:
            try:
                # 提取引擎类型，例如：luid_00000000_00000000_copy_3d
                # 注意：这里我们使用完整的路径作为 key，例如：luid_00000000_00000000_copy_3d
                counter_handle = win32pdh.AddCounter(QUERY_HANDLE, path)
                # 使用路径的实例名作为 key
                full_engine_key = path.split('(')[1].split(')')[0]
                ENGINE_COUNTERS[full_engine_key] = counter_handle
            except Exception as e:
                # 警告：此处的错误可能是计数器实例不存在
                # logger.warning(f"添加计数器失败: {path}。错误: {e}")
                pass # 忽略单个计数器添加失败

        if not ENGINE_COUNTERS:
            logger.error("未找到任何 GPU 引擎性能计数器实例，PDH 初始化失败。")
            PDH_AVAILABLE = False
            # 失败后关闭句柄
            cleanup_pdh_resources()
        else:
            # 第一次采集数据，防止 PDH_CALC_COUNTER_VALUE_FIRST 错误
            win32pdh.CollectQueryData(QUERY_HANDLE)
            PDH_AVAILABLE = True
            logger.success(f"PDH 成功初始化/恢复，找到 {len(ENGINE_COUNTERS)} 个 GPU 引擎计数器。")
            
    except Exception as e:
        PDH_AVAILABLE = False
        logger.error(f"PDH 初始化失败。错误: {e}")
        # 确保失败后句柄被关闭
        cleanup_pdh_resources()

def get_core_gpu_utilization():
    """
    获取 GPU 核心引擎 (Compute, Copy, 3D) 的聚合利用率。
    此函数在后台线程中被调用。
    """
    global PDH_AVAILABLE
    
    if not PDH_AVAILABLE:
        # PDH 不可用时返回默认 0 值
        return {engine: 0 for engine in CORE_ENGINES_TO_MONITOR}
        
    core_engine_utilization = {engine: 0.0 for engine in CORE_ENGINES_TO_MONITOR}
    
    try:
        # 采集 PDH 数据，这是 PDH 监控的关键一步
        win32pdh.CollectQueryData(QUERY_HANDLE)
        
        for full_engine_key, counter_handle in ENGINE_COUNTERS.items():
            try:
                type_val, value = win32pdh.GetFormattedCounterValue(counter_handle, win32pdh.PDH_FMT_DOUBLE)
                util_percent = value
                
                if util_percent > 0:
                    # 提取引擎类型 (例如：luid_..._3d 提取 3D)
                    parts = full_engine_key.split('_')
                    # 尝试从最后一个部分获取引擎类型
                    engine_type = parts[-1] 
                    
                    # 仅关注核心引擎
                    if engine_type in CORE_ENGINES_TO_MONITOR:
                        # 核心利用率是所有进程中该引擎的利用率之和（PDH 自动聚合）
                        core_engine_utilization[engine_type] += util_percent
                        
            except Exception as e:
                # 忽略第一次采集数据时可能出现的 PDH_CALC_COUNTER_VALUE_FIRST 或 PDH_NO_DATA
                if hasattr(e, 'winerror') and e.winerror in [win32pdh.PDH_NO_DATA, win32pdh.PDH_CALC_COUNTER_VALUE_FIRST]:
                     continue
                
                # 如果是其他 PDH 错误（如句柄失效），则记录并强制禁用 PDH 
                if hasattr(e, 'winerror'):
                    logger.error(f"PDH 计数器值获取失败，错误代码: {e.winerror}。强制 PDH_AVAILABLE=False 触发重试。")
                    PDH_AVAILABLE = False
                    # 必须跳出循环，因为句柄已失效
                    break 
                
                logger.warning(f"获取 {full_engine_key} 计数器值失败: {e}")
                
        return core_engine_utilization
        
    except Exception as e:
        # 捕获 CollectQueryData 错误（例如句柄已失效）
        logger.error(f"PDH CollectQueryData 失败: {e}。强制 PDH_AVAILABLE=False 触发重试。")
        PDH_AVAILABLE = False
        # 失败时返回 0，确保程序不中断
        return {engine: 0.0 for engine in CORE_ENGINES_TO_MONITOR}

def cleanup_pdh_resources():
    """
    关闭全局 PDH 查询句柄，释放资源。
    【改动点 2/5】: 整合 PDH 清理逻辑。
    """
    global QUERY_HANDLE, PDH_AVAILABLE, ENGINE_COUNTERS
    
    if QUERY_HANDLE:
         try:
             win32pdh.CloseQuery(QUERY_HANDLE)
             QUERY_HANDLE = None
             ENGINE_COUNTERS = {}
             # 仅清理资源，不影响 PDH_AVAILABLE 的状态判断
             logger.info("PDH 查询资源已关闭。")
         except Exception as e:
             logger.error(f"关闭 PDH 查询失败: {e}")
             
# ----------------------------------------------------

class IntelArcMonitorApp:
    """
    Intel Arc A770 实时监控应用程序，使用系统命令行工具获取 GPU VRAM 和
    PDH 获取 GPU 核心引擎细分信息，并用 tkinter 显示和警报。
    
    数据顺序：CPU -> 物理内存 -> 虚拟内存 -> GPU 核心引擎 (Compute/Copy/3D) -> 专有显存 -> 下载速度 -> 上传速度
    
    【程序核心功能】
    ------------------------------------------------------------------------------------
    核心目标 1: 持续监控专有显存（VRAM）使用情况，当其低于设定阈值（VRAM < 8GB）时，
    通过声音和UI进行警报，以确认 Webui 等任务是否意外中断。
    
    核心目标 2: 持续监控 Webui 输出目录文件数量，当其在设定周期内（默认 30 秒）
    没有增加时，触发警报，以确认 Webui 生成任务是否中断。
    
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
    
    # 【新增】PDH 重试冷却时间 (秒)
    PDH_RETRY_COOLDOWN_SECONDS = 60 

    # 自定义警报声音文件名 (注意：这里只是文件名，路径在 __init__ 中处理)
    ALARM_WAV_FILENAME = "7 you.wav" # <-- CHANGE 1/2: 改为仅文件名
    
    # 数据条尺寸常量
    BAR_WIDTH = 250
    BAR_HEIGHT = 15

    def __init__(self, master):
        """
        初始化应用程序和tkinter界面。
        """
        # 【新增】线程池设置：用于后台获取GPU/系统数据，避免UI卡顿
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=2) 
        
        # just_playback 播放器对象
        self.playback = None # @@ 212-212,+0/-1 @@
        
        self.master = master
        master.title("Intel Arc A770 实时监控 (GPU 核心引擎细分增强)")
        # 增大窗口以容纳数据条、时钟和 Webui 状态
        master.geometry("450x710") # <-- UPDATED SIZE to fit 3 GPU engine bars
        
        self.os_type = platform.system()
        if self.os_type != "Windows":
             logger.warning(f"当前操作系统为 {self.os_type}。注意：本应用的核心功能和声音警报主要在 Windows 上有效。")
        
        # 【新增】: 初始化 PDH 资源
        init_pdh_resources()
        
        # 【新增】：PDH 重试计数器
        self.pdh_retry_timestamp = 0.0 # 上次尝试重初始化的时间戳

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

        # --- Webui 文件监控追踪变量 ---
        # Webui 输出目录的基路径 (用户自定义路径)
        self.WEBUI_OUTPUT_BASE_DIR = r'C:\stable-diffusion-webui\outputs\txt2img-images'
        # 监测 Webui 文件数量的周期 (秒，用户要求 30 秒)
        self.WEBUI_CHECK_INTERVAL_SECONDS = 30
        
        self.last_webui_check_time = time.time() # 上次检查 Webui 文件数量的时间
        self.last_webui_file_count = -1 # 上次检查到的文件数量 (-1 为初始值)
        # 连续周期内文件数量未增加的次数
        self.consecutive_webui_no_increase_count = 0 
        # 连续未增加文件数量的警报周期阈值 (连续 2 个 30 秒周期未增加，则警报)
        self.WEBUI_WARN_CYCLE_THRESHOLD = 2 
        # ----------------------------------------------------

        # 【核心改动点 1/1】：计算警报文件的绝对路径
        script_dir = os.path.abspath(os.path.dirname(__file__))
        self.ALARM_WAV_FILE_PATH = os.path.join(script_dir, self.ALARM_WAV_FILENAME)
        logger.info(f"警报文件预设路径: {self.ALARM_WAV_FILE_PATH}")
        # ----------------------------------------------------


        # 创建并配置tkinter界面
        self._setup_gui()
        
        # 启动定时更新
        self.update_gpu_info()
        
        self._update_clock() 

        # 【新增】绑定窗口关闭事件，确保线程池能被关闭
        master.protocol("WM_DELETE_WINDOW", self.on_closing) 

        # 【重要】：初始化播放器并预加载 WAV 文件
        if self.os_type == "Windows": # @@ 306-313,2/+0 @@
            try:
                # 播放器初始化被移除，因为 playsound3 不需要预加载
                # self.playback = Playback() # 移除初始化
                # self.playback.load_file(self.ALARM_WAV_FILE_PATH) # 移除预加载
                logger.success(f"警报文件 '{self.ALARM_WAV_FILENAME}' 路径已设置。")
            except Exception as e:
                logger.error(f"设置警报文件路径失败（playsound3）：{e}。将使用系统默认警报音作为回退。")
        
        logger.info("Intel Arc GPU 监控应用启动成功。")

    def on_closing(self):
        """
        处理窗口关闭事件，优雅地关闭线程池和 PDH 资源。
        【改动点 3/5】: 在关闭时清理 PDH 资源。
        """
        logger.info("应用接收到关闭信号，正在关闭线程池...")
        # 1. 立即关闭线程池，不等待正在运行的任务
        self.executor.shutdown(wait=False, cancel_futures=True) 
        # 2. 清理 PDH 资源
        cleanup_pdh_resources()
        # 3. 关闭主窗口
        self.master.destroy() 


    def _setup_progress_bar(self, name):
        """
        为指定的指标设置进度条组件。
        进度条由一个灰色的背景Label和一个动态宽度的填充Label组成。
        """
        # 创建一个Frame来容纳背景和填充，以便对齐
        frame = tk.Frame(self.master, height=self.BAR_HEIGHT, bg='SystemButtonFace')
        frame.pack(fill='x', padx=10)
        
        # 灰色背景 (整体宽度)
        bg_bar = tk.Label(frame, bg='#CCCCCC')
        bg_bar.place(x=0, y=0, width=self.BAR_WIDTH, height=self.BAR_HEIGHT) 
        
        # 颜色填充条 (动态宽度，初始宽度为 0)
        fill_bar = tk.Label(frame, bg='green', height=1)
        fill_bar.place(x=0, y=0, width=0, height=self.BAR_HEIGHT)
        
        # 将组件存入实例变量
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
        【改动点 4/5】: 移除 GPU 综合指标，新增 Compute/Copy/3D 细分指标。
        """
        # 调整窗口大小以容纳新增的 GPU 细分指标
        self.master.geometry("500x710") 
        
        # --- 新增：时钟标签 (1 秒刷新) ---
        self.clock_label = tk.Label(self.master, 
                                     text="当前时间: 正在加载...", 
                                     font=('Arial', 14, 'bold'), 
                                     fg="#0000FF") # 使用蓝色突出显示
        self.clock_label.pack(fill='x', padx=10, pady=(5, 5))
        
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
        
        # --- 4. 【改动点】GPU 核心引擎细分 (Compute -> Copy -> 3D) ---
        for engine_type in CORE_ENGINES_TO_MONITOR:
            cn_name = ENGINE_TRANSLATIONS.get(engine_type, engine_type)
            # 使用 getattr 来动态创建标签和进度条变量
            label_name = f'gpu_{engine_type.lower()}_label'
            
            # Label
            label = tk.Label(self.master, text=f"GPU {cn_name}: N/A", font=('Arial', 14), anchor='w')
            setattr(self, label_name, label)
            label.pack(fill='x', padx=10, pady=(10, 0))
            
            # Progress Bar
            self._setup_progress_bar(engine_type.lower())
        # ------------------------------------------------------------------

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

        # --- VRAM/VM 状态独立显示 (三行) ---
        # 第一行：专有显存状态（用于确认程序运行）
        self.status_vram_label = tk.Label(self.master, text="状态: 监控正常", font=('Arial', 12, 'bold'), fg="green")
        self.status_vram_label.pack(pady=(5, 0)) # 上方留白
        
        # 第二行：虚拟内存风险提示
        self.status_vm_label = tk.Label(self.master, text="", font=('Arial', 12), fg="SystemButtonFace")
        self.status_vm_label.pack(pady=(0, 5)) # 下方留白
        
        # 第三行：Webui 任务状态提示
        self.status_webui_label = tk.Label(self.master, text="", font=('Arial', 12, 'bold'), fg="SystemButtonFace")
        self.status_webui_label.pack(pady=(5, 5)) # 上下方留白

        # 日志计数标签
        self.log_count_label = tk.Label(self.master, text="总次数: 0 | 正常: 0 | 警报触发: 0", font=('Arial', 10), anchor='w')
        self.log_count_label.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)

    def _update_clock(self):
        """
        独立更新时钟标签，1000ms (1秒) 刷新一次。
        """
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.clock_label.config(text=f"当前时间: {current_time}")
        self.master.after(1000, self._update_clock) # 1秒刷新


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


    def _get_gpu_vram_stats_windows(self):
        """
        [Windows 平台专用]
        通过 PowerShell 性能计数器获取 GPU **专有显存占用**。
        【改动点 5/5】: 移除 GPU 综合性能占用获取逻辑，只保留 VRAM 逻辑。
        """
        if self.os_type != "Windows":
            # 专有 VRAM 无法在非 Windows 上获取，返回 0
            return 0.0, 0.0, 0.0 
            
        try:
            # --- 1. 获取 专有显存占用 (Local Usage) ---
            # 此命令与原 sd-webui_monitor.py 中用于 VRAM 获取的命令相同
            mem_cmd = r'powershell -ExecutionPolicy Bypass -Command "((Get-Counter \"\GPU Process Memory(*)\Local Usage\").CounterSamples | Select-Object -ExpandProperty CookedValue | Measure-Object -Sum).Sum"'
            result = subprocess.run(mem_cmd, capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            mem_used_bytes = float(result.stdout.strip() or 0)
            
            # 硬编码总显存 (Intel Arc A770 16GB)
            mem_total_bytes = INTEL_ARC_A770_TOTAL_BYTES
            
            # 计算专有显存占用百分比
            vram_local_percent = (mem_used_bytes / mem_total_bytes) * 100 if mem_total_bytes > 0 else 0
            
            return mem_used_bytes, mem_total_bytes, vram_local_percent

        except subprocess.CalledCounterError as e:
            logger.error(f"PowerShell VRAM 命令执行失败，错误代码: {e.returncode}，输出: {e.stderr.strip()}")
            return 0.0, INTEL_ARC_A770_TOTAL_BYTES, 0.0 # 失败时返回 0.0% 和默认总显存
        except Exception as e:
            logger.error(f"获取 GPU VRAM 数据时发生未知错误: {e}")
            return 0.0, INTEL_ARC_A770_TOTAL_BYTES, 0.0


    def _play_beep_alarm(self):
        """
        播放自定义 WAV 文件（使用 playsound3）作为警报。
        """
        # 【新增】：记录并打印本次播放是第几次循环
        self.playback_count += 1
        logger.info(f"尝试播放声音警报 (第 {self.playback_count} 次循环)...")
        if self.os_type == "Windows":
            # 这里的 self.playback 实际存储的是 playsound3.Sound 对象
            try:
                # 1. 确保停止上一次的播放（防止警报堆叠）
                if self.playback and self.playback.is_alive(): # @@ 661-662,+0/-1 @@
                    self.playback.stop() 
                
                # 2. 播放预加载的文件 (非阻塞，playsound3 是一步到位)
                # playsound3.playsound(path, block=False) 返回一个 Sound 对象
                # 将 Sound 对象存到 self.playback
                self.playback = playsound(self.ALARM_WAV_FILE_PATH, block=False) # @@ 665-667,+1/-2 @@
            except Exception as e:
                logger.error(f"播放自定义声音文件 '{self.ALARM_WAV_FILENAME}' 失败（playsound3）：{e}。") # @@ 668-669,+0/-1 @@
                # 播放失败时，回退到系统警报音
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
        
        # 计算新的宽度：总宽度 * 百分比。
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

    def _count_files_in_output_dir(self):
        """
        获取当天 Webui 输出目录的文件数量。
        """
        try:
            # 1. 获取当天日期的目录名 (格式: 2025-10-20)
            today_date_str = datetime.datetime.now().strftime("%Y-%m-%d")
            full_path = os.path.join(self.WEBUI_OUTPUT_BASE_DIR, today_date_str)
            
            if not os.path.exists(full_path):
                # 目录不存在，Webui 可能未启动或今天未生成
                return 0

            # 2. 统计非目录文件的数量
            file_count = 0
            # 排除文件夹（目录）
            for item in os.listdir(full_path):
                 item_path = os.path.join(full_path, item)
                 if os.path.isfile(item_path):
                      file_count += 1
                      
            return file_count
            
        except Exception as e:
            logger.error(f"统计 Webui 输出目录文件数量失败: {e}")
            return 0 # 失败时返回 0，确保程序不中断

    def _check_webui_generation_status(self, current_time):
        """
        检查 Webui 文件数量是否在周期内增加，用于判断生成任务是否中断。
        """
        
        current_file_count = self._count_files_in_output_dir()
        
        # 首次运行时或上次数量为初始值 -1
        if self.last_webui_file_count == -1:
             self.last_webui_file_count = current_file_count
             self.last_webui_check_time = current_time
             logger.info(f"Webui 监控初始化：当前文件数 {current_file_count}。")
             # 初始状态默认正常，不触发警报
             return False, f"Webui 状态: 监控初始化完成 (文件数 {current_file_count})", current_file_count
             
        time_since_last_check = current_time - self.last_webui_check_time
        
        is_webui_alert_active = False
        # 默认状态
        webui_status_msg = f"Webui 状态: 正在生成 (文件数 {current_file_count})"
        
        # 检查是否达到一个完整的监测周期 (30秒)
        # 允许 1.0 秒的误差
        if time_since_last_check >= self.WEBUI_CHECK_INTERVAL_SECONDS - 1.0: 
             
             # 1. 判断文件数量是否增加
             if current_file_count > self.last_webui_file_count:
                  # 文件数量有增加，Webui 正在生成
                  # 重置未增加计数器
                  self.consecutive_webui_no_increase_count = 0
                  logger.debug(f"Webui 状态: 文件数量增加 ({self.last_webui_file_count} -> {current_file_count})。")

             elif current_file_count <= self.last_webui_file_count:
                  # 文件数量没有增加或减少，可能中断
                  self.consecutive_webui_no_increase_count += 1
                  logger.warning(f"Webui 状态: 文件数量 {current_file_count} 未增加。连续未增加周期: {self.consecutive_webui_no_increase_count}/{self.WEBUI_WARN_CYCLE_THRESHOLD}")
                  
                  # 检查警报阈值
                  if self.consecutive_webui_no_increase_count >= self.WEBUI_WARN_CYCLE_THRESHOLD:
                       is_webui_alert_active = True
                       webui_status_msg = f"!!! 警报: Webui 生成任务可能中断 (文件数 {current_file_count} 持续 {self.WEBUI_CHECK_INTERVAL_SECONDS * self.WEBUI_WARN_CYCLE_THRESHOLD}s 未增加) !!!"
                  else:
                       # 正在计数中，但未达到阈值
                       webui_status_msg = f"Webui 警报: 文件数 {current_file_count} 未增加! 连续未增加周期: {self.consecutive_webui_no_increase_count}/{self.WEBUI_WARN_CYCLE_THRESHOLD}"

             # 2. 更新上次记录值
             self.last_webui_file_count = current_file_count
             self.last_webui_check_time = current_time
             
        # 3. 如果在周期内，返回上次的状态
        elif self.consecutive_webui_no_increase_count > 0:
             # 如果正在警报计数中，更新当前的状态信息
             webui_status_msg = f"Webui 警报: 文件数 {self.last_webui_file_count} 未增加! 连续未增加周期: {self.consecutive_webui_no_increase_count}/{self.WEBUI_WARN_CYCLE_THRESHOLD}"
             if self.consecutive_webui_no_increase_count >= self.WEBUI_WARN_CYCLE_THRESHOLD:
                 is_webui_alert_active = True
                 webui_status_msg = f"!!! 警报: Webui 生成任务可能中断 (文件数 {self.last_webui_file_count} 持续 {self.WEBUI_CHECK_INTERVAL_SECONDS * self.WEBUI_WARN_CYCLE_THRESHOLD}s 未增加) !!!"


        # 返回当前的警报状态、消息和文件数
        return is_webui_alert_active, webui_status_msg, current_file_count

    def _try_reinitialize_pdh(self, current_time):
        """
        【新增方法】尝试在冷却时间后重新初始化 PDH 资源。
        此函数在后台线程中被调用。
        """
        global PDH_AVAILABLE
        
        # 检查是否处于冷却期
        time_since_last_retry = current_time - self.pdh_retry_timestamp
        
        if time_since_last_retry >= self.PDH_RETRY_COOLDOWN_SECONDS:
            logger.warning(f"PDH 监控已中断。尝试重新初始化 PDH 资源 (距离上次重试 {time_since_last_retry:.1f} 秒)...")
            # 更新重试时间戳
            self.pdh_retry_timestamp = current_time
            
            # 1. 强制清理 (以防万一)
            cleanup_pdh_resources()
            
            # 2. 尝试重新初始化
            init_pdh_resources()
            
            if PDH_AVAILABLE:
                logger.success("PDH 重新初始化成功。GPU 引擎细分监控已恢复。")
            else:
                logger.error("PDH 重新初始化失败。将等待下一个冷却周期重试。")
        else:
            logger.warning(f"PDH 监控已中断，但仍在冷却期内 ({time_since_last_retry:.1f}/{self.PDH_RETRY_COOLDOWN_SECONDS} 秒)。跳过本次重试。")
    
    def _fetch_all_data(self):
        """
        【后台线程】负责所有阻塞式的数据获取工作，包括 GPU VRAM、GPU 引擎细分、系统和网络I/O。
        该方法返回一个包含所有数据的字典。
        """
        # 记录本次更新的时间
        current_time = time.time()
        
        # --- 0. 检查并尝试恢复 PDH 资源 ---
        global PDH_AVAILABLE
        if not PDH_AVAILABLE and self.os_type == "Windows":
             self._try_reinitialize_pdh(current_time)

        # --- 1. 获取 GPU 专有 VRAM 数据 ---
        mem_used_bytes, mem_total_bytes, vram_local_percent = self._get_gpu_vram_stats_windows()

        # --- 2. 【新增】获取 GPU 核心引擎细分数据 ---
        # 如果 PDH 仍不可用，这里将返回 0 值
        gpu_engine_util = get_core_gpu_utilization()

        # --- 3. 获取 系统数据 ---
        cpu_percent, ram_used_gb, ram_total_gb, ram_percent, vram_system_used_bytes, vram_system_total_bytes = self._get_system_stats_psutil()
        
        # --- 4. 获取网络 I/O 统计并计算速度 ---
        net_io = psutil.net_io_counters()
        current_bytes_sent = net_io.bytes_sent
        current_bytes_recv = net_io.bytes_recv
        
        time_diff = current_time - self.last_update_time
        
        # 最大的预期带宽（例如 100 MB/s），用于进度条的百分比计算
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
        
        # --- 5. 检查 Webui 生成状态 ---
        is_webui_alert_active, webui_status_msg, current_file_count = self._check_webui_generation_status(current_time)
        # ------------------------------------

        # 格式化 GPU 显存数据
        mem_used_gb = mem_used_bytes / 1024**3
        
        vram_system_used_gb = vram_system_used_bytes / 1024**3
        vram_system_total_gb = vram_system_total_bytes / 1024**3

        return {
            'gpu_engine_util': gpu_engine_util, # 【新增】
            'mem_used_bytes': mem_used_bytes, 'mem_total_bytes': mem_total_bytes, 
            'vram_local_percent': vram_local_percent, 'cpu_percent': cpu_percent, 'ram_used_gb': ram_used_gb, 
            'ram_total_gb': ram_total_gb, 'ram_percent': ram_percent, 'vram_system_used_bytes': vram_system_used_bytes, 
            'vram_system_total_bytes': vram_system_total_bytes, 'vram_system_used_gb': vram_system_used_gb,
            'vram_system_total_gb': vram_system_total_gb, 'mem_used_gb': mem_used_gb, 
            'recv_speed_mbps': recv_speed_mbps, 'sent_speed_mbps': sent_speed_mbps, 
            'recv_percent': recv_percent, 'sent_percent': sent_percent, 'MAX_BANDWIDTH_MBPS': MAX_BANDWIDTH_MBPS,
            'current_time': current_time,
            'is_webui_alert_active': is_webui_alert_active, # Webui 警报状态
            'webui_status_msg': webui_status_msg, # Webui 状态信息
            'current_file_count': current_file_count, # 当前文件数量
            'error': None # 默认无错误
        }


    def update_gpu_info(self):
        """
        【主线程】启动后台任务获取数据，并安排在后台任务完成后更新UI。
        同时，立即安排下一次数据获取任务的启动时间。
        """
        self.total_checks += 1
        
        # 1. 提交数据获取任务到线程池
        future = self.executor.submit(self._fetch_all_data)
        
        # 2. 任务完成后，调用 _on_data_fetch_complete 
        future.add_done_callback(self._on_data_fetch_complete)
        
        # 3. 安排下一次数据获取任务的提交时间 (确保周期性执行)
        self.master.after(self.UPDATE_INTERVAL_MS, self.update_gpu_info)

    def _on_data_fetch_complete(self, future):
        """
        【工作线程】任务完成后执行的回调函数。
        它不应直接操作UI，而是将UI更新任务安全地调度回主线程。
        """
        try:
            # 尝试获取结果或捕获异常。
            fetched_data = future.result()
            
            # 将实际的 UI 更新和逻辑处理调度回主线程
            self.master.after(0, lambda: self._process_fetched_data(fetched_data=fetched_data))

        except concurrent.futures.CancelledError:
            # 线程池关闭时可能发生，正常处理
            logger.info("数据获取任务被取消 (线程池关闭)。")
        except Exception as e:
            # 数据获取过程中的异常，仍需要调度回主线程显示
            self.master.after(0, lambda: self._process_fetched_data(error=e))
            logger.error(f"后台线程数据获取失败: {e}")
    
    def _process_fetched_data(self, fetched_data=None, error=None):
        """
        【主线程】负责处理从后台获取的数据，更新UI、执行警报逻辑和日志记录。
        """
        global PDH_AVAILABLE
        
        if error or fetched_data is None:
            # 数据获取失败，处理错误情况
            if error is None:
                error = Exception("未知错误：_fetch_all_data 返回空数据。")
                
            error_msg = f"数据获取失败: {error}"
            logger.error(error_msg)
            # 在错误情况下，所有 Label 都显示错误信息
            self.status_vram_label.config(text=f"错误: VRAM 数据获取失败", fg="red")
            self.status_vm_label.config(text=f"详细信息: {error}", fg="red")
            self.status_webui_label.config(text=f"Webui 状态: 数据获取失败", fg="red")
            self.name_label.config(text="!!! 致命错误: 数据获取中断 !!!", fg="red")
            self.failure_count += 1
            self.log_count_label.config(text=f"总次数: {self.total_checks} | 正常: {self.success_count} | 警报触发: {self.failure_count}")
            return # 退出处理

        # 从字典中解包数据
        gpu_engine_util = fetched_data['gpu_engine_util'] # 【新增】
        mem_used_bytes = fetched_data['mem_used_bytes']
        mem_total_bytes = fetched_data['mem_total_bytes']
        vram_local_percent = fetched_data['vram_local_percent']
        cpu_percent = fetched_data['cpu_percent']
        ram_used_gb = fetched_data['ram_used_gb']
        ram_total_gb = fetched_data['ram_total_gb']
        ram_percent = fetched_data['ram_percent']
        vram_system_used_bytes = fetched_data['vram_system_used_bytes']
        vram_system_total_bytes = fetched_data['vram_system_total_bytes']
        vram_system_used_gb = fetched_data['vram_system_used_gb']
        vram_system_total_gb = fetched_data['vram_system_total_gb']
        mem_used_gb = fetched_data['mem_used_gb']
        recv_speed_mbps = fetched_data['recv_speed_mbps']
        sent_speed_mbps = fetched_data['sent_speed_mbps']
        recv_percent = fetched_data['recv_percent']
        sent_percent = fetched_data['sent_percent']
        MAX_BANDWIDTH_MBPS = fetched_data['MAX_BANDWIDTH_MBPS']
        current_time = fetched_data['current_time']

        # Webui 监控数据
        is_webui_alert_active = fetched_data['is_webui_alert_active']
        webui_status_msg = fetched_data['webui_status_msg']
        current_file_count = fetched_data['current_file_count']

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
             vram_system_used_gb = 0 
        
        # 4. 【改动点】更新 GPU 核心引擎细分
        for engine_type in CORE_ENGINES_TO_MONITOR:
             util_percent = gpu_engine_util.get(engine_type, 0.0)
             cn_name = ENGINE_TRANSLATIONS.get(engine_type, engine_type)
             
             label_name = f'gpu_{engine_type.lower()}_label'
             label = getattr(self, label_name)
             
             # 如果 PDH 不可用，在 UI 上也给出提示
             if not PDH_AVAILABLE and self.os_type == "Windows":
                 label.config(text=f"GPU {cn_name}: N/A (监控中断)")
                 self._update_progress_bar(engine_type.lower(), 0)
             else:
                 # 更新 Label 和 Progress Bar
                 label.config(text=f"GPU {cn_name}: {util_percent:.2f}%")
                 self._update_progress_bar(engine_type.lower(), util_percent)
             
        # 5. 专有显存占用
        self.memory_label.config(text=f"专有显存占用: {mem_used_gb:.2f} GB / {mem_total_bytes/1024**3:.2f} GB ({vram_local_percent:.1f}%)")
        self._update_progress_bar('vram_local', vram_local_percent)
        
        # 6. 下载速度
        self.net_recv_label.config(text=f"下载速度: {recv_speed_mbps:.2f} MB/s (上限 {MAX_BANDWIDTH_MBPS} MB/s)")
        self._update_progress_bar('net_recv', recv_percent)
        
        # 7. 上传速度
        self.net_sent_label.config(text=f"上传速度: {sent_speed_mbps:.2f} MB/s (上限 {MAX_BANDWIDTH_MBPS} MB/s)")
        self._update_progress_bar('net_sent', sent_percent)
        
        # --- 检查警报条件 (仅 VRAM 和 Webui 触发铃声警报) ---
        
        # 警报条件触发标志：VRAM < 8GB OR Webui 持续未增加
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
             
        # 3. Webui 文件数量未增加警报
        if is_webui_alert_active:
             is_interrupt_warn_met = True
             
        
        # --- 根据警报状态更新所有 Label ---
        if is_interrupt_warn_met:
            # 触发 VRAM 或 Webui 中断警报时：所有警报相关的 Label 都显示红色
            self.name_label.config(text="!!! 警报: 任务可能已中断 !!!", fg="red")
            
            # 第一行：专有显存状态
            self.status_vram_label.config(text=vram_status_msg, fg="red")
            # 第二行：虚拟内存风险提示
            self.status_vm_label.config(text=vm_status_msg, fg="red") 
            # 第三行：Webui 任务状态提示
            self.status_webui_label.config(text=webui_status_msg, fg="red")
            
            # 【核心逻辑 1: 延迟触发】如果警报未激活，则累加计数器 (VRAM/Webui 警报都走这个逻辑)
            if not self.is_alarm_active:
                self.consecutive_warn_count += 1
                
                # 如果连续计数达到阈值，则启动警报 (VRAM 或 Webui 的警报都走这个逻辑)
                if self.consecutive_warn_count >= self.WARN_COUNT_THRESHOLD:
                    
                    # 记录正式报警开始时间
                    if self.alarm_start_time is None:
                         self.alarm_start_time = time.time()
                         start_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.alarm_start_time))
                         
                         # 记录详细的警报原因
                         warn_reasons = []
                         if mem_used_bytes < self.MEMORY_WARN_THRESHOLD_BYTES:
                              warn_reasons.append("VRAM 低于 8GB")
                         if is_webui_alert_active:
                              warn_reasons.append(f"Webui 文件数 {current_file_count} 持续未增加")
                              
                         logger.critical(f"正式警报已启动！报警开始时间: {start_time_str}。原因: {', '.join(warn_reasons)}")
                    
                    self.is_alarm_active = True
                    self.failure_count += 1
                    self.consecutive_warn_count = 0
                    self._play_beep_alarm()
                else:
                     # 记录详细的警报信息 (延迟触发中)
                     current_warn_parts = []
                     if mem_used_bytes < self.MEMORY_WARN_THRESHOLD_BYTES:
                          current_warn_parts.append("VRAM 低于 8GB")
                     if is_webui_alert_active:
                          current_warn_parts.append("Webui 文件数持续未增加")
                          
                     logger.warning(f"中断警报条件满足 ({', '.join(current_warn_parts)})，连续计数: {self.consecutive_warn_count}/{self.WARN_COUNT_THRESHOLD}。未达警报阈值。")
                     
            # 【警报循环播放 Bug 逻辑】：如果警报已启动 (is_alarm_active=True)，但音乐已停止 (playback.is_alive()=False)，则重新启动音乐。
            elif self.is_alarm_active and self.playback and not self.playback.is_alive(): # @@ 1073-1073,+0/-1 @@
                 # 播放计数和日志打印已在 _play_beep_alarm 内部处理
                 self._play_beep_alarm()
                     
        else: # 警报条件不满足 (VRAM >= 8 GB 且 Webui 状态正常)
            
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
                 
            # 第三行：Webui 任务状态提示
            self.status_webui_label.config(text=webui_status_msg, fg="green") # 正常时显示绿色

            
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
                if self.playback and self.playback.is_alive(): # @@ 1118-1118,+0/-1 @@
                    self.playback.stop()
                    logger.info("中断警报条件解除，已强制停止警报音乐。")
                    
            
            # 【核心逻辑 3: 状态重置】只要不满足警报条件，就重置连续计数器
            if self.consecutive_warn_count > 0:
                logger.info(f"所有警报条件解除，连续计数器重置 (原值: {self.consecutive_warn_count})。")
                self.consecutive_warn_count = 0
            
            self.success_count += 1
            
            # 记录正常日志
            if cpu_percent is not None:
                 # 提取核心引擎利用率进行日志记录
                 compute_util = gpu_engine_util.get("Compute", 0.0)
                 copy_util = gpu_engine_util.get("Copy", 0.0)
                 
                 # PDH 不可用时，在日志中也要体现
                 pdh_status = "正常" if PDH_AVAILABLE else "PDH 中断"
                 
                 log_msg = f"状态正常 ({pdh_status}) | GPU Compute: {compute_util:.2f}% | GPU Copy: {copy_util:.2f}% | VRAM: {mem_used_gb:.2f} GB | VM: {vram_system_used_gb:.1f} GB | CPU Util: {cpu_percent:.1f}% | Net Recv: {recv_speed_mbps:.2f} MB/s | Webui File Count: {current_file_count}"
            else:
                 log_msg = f"状态正常 | GPU 引擎数据: {gpu_engine_util} | VRAM: {mem_used_gb:.2f} GB | 系统数据获取失败 | Webui File Count: {current_file_count}"
            logger.info(log_msg)
            
        # --- 周期性 VM 使用量记录 ---
        if cpu_percent is not None:
             self._log_vm_usage_periodically(current_time, vram_system_used_gb)
        
        # 更新日志计数器
        self.log_count_label.config(text=f"总次数: {self.total_checks} | 正常: {self.success_count} | 警报触发: {self.failure_count}")


# --------------------------------------------------------------------------

if __name__ == '__main__':
    try:
        root = tk.Tk()
        print("=" * 70)
        print("程序核心功能 1: 持续监控 VRAM 使用情况，确保 Webui 等任务持续运行 (VRAM >= 8GB)。")
        print("程序核心功能 2: 持续监控 Webui 输出目录文件数量，未增加则警报。")
        print("核心指标替换: GPU 综合性能替换为核心引擎细分 (Compute, Copy, 3D)。")
        print("次要功能: 监控 VM 使用量，超过 80GB 时给出橙色风险提醒，并周期性记录其增长量。")
        print("【新增功能】：实时时钟显示 (1秒刷新) 和多线程数据采集 (避免UI卡顿)。")
        print("【优化功能】：PDH GPU 引擎细分监控中断时，自动进行冷却后重试。")
        print("【本次优化】：警报声音文件路径自适应，确保 CMD 启动时不报错。") # <-- 补充日志
        print("【本次优化】：将 just_playback 替换为 playsound3 实现更稳定的跨平台播放控制。") # @@ 1162-1162,+1/-0 @@
        print("=" * 70)
        app = IntelArcMonitorApp(root)
        root.mainloop()
            
    except Exception as main_e:
        logger.critical(f"程序主循环发生致命错误: {main_e}")