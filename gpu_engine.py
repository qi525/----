import tkinter as tk
from tkinter import ttk 
import win32pdh
from loguru import logger
import wmi # 仅导入，但不使用其API

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
    "Media": "视频编解码",
    "VideoDecode": "视频解码 (播放)",
    "VideoEncode": "视频编码 (录制/直播)",
    # 针对 Intel 显卡可能会出现的其他类型：
    "Render": "通用渲染",
    "Gfx": "图形处理",
    "Tile": "平铺渲染",
    "DMA": "直接内存访问"
}

# 定义需要重点监控的核心引擎
CORE_ENGINES_TO_MONITOR = ["3D", "Compute", "Copy"] 

try:
    # 打开一个查询句柄
    QUERY_HANDLE = win32pdh.OpenQuery()
    # 定义性能计数器路径。"*\" 表示所有实例 (即所有 GPU 引擎)
    COUNTER_PATH = r"\GPU Engine(*)\Utilization Percentage" 
    
    # 获取所有匹配的计数器实例路径
    counter_paths = win32pdh.ExpandCounterPath(COUNTER_PATH)
    
    # 为每个引擎添加一个计数器
    # 我们获取所有实例，包括进程 (pid) 细分的，然后在处理数据时进行累加和过滤
    for path in counter_paths:
        try:
            # 路径示例: \GPU Engine(pid_xxxx_luid_xxxx_engine_3D)\Utilization Percentage
            
            # 提取完整的引擎键 (用于计数器字典的键)
            full_engine_key = path.split('(')[1].split(')')[0]
            
            # 添加计数器
            counter_handle = win32pdh.AddCounter(QUERY_HANDLE, path)
            ENGINE_COUNTERS[full_engine_key] = counter_handle
        except Exception as e:
            logger.warning(f"添加计数器失败: {path}。错误: {e}")
            
    if not ENGINE_COUNTERS:
        logger.error("未找到任何 GPU 引擎性能计数器实例，PDH 初始化失败。")
        PDH_AVAILABLE = False
    else:
        # 第一次收集数据（PDH API 需要两次收集才能计算百分比，第一次是基准）
        win32pdh.CollectQueryData(QUERY_HANDLE)
        logger.info(f"PDH 成功初始化，找到 {len(ENGINE_COUNTERS)} 个 GPU 引擎计数器（含进程）。")
        
except Exception as e:
    PDH_AVAILABLE = False
    logger.error(f"PDH 初始化失败。错误: {e}")
    logger.warning("请检查 pywin32 是否安装完整，或运行 'lodctr /r' 修复性能计数器并重启电脑。")

# ----------------------------------------------------

