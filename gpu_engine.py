import tkinter as tk
from tkinter import ttk 
import win32pdh
from loguru import logger
import subprocess # 【新增】用于运行 PowerShell 命令
import time
import sys

# --- Loguru 配置 (完美的日志输出) ---
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>", enqueue=True)

# ----------------------------------------------------
# 核心常量定义
# ----------------------------------------------------
# WMI 已移除，直接使用硬编码或回退值 (源自您的 sd-webui_monitor.py 文件)
INTEL_ARC_A770_TOTAL_MB = 16 * 1024 

# ----------------------------------------------------
# PDH (Performance Data Helper) 初始化
# ----------------------------------------------------
PDH_AVAILABLE = True
QUERY_HANDLE = None
ENGINE_COUNTERS = {} 

# 定义引擎类型和功能的中文翻译
ENGINE_TRANSLATIONS = {
    "3D": "3D 渲染 (游戏/图形加速)",
    "Compute": "计算着色器 (AI/挖矿/并行计算)",
    "Copy": "数据复制 (显存与内存/设备间传输)",
}

# 需求：计算放第一，复制放第二，第三才是3d
CORE_ENGINES_TO_MONITOR = ["Compute", "Copy", "3D"] 

try:
    QUERY_HANDLE = win32pdh.OpenQuery()
    COUNTER_PATH = r"\GPU Engine(*)\Utilization Percentage" 
    counter_paths = win32pdh.ExpandCounterPath(COUNTER_PATH)
    
    for path in counter_paths:
        try:
            full_engine_key = path.split('(')[1].split(')')[0]
            counter_handle = win32pdh.AddCounter(QUERY_HANDLE, path)
            ENGINE_COUNTERS[full_engine_key] = counter_handle
        except Exception as e:
            logger.warning(f"添加计数器失败: {path}。错误: {e}")
            
    if not ENGINE_COUNTERS:
        logger.error("未找到任何 GPU 引擎性能计数器实例，PDH 初始化失败。")
        PDH_AVAILABLE = False
    else:
        win32pdh.CollectQueryData(QUERY_HANDLE)
        logger.info(f"PDH 成功初始化，找到 {len(ENGINE_COUNTERS)} 个 GPU 引擎计数器（含进程）。")
        
except Exception as e:
    PDH_AVAILABLE = False
    logger.error(f"PDH 初始化失败。错误: {e}")

# ----------------------------------------------------
# 模块化功能：通过 PowerShell 获取 VRAM 显存使用量 (核心改动点)
# ----------------------------------------------------

