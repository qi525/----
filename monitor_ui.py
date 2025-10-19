# monitor_ui.py - 负责所有 tkinter 相关的 GUI 元素配置、布局和更新逻辑

import tkinter as tk
import datetime
from monitor_config import CONFIG # 引入配置

class MonitorUI:
    """
    负责创建、管理和更新应用程序的 tkinter 界面元素。
    """
    def __init__(self, master):
        self.master = master
        self.components = {} # 用于存储所有动态组件的引用
        
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

    def _setup_progress_bar(self, name):
        """
        为指定的指标设置进度条组件。
        """
        frame = tk.Frame(self.master, height=CONFIG.BAR_HEIGHT, bg='SystemButtonFace')
        frame.pack(fill='x', padx=10)
        
        bg_bar = tk.Label(frame, bg='#CCCCCC')
        bg_bar.place(x=0, y=0, width=CONFIG.BAR_WIDTH, height=CONFIG.BAR_HEIGHT) 
        
        fill_bar = tk.Label(frame, bg='green', height=1)
        fill_bar.place(x=0, y=0, width=0, height=CONFIG.BAR_HEIGHT)
        
        # 将组件存入一个字典中
        self.components[f'{name}_fill_bar'] = fill_bar
        self.components[f'{name}_bg_bar'] = bg_bar


    def setup_gui(self):
        """
        配置GUI界面元素，并按照指定顺序设置标签和数据条。
        """
        self.master.title("Intel Arc A770 实时监控 (数据条增强)")
        self.master.geometry(CONFIG.INITIAL_WINDOW_SIZE)
        
        # --- 新增：时钟标签 (1 秒刷新) ---
        self.components['clock_label'] = tk.Label(self.master, 
                                     text="当前时间: 正在加载...", 
                                     font=('Arial', 14, 'bold'), 
                                     fg="#0000FF") 
        self.components['clock_label'].pack(fill='x', padx=10, pady=(5, 5))
        
        # GPU 名称标签
        self.components['name_label'] = tk.Label(self.master, text="GPU: Intel Arc A770 16GB", font=('Arial', 12, 'bold'))
        self.components['name_label'].pack(pady=5)
        
        # 1. CPU 利用率
        self.components['cpu_label'] = tk.Label(self.master, text="CPU 利用率: N/A", font=('Arial', 14), anchor='w')
        self.components['cpu_label'].pack(fill='x', padx=10, pady=(10, 0))
        self._setup_progress_bar('cpu')

        # 2. 物理内存占用
        self.components['ram_label'] = tk.Label(self.master, text="物理内存占用: N/A", font=('Arial', 14), anchor='w')
        self.components['ram_label'].pack(fill='x', padx=10, pady=(10, 0))
        self._setup_progress_bar('ram')

        # 3. 虚拟内存占用
        self.components['shared_memory_label'] = tk.Label(self.master, text="虚拟内存占用 (已提交): N/A", font=('Arial', 14), anchor='w')
        self.components['shared_memory_label'].pack(fill='x', padx=10, pady=(10, 0))
        self._setup_progress_bar('vram_system')
        
        # 4. GPU 性能占用
        self.components['utilization_label'] = tk.Label(self.master, text="GPU 性能占用: N/A", font=('Arial', 14), anchor='w')
        self.components['utilization_label'].pack(fill='x', padx=10, pady=(10, 0))
        self._setup_progress_bar('gpu_util')

        # 5. 专有显存占用
        self.components['memory_label'] = tk.Label(self.master, text="专有显存占用: N/A", font=('Arial', 14), anchor='w')
        self.components['memory_label'].pack(fill='x', padx=10, pady=(10, 0))
        self._setup_progress_bar('vram_local')

        # 6. 下载速度
        self.components['net_recv_label'] = tk.Label(self.master, text="下载速度: N/A", font=('Arial', 14), anchor='w')
        self.components['net_recv_label'].pack(fill='x', padx=10, pady=(10, 0))
        self._setup_progress_bar('net_recv')
        
        # 7. 上传速度
        self.components['net_sent_label'] = tk.Label(self.master, text="上传速度: N/A", font=('Arial', 14), anchor='w')
        self.components['net_sent_label'].pack(fill='x', padx=10, pady=(10, 0))
        self._setup_progress_bar('net_sent')

        # VRAM/VM/Webui 状态独立显示
        self.components['status_vram_label'] = tk.Label(self.master, text="状态: 监控正常", font=('Arial', 12, 'bold'), fg="green")
        self.components['status_vram_label'].pack(pady=(5, 0)) 
        
        self.components['status_vm_label'] = tk.Label(self.master, text="", font=('Arial', 12), fg="SystemButtonFace")
        self.components['status_vm_label'].pack(pady=(0, 5)) 
        
        self.components['status_webui_label'] = tk.Label(self.master, text="", font=('Arial', 12, 'bold'), fg="SystemButtonFace")
        self.components['status_webui_label'].pack(pady=(5, 5)) 

        # 日志计数标签
        self.components['log_count_label'] = tk.Label(self.master, text="总次数: 0 | 正常: 0 | 警报触发: 0", font=('Arial', 10), anchor='w')
        self.components['log_count_label'].pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)
        
        # 启动时钟独立更新
        self._update_clock()


    def _update_clock(self):
        """
        独立更新时钟标签，1000ms (1秒) 刷新一次。
        """
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.components['clock_label'].config(text=f"当前时间: {current_time}")
        self.master.after(1000, self._update_clock) 


    def update_progress_bar(self, name, percentage):
        """
        根据百分比更新指定指标的数据条 Label 的宽度和颜色。
        """
        fill_bar = self.components.get(f'{name}_fill_bar')
        if not fill_bar:
            return

        percentage = max(0, min(100, percentage))
        
        new_width = int(CONFIG.BAR_WIDTH * (percentage / 100))
        color = self._get_color(percentage)
        
        fill_bar.place(width=new_width)
        fill_bar.config(bg=color)
        
    def update_labels_on_error(self, error_msg, total_checks, success_count, failure_count):
        """
        在数据采集失败时更新所有 UI 标签。
        """
        self.components['status_vram_label'].config(text=f"错误: VRAM 数据获取失败", fg="red")
        self.components['status_vm_label'].config(text=f"详细信息: {error_msg}", fg="red")
        self.components['status_webui_label'].config(text=f"Webui 状态: 数据获取失败", fg="red")
        self.components['name_label'].config(text="!!! 致命错误: 数据获取中断 !!!", fg="red")
        self.components['log_count_label'].config(text=f"总次数: {total_checks} | 正常: {success_count} | 警报触发: {failure_count}")

    def update_labels_with_data(self, data, is_interrupt_warn_met):
        """
        用采集到的数据更新所有 Label 和进度条。
        """
        # --- 数据条更新 ---
        self.update_progress_bar('cpu', data.get('cpu_percent', 0))
        self.update_progress_bar('ram', data.get('ram_percent', 0))
        
        vram_system_used_bytes = data.get('vram_system_used_bytes', 0)
        vram_system_total_bytes = data.get('vram_system_total_bytes', 0)
        vram_system_percent = (vram_system_used_bytes / vram_system_total_bytes) * 100 if vram_system_total_bytes > 0 else 0
        self.update_progress_bar('vram_system', vram_system_percent)
        
        self.update_progress_bar('gpu_util', data.get('gpu_util', 0))
        self.update_progress_bar('vram_local', data.get('vram_local_percent', 0))
        
        self.update_progress_bar('net_recv', data.get('recv_percent', 0))
        self.update_progress_bar('net_sent', data.get('sent_percent', 0))
        
        # --- 标签文本更新 ---
        self.components['cpu_label'].config(text=f"CPU 利用率: {data.get('cpu_percent', 0):.1f}%")
        self.components['ram_label'].config(text=f"物理内存占用: {data.get('ram_used_gb', 0):.1f} GB / {data.get('ram_total_gb', 0):.1f} GB ({data.get('ram_percent', 0):.1f}%)")
        self.components['shared_memory_label'].config(text=f"虚拟内存占用 (已提交): {data.get('vram_system_used_gb', 0):.1f} GB / {data.get('vram_system_total_gb', 0):.1f} GB ({vram_system_percent:.1f}%)")
        self.components['utilization_label'].config(text=f"GPU 性能占用: {data.get('gpu_util', 0):.2f}%")
        self.components['memory_label'].config(text=f"专有显存占用: {data.get('mem_used_gb', 0):.2f} GB / {data.get('mem_total_gb', 0):.2f} GB ({data.get('vram_local_percent', 0):.1f}%)")
        self.components['net_recv_label'].config(text=f"下载速度: {data.get('recv_speed_mbps', 0):.2f} MB/s (上限 {CONFIG.MAX_BANDWIDTH_MBPS} MB/s)")
        self.components['net_sent_label'].config(text=f"上传速度: {data.get('sent_speed_mbps', 0):.2f} MB/s (上限 {CONFIG.MAX_BANDWIDTH_MBPS} MB/s)")

        # --- 状态栏更新 ---
        vram_status_msg = f"VRAM 状态: 达标 ({data.get('mem_used_gb', 0):.2f} GB)"
        if data.get('mem_used_bytes', 0) < CONFIG.MEMORY_WARN_THRESHOLD_BYTES:
            vram_status_msg = f"!!! 警报: VRAM {data.get('mem_used_gb', 0):.2f} GB (低于 {CONFIG.MEMORY_WARN_THRESHOLD_GB} GB) !!!"
            
        vm_status_msg = f"VM 状态: 正常 ({data.get('vram_system_used_gb', 0):.1f} GB)"
        vm_color = "SystemButtonFace"
        if data.get('vram_system_used_gb', 0) >= CONFIG.VIRTUAL_MEMORY_WARN_THRESHOLD_GB:
             vm_status_msg = f"风险: VM {data.get('vram_system_used_gb', 0):.1f} GB (高于 {CONFIG.VIRTUAL_MEMORY_WARN_THRESHOLD_GB} GB 存在爆内存风险!)"
             vm_color = "orange"
        
        webui_status_msg = data.get('webui_status_msg', "Webui 状态: N/A")
        
        # 根据是否触发铃声警报设置颜色
        if is_interrupt_warn_met:
            self.components['name_label'].config(text="!!! 警报: 任务可能已中断 !!!", fg="red")
            self.components['status_vram_label'].config(text=vram_status_msg, fg="red")
            self.components['status_vm_label'].config(text=vm_status_msg, fg="red")
            self.components['status_webui_label'].config(text=webui_status_msg, fg="red")
        else:
            self.components['name_label'].config(text="GPU: Intel Arc A770 16GB", fg="black")
            self.components['status_vram_label'].config(text=vram_status_msg, fg="green")
            self.components['status_vm_label'].config(text=vm_status_msg, fg=vm_color)
            self.components['status_webui_label'].config(text=webui_status_msg, fg="green")
            
    def update_log_count(self, total_checks, success_count, failure_count):
         self.components['log_count_label'].config(text=f"总次数: {total_checks} | 正常: {success_count} | 警报触发: {failure_count}")