class GpuMonitorApp:
    # 难度系数评估：2/10 (重构分段 3)
    
    # 将全局句柄作为类属性，方便在 on_closing 中访问和清理
    QUERY_HANDLE = QUERY_HANDLE 
    # 将全局计数器作为类属性，方便数据收集方法访问
    ENGINE_COUNTERS = ENGINE_COUNTERS
    
    def __init__(self, master):
        self.master = master
        master.geometry("800x450") # 增加窗口高度以容纳条形图
        master.title("GPU 核心引擎占用率监控 (PDH)")
        
        logger.info("初始化 GPU 监控应用...")
        
        # ------------------- 新增可视化组件和样式定义 --------------------
        self.style = ttk.Style()
        
        # 定义进度条样式
        # 50%以下绿色 (健康)
        self.style.configure("Green.Horizontal.TProgressbar", 
                             troughcolor='white', 
                             background='green', 
                             troughrelief='flat')
        # 50%-75%橙色 (警告)
        self.style.configure("Orange.Horizontal.TProgressbar", 
                             troughcolor='white', 
                             background='orange',
                             troughrelief='flat')
        # 75%-100%红色 (高负载)
        self.style.configure("Red.Horizontal.TProgressbar", 
                             troughcolor='white', 
                             background='red',
                             troughrelief='flat')

        # 主内容框架 (用于容纳所有的条形图和文本)
        self.main_frame = ttk.Frame(master)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 错误或警告信息标签
        self.label_status = tk.Label(master, text="", fg="red")
        self.label_status.pack(pady=5)
        
        if PDH_AVAILABLE: 
            # 初始显示
            init_label = tk.Label(self.main_frame, text="正在收集数据并初始化条形图...")
            init_label.pack()
            self.update_gpu_data()
            init_label.destroy() # 收集到数据后会清除
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

    # ------------------- 数据层：数据获取和解析 (难度系数: 3/10) -------------------
    @staticmethod
    def _get_gpu_utilization_data(query_handle, engine_counters):
        """
        核心数据收集和解析逻辑，与 UI 分离。
        返回: (aggregated_utilization, process_tracker, total_utilization, success_count, core_engine_utilization)
        """
        aggregated_utilization = {}
        process_tracker = {}
        total_utilization = 0
        success_count = 0
        # 用于存储核心引擎的汇总利用率
        core_engine_utilization = {engine: 0 for engine in CORE_ENGINES_TO_MONITOR}
        
        # 核心步骤：收集新的性能数据
        win32pdh.CollectQueryData(query_handle)
        
        # 遍历所有引擎计数器并获取其值
        for full_engine_key, counter_handle in engine_counters.items():
            try:
                type_val, value = win32pdh.GetFormattedCounterValue(
                    counter_handle, 
                    win32pdh.PDH_FMT_DOUBLE 
                )
                
                util_percent = int(value) 
                
                if util_percent > 0:
                    success_count += 1
                    total_utilization += util_percent 
                    
                    # 解析出引擎类型 (例如 '3D') 和 PID (例如 'pid_25056')
                    # 路径示例: pid_xxxx_luid_xxxx_engine_3D
                    parts = full_engine_key.split('_')
                    
                    # 假设引擎类型在最后
                    engine_type = parts[-1] 
                    
                    # 尝试提取 PID
                    pid = 'N/A'
                    for part in parts:
                        if part.startswith('pid'):
                            pid = part
                            break
                    
                    # 1. 累加总利用率
                    aggregated_utilization[engine_type] = aggregated_utilization.get(engine_type, 0) + util_percent
                    
                    # 2. 核心引擎提取 (复用点)
                    if engine_type in CORE_ENGINES_TO_MONITOR:
                        core_engine_utilization[engine_type] += util_percent
                        
                    # 3. 跟踪进程利用率
                    if engine_type not in process_tracker:
                        process_tracker[engine_type] = {}
                    # 累加同一 PID 对同一引擎的贡献
                    process_tracker[engine_type][pid] = process_tracker[engine_type].get(pid, 0) + util_percent

            except Exception as e:
                # 忽略 PDH_NO_DATA 和 PDH_CALC_COUNTER_VALUE_FIRST 等常见初始错误
                if hasattr(e, 'winerror') and e.winerror not in [win32pdh.PDH_NO_DATA, win32pdh.PDH_CALC_COUNTER_VALUE_FIRST]:
                    logger.warning(f"获取 {full_engine_key} 计数器值失败: {e}")
                    
        return aggregated_utilization, process_tracker, total_utilization, success_count, core_engine_utilization

    # ------------------- 视图层：核心引擎摘要渲染 (难度系数: 2/10) -------------------
    def _render_core_engines_summary(self, core_engine_utilization):
        """
        在 UI 上方渲染核心引擎 (3D, Compute, Copy) 的汇总信息。
        """
        core_text = "核心引擎利用率 (3D/Compute/Copy): "
        for engine in CORE_ENGINES_TO_MONITOR:
            util = core_engine_utilization.get(engine, 0)
            core_text += f"[{engine}: {util:>3d}%] "
            
        tk.Label(self.main_frame, 
                 text=core_text, 
                 font=("Consolas", 10, "bold"), 
                 fg="blue", 
                 anchor=tk.W).pack(fill=tk.X, pady=(0, 10))

    # ------------------- 视图层：详细条形图渲染 (难度系数: 3/10) -------------------
    def _render_utilization_bars(self, aggregated_utilization, process_tracker):
        """
        根据聚合的利用率数据，动态创建并渲染所有引擎的进度条和进程细分信息。
        """
        # 2. 引擎类型细分
        # 按利用率降序排列累加后的引擎类型
        sorted_types = sorted(aggregated_utilization.items(), key=lambda item: item[1], reverse=True)
        
        display_text_detail = "" # 用于控制台输出的详细信息
        active_engine_types = 0
        
        for engine_type, total_util in sorted_types:
            if total_util == 0:
                continue
            
            active_engine_types += 1
            
            # 获取中文翻译
            cn_name = ENGINE_TRANSLATIONS.get(engine_type, engine_type)
            
            # 确定进度条样式 (50%以下绿色，50-75%橙色，75-100%红色)
            if total_util <= 50:
                style_name = "Green.Horizontal.TProgressbar"
            elif total_util <= 75:
                style_name = "Orange.Horizontal.TProgressbar"
            else:
                style_name = "Red.Horizontal.TProgressbar"

            # 容器：用于放置引擎名称、百分比和进度条
            engine_frame = ttk.Frame(self.main_frame)
            engine_frame.pack(fill=tk.X, pady=2)
            
            # 引擎名称和利用率标签
            tk.Label(engine_frame, 
                     text=f"[{cn_name} ({engine_type})]: {total_util:>3d}%",
                     font=("Consolas", 10), 
                     width=50, # 固定宽度以对齐
                     anchor=tk.W).pack(side=tk.LEFT)
            
            # 进度条
            progressbar = ttk.Progressbar(engine_frame, 
                                          orient="horizontal", 
                                          length=200, 
                                          mode="determinate",
                                          style=style_name)
            progressbar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
            progressbar['value'] = min(total_util, 100) # 确保不超过100
            
            # 进程细分（显示到主框架，或折叠在子框架）
            process_label_frame = ttk.Frame(self.main_frame)
            process_label_frame.pack(fill=tk.X, padx=20)
            
            # 显示贡献最大的前三个进程
            valid_pids = {p: u for p, u in process_tracker[engine_type].items() if u > 0}
            sorted_pids = sorted(valid_pids.items(), key=lambda item: item[1], reverse=True)[:3]
            
            process_info = ""
            for pid, util in sorted_pids:
                # 去掉 pid_ 前缀
                pid_num = pid.replace('pid_', '')
                process_info += f"| PID {pid_num:<6}: {util:>3d}% "
            
            if process_info:
                tk.Label(process_label_frame, 
                         text=f"  {process_info}", 
                         font=("Consolas", 8), 
                         fg="gray", 
                         anchor=tk.W).pack(fill=tk.X)
            
            # 同时构建控制台输出文本 (保留原有日志)
            display_text_detail += f"\n [{cn_name} ({engine_type})]: {total_util:>3d}%\n"
            for pid, util in sorted_pids:
                pid_num = pid.replace('pid_', '')
                display_text_detail += f"     - PID {pid_num:<6}: {util:>3d}%\n"
                
        return display_text_detail.strip(), active_engine_types
    
    
    def update_gpu_data(self):
        """
        核心控制器：定时获取并更新 GPU 引擎数据
        """
        
        if not PDH_AVAILABLE:
            self.master.after(1000, self.update_gpu_data)
            return
            
        # 清除旧组件
        self._clear_main_frame()
            
        total_engines_count = len(self.ENGINE_COUNTERS)

        try:
            # 1. 调用数据层方法获取所有数据
            aggregated_utilization, process_tracker, total_utilization, success_count, core_engine_utilization = \
                self._get_gpu_utilization_data(self.QUERY_HANDLE, self.ENGINE_COUNTERS)
            
            # 输出综合性能指标 (上限设为100%以防误导)
            total_util_capped = min(total_utilization, 100)
            
            # ------------------- UI 渲染调用 --------------------
            
            # 1. 总体利用率标题
            tk.Label(self.main_frame, 
                     text=f"--- GPU 综合利用率 (核心引擎近似求和): {total_util_capped:>3d}% ---", 
                     font=("Consolas", 10, "bold"), 
                     anchor=tk.W).pack(fill=tk.X, pady=(0, 5))
                     
            # 2. 调用核心引擎汇总渲染方法
            self._render_core_engines_summary(core_engine_utilization)
            
            # 3. 详细信息标题
            tk.Label(self.main_frame, 
                     text="--- 详细引擎类型细分 (含可视化条) ---", 
                     font=("Consolas", 10, "bold"), 
                     anchor=tk.W).pack(fill=tk.X, pady=(5, 5))
                     
            # 4. 调用抽象后的详细渲染方法
            display_text_detail, active_engine_types = self._render_utilization_bars(aggregated_utilization, process_tracker)
            
            # ------------------- 控制台输出 (聚合) --------------------
            # 构造核心引擎信息用于控制台输出
            core_engine_info = f"--- 核心引擎 (3D, Compute, Copy) 提取结果 ---\n"
            for engine, util in core_engine_utilization.items():
                core_engine_info += f"[{engine}]: {util:>3d}% "
            core_engine_info += "\n"
            
            print("\n" + core_engine_info)
            display_text = core_engine_info + "\n" + display_text_detail # 聚合核心信息和详细信息
            
            print(f"\n--- GPU 综合利用率 (核心引擎近似求和): {total_util_capped:>3d}% ---\n")
            print(display_text.strip() + "\n")
            # ******************************************************
                
            logger.info(f"数据更新：总计数器 {total_engines_count}, 成功获取 {success_count} 个非零计数器 (显示 {active_engine_types} 个活跃引擎类型)。")

        except Exception as e:
            error_msg = f"PDH 数据收集失败: {e}"
            self.label_status.config(text=error_msg, fg="red")
            logger.error(error_msg)

        # 记录优化的点：使用 Tkinter 的 after 方法进行定时刷新，避免阻塞 GUI 线程。
        self.master.after(1000, self.update_gpu_data)
    
    def on_closing(self):
        """窗口关闭时执行清理操作"""
        logger.info("应用关闭。")
        if PDH_AVAILABLE and self.QUERY_HANDLE:
             try:
                 # 清理 PDH 资源
                 win32pdh.CloseQuery(self.QUERY_HANDLE)
                 logger.info("PDH 查询资源已关闭。")
             except Exception as e:
                 logger.error(f"关闭 PDH 查询失败: {e}")

        self.master.destroy()

if __name__ == '__main__':
    
    # 初始化 loguru 配置 (如果还没有配置)
    logger.add("gpu_monitor_{time}.log", rotation="10 MB", level="INFO")
    
    root = tk.Tk()
    app = GpuMonitorApp(root)
    root.mainloop()