def get_vram_stats_powershell(total_vram_fallback_mb: int = INTEL_ARC_A770_TOTAL_MB) -> dict:
    """
    通过 PowerShell 性能计数器获取 GPU 专有显存占用 (Local Usage)。
    此方法源自用户提供的 sd-webui_monitor.py 文件，用于解决 VRAM 实时占用问题。
    """
    mem_total_mb = total_vram_fallback_mb
    
    try:
        # 获取 专有显存占用 (Local Usage) - 对应 \GPU Process Memory(*)\Local Usage
        # 命令来自 sd-webui_monitor.py
        mem_cmd = r'powershell -ExecutionPolicy Bypass -Command "((Get-Counter \"\GPU Process Memory(*)\Local Usage\").CounterSamples | Select-Object -ExpandProperty CookedValue | Measure-Object -Sum).Sum"'
        
        # 使用 subprocess 运行 PowerShell 命令
        result = subprocess.run(
            mem_cmd, 
            capture_output=True, 
            text=True, 
            check=True, 
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        
        # mem_used_bytes 的单位是 Bytes
        mem_used_bytes = float(result.stdout.strip() or 0)
        mem_used_mb = mem_used_bytes / (1024 * 1024)
        
        # 计算百分比
        vram_local_percent = (mem_used_bytes / (mem_total_mb * 1024 * 1024)) * 100 if mem_total_mb > 0 else 0
        
        logger.info(f"PowerShell 成功获取 VRAM 使用量: {mem_used_mb:.2f} MB。")
        
        return {
            "total_mb": mem_total_mb,
            "used_mb": mem_used_mb, 
            "utilization_percent": vram_local_percent, 
            "status": "PS_SUCCESS"
        }

    except subprocess.CalledProcessError as e:
        logger.error(f"PowerShell VRAM 命令执行失败: {e.returncode}, 输出: {e.stderr.strip()}")
        logger.warning("请确保 Windows 性能计数器正常。")
    except Exception as e:
        logger.error(f"VRAM 获取失败: {e}")
        
    # 失败时回退
    return {
        "total_mb": mem_total_mb,
        "used_mb": -1, 
        "utilization_percent": -1, 
        "status": "PS_FAIL_FALLBACK"
    }

# ----------------------------------------------------
# 模块化功能：获取核心引擎利用率 (PDH 方式不变，以获取 Compute/Copy/3D breakdown)
# ----------------------------------------------------

def get_core_gpu_utilization():
    """获取 GPU 核心引擎 (Compute, Copy, 3D) 的聚合利用率。"""
    if not PDH_AVAILABLE:
        return {"error": "PDH_NOT_AVAILABLE"}
        
    core_engine_utilization = {engine: 0 for engine in CORE_ENGINES_TO_MONITOR}
    success_count = 0
    total_engine_count = len(ENGINE_COUNTERS)
    
    try:
        win32pdh.CollectQueryData(QUERY_HANDLE)
        
        for full_engine_key, counter_handle in ENGINE_COUNTERS.items():
            try:
                type_val, value = win32pdh.GetFormattedCounterValue(counter_handle, win32pdh.PDH_FMT_DOUBLE)
                util_percent = int(value) 
                
                if util_percent > 0:
                    success_count += 1
                    parts = full_engine_key.split('_')
                    engine_type = parts[-1] 
                    
                    if engine_type in CORE_ENGINES_TO_MONITOR:
                        # 核心利用率是所有进程中该引擎的利用率之和
                        core_engine_utilization[engine_type] += util_percent
                        
            except Exception as e:
                if hasattr(e, 'winerror') and e.winerror not in [win32pdh.PDH_NO_DATA, win32pdh.PDH_CALC_COUNTER_VALUE_FIRST]:
                    logger.warning(f"获取 {full_engine_key} 计数器值失败: {e}")
                    
        logger.info(f"数据收集：总计数器实例 {total_engine_count}, 成功获取 {success_count} 个非零实例。")
        
        return core_engine_utilization
        
    except Exception as e:
        logger.error(f"PDH 数据收集失败: {e}")
        return {"error": str(e)}

def cleanup_pdh_resources():
    """关闭全局 PDH 查询句柄，释放资源。"""
    global QUERY_HANDLE
    global PDH_AVAILABLE
    
    if PDH_AVAILABLE and QUERY_HANDLE:
         try:
             win32pdh.CloseQuery(QUERY_HANDLE)
             QUERY_HANDLE = None
             logger.info("PDH 查询资源已关闭。")
         except Exception as e:
             logger.error(f"关闭 PDH 查询失败: {e}")


# ----------------------------------------------------
# GUI 应用类
# ----------------------------------------------------

class GpuMonitorApp:
    
    def __init__(self, master):
        self.master = master
        master.geometry("800x450") 
        master.title("GPU 核心引擎与 VRAM 监控 (PowerShell/PDH Hybrid)")
        
        logger.info("初始化 GPU 监控应用...")
        
        # ------------------- 可视化组件和样式定义 --------------------
        self.style = ttk.Style()
        
        # 定义进度条样式
        self.style.configure("Green.Horizontal.TProgressbar", troughcolor='white', background='green', troughrelief='flat')
        self.style.configure("Orange.Horizontal.TProgressbar", troughcolor='white', background='orange', troughrelief='flat')
        self.style.configure("Red.Horizontal.TProgressbar", troughcolor='white', background='red', troughrelief='flat')

        # 主内容框架
        self.main_frame = ttk.Frame(master)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 错误或警告信息标签
        self.label_status = tk.Label(master, text="", fg="red")
        self.label_status.pack(pady=5)
        
        if PDH_AVAILABLE: 
            init_label = tk.Label(self.main_frame, text="正在收集数据并初始化条形图...")
            init_label.pack()
            self.update_gpu_data()
        else:
            error_label = tk.Label(self.main_frame, 
                                   text="PDH/性能计数器不可用，无法监控 GPU 引擎。",
                                   fg="red")
            error_label.pack()
            
        # 注册窗口关闭时的清理操作
        master.protocol("WM_DELETE_WINDOW", self.on_closing)
        
    def _clear_main_frame(self):
        """清除 main_frame 中的所有组件"""
        for widget in self.main_frame.winfo_children():
            widget.destroy()

    def _render_core_engines_summary(self, core_engine_utilization):
        """
        渲染核心引擎 (Compute, Copy, 3D) 和 VRAM 汇总信息。
        """
        
        tk.Label(self.main_frame, 
                 text="--- GPU 核心引擎利用率 (PDH) ---", 
                 font=("Consolas", 10, "bold"), 
                 anchor=tk.W).pack(fill=tk.X, pady=(0, 5))
        
        # 核心引擎按 CORE_ENGINES_TO_MONITOR 顺序 (Compute, Copy, 3D) 渲染
        for engine in CORE_ENGINES_TO_MONITOR: 
            util = core_engine_utilization.get(engine, 0)
            cn_name = ENGINE_TRANSLATIONS.get(engine, engine)
            
            # 确定进度条样式
            if util <= 50:
                style_name = "Green.Horizontal.TProgressbar"
            elif util <= 75:
                style_name = "Orange.Horizontal.TProgressbar"
            else:
                style_name = "Red.Horizontal.TProgressbar"

            # 容器
            engine_frame = ttk.Frame(self.main_frame)
            engine_frame.pack(fill=tk.X, pady=2)
            
            # 引擎名称和利用率标签
            tk.Label(engine_frame, 
                     text=f"[{cn_name} ({engine})]: {util:>3d}%",
                     font=("Consolas", 10), 
                     width=50, 
                     anchor=tk.W).pack(side=tk.LEFT)
            
            # 进度条
            progressbar = ttk.Progressbar(engine_frame, 
                                          orient="horizontal", 
                                          length=200, 
                                          mode="determinate",
                                          style=style_name)
            progressbar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
            progressbar['value'] = min(util, 100) 
            
        # ------------------- VRAM 渲染 -------------------
        vram_data = get_vram_stats_powershell()
        
        total_mb = vram_data.get("total_mb", 0)
        used_mb = vram_data.get("used_mb", -1)
        util_percent = vram_data.get("utilization_percent", 0)
        status = vram_data.get("status", "UNKNOWN")
        
        
        if used_mb != -1 and total_mb > 0:
            text_vram = f"显存占用 (VRAM): {used_mb:.2f} MB / {total_mb:.0f} MB ({util_percent:.1f}%)"
            
            # VRAM 进度条样式
            if util_percent <= 50:
                vram_style = "Green.Horizontal.TProgressbar"
            elif util_percent <= 75:
                vram_style = "Orange.Horizontal.TProgressbar"
            else:
                vram_style = "Red.Horizontal.TProgressbar"
            
            #  VRAM 容器
            vram_frame = ttk.Frame(self.main_frame)
            vram_frame.pack(fill=tk.X, pady=(10, 5))
            
            # VRAM 标签
            tk.Label(vram_frame, 
                     text=f"--- {text_vram} ---", 
                     font=("Consolas", 10, "bold"),
                     anchor=tk.W).pack(side=tk.LEFT)
                     
            # VRAM 进度条
            vram_progressbar = ttk.Progressbar(vram_frame, 
                                          orient="horizontal", 
                                          length=150, 
                                          mode="determinate",
                                          style=vram_style)
            vram_progressbar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
            vram_progressbar['value'] = min(util_percent, 100)
            
        else:
            text_vram = f"VRAM 总容量 {total_mb:.0f} MB。实时占用: 无法获取 (PowerShell 状态: {status})"
            tk.Label(self.main_frame, 
                     text=f"--- {text_vram} ---", 
                     font=("Consolas", 10, "bold"),
                     anchor=tk.W).pack(fill=tk.X, pady=(10, 5))
                 
    
    def update_gpu_data(self):
        """核心控制器：定时获取并更新 GPU 引擎数据"""
        
        if not PDH_AVAILABLE:
            self.master.after(1000, self.update_gpu_data)
            return
            
        self._clear_main_frame()
            
        try:
            # 1. 获取核心引擎利用率 (PDH)
            core_engine_utilization = get_core_gpu_utilization()
            
            if "error" in core_engine_utilization:
                 raise Exception(core_engine_utilization["error"])
                 
            # 2. 渲染 UI
            self._render_core_engines_summary(core_engine_utilization)
            
            # ------------------- 控制台输出 (只输出三个核心参数) --------------------
            core_engine_info = f"--- 核心引擎 (Compute, Copy, 3D) 提取结果 ---\n"
            for engine, util in core_engine_utilization.items():
                core_engine_info += f"[{engine}]: {util:>3d}% "
            core_engine_info += "\n"
            
            print("\n" + core_engine_info)
            # ******************************************************
                
        except Exception as e:
            error_msg = f"数据收集失败: {e}"
            self.label_status.config(text=error_msg, fg="red")
            logger.error(error_msg)

        self.master.after(1000, self.update_gpu_data)
    
    def on_closing(self):
        """窗口关闭时执行清理操作"""
        logger.info("应用关闭。")
        cleanup_pdh_resources() 
        self.master.destroy()

if __name__ == '__main__':
    
    # 模块测试 - VRAM 信息 (使用新的 PowerShell 逻辑)
    vram_test_data = get_vram_stats_powershell()
    logger.info(f"模块测试 - VRAM 信息: {vram_test_data}")

    # 模块测试 - 核心利用率 (PDH 逻辑)
    core_util_initial = get_core_gpu_utilization()
    logger.info(f"模块测试 - 初始核心利用率: {core_util_initial}")
    
    # 等待一秒，保证两次 CollectQueryData 间隔
    time.sleep(1) 
    core_util_second = get_core_gpu_utilization()
    logger.info(f"模块测试 - 第二次核心利用率: {core_util_second}")
    
    # 运行 GUI 示例
    root = tk.Tk()
    app = GpuMonitorApp(root)
    
    # 确保在 mainloop 结束时（GUI 关闭）清理资源
    try:
        root.mainloop()
    finally:
        cleanup_pdh_resources()