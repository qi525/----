# sd_webui_monitor.py - 主应用程序，控制线程、警报逻辑和整体流程

import tkinter as tk
import time
import sys
import platform
from loguru import logger
import winsound
import concurrent.futures 

# 导入模块
from monitor_config import CONFIG
from monitor_data import DataCollector
from monitor_ui import MonitorUI
from just_playback import Playback

# --- Loguru 配置 (完美的日志输出) ---
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>", enqueue=True)

# --------------------------------------------------------------------------

class IntelArcMonitorApp:
    """
    Intel Arc A770 实时监控应用程序，核心控制层。
    负责初始化、线程调度和警报逻辑。
    """
    
    def __init__(self, master):
        self.master = master
        self.os_type = platform.system()
        
        # 实例化子模块
        self.data_collector = DataCollector()
        self.ui_manager = MonitorUI(master)
        
        # 【新增】线程池设置：用于后台获取GPU/系统数据，避免UI卡顿
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=2) 
        
        # just_playback 播放器对象
        self.playback = None 
        
        # 初始化计数器和状态变量
        self.total_checks = 0
        self.success_count = 0
        self.failure_count = 0
        self.is_alarm_active = False 
        self.consecutive_warn_count = 0
        self.alarm_start_time = None 
        self.playback_count = 0 
        
        # VM 记录变量
        self.first_vm_record_time = None
        self.first_vm_used_gb = None
        self.last_vm_record_time = None
        self.last_vm_used_gb = None
        
        if self.os_type != "Windows":
             logger.warning(f"当前操作系统为 {self.os_type}。注意：本应用的核心功能和声音警报主要在 Windows 上有效。")

        # 创建并配置tkinter界面
        self.ui_manager.setup_gui()
        
        # 启动定时更新
        self.update_gpu_info()
        
        # 绑定窗口关闭事件
        master.protocol("WM_DELETE_WINDOW", self.on_closing) 

        # 初始化播放器
        self._init_playback()
        
        logger.info("Intel Arc GPU 监控应用启动成功。")

    def _init_playback(self):
        """
        初始化播放器并预加载 WAV 文件。
        """
        if self.os_type == "Windows":
            try:
                self.playback = Playback()
                self.playback.load_file(CONFIG.ALARM_WAV_FILE)
                logger.success(f"警报文件 '{CONFIG.ALARM_WAV_FILE}' 加载成功。")
            except Exception as e:
                logger.error(f"初始化或加载警报文件失败（just_playback）：{e}。将使用系统默认警报音作为回退。")

    def on_closing(self):
        """
        处理窗口关闭事件，优雅地关闭线程池。
        """
        logger.info("应用接收到关闭信号，正在关闭线程池...")
        self.executor.shutdown(wait=False, cancel_futures=True) 
        self.master.destroy() 

    def _play_beep_alarm(self):
        """
        播放自定义 WAV 文件（使用 just_playback）作为警报。
        """
        self.playback_count += 1
        logger.info(f"尝试播放声音警报 (第 {self.playback_count} 次循环)...")
        if self.os_type == "Windows":
            if self.playback:
                try:
                    if self.playback.active:
                        self.playback.stop() 
                    
                    self.playback.seek(0) 
                    self.playback.play()
                except Exception as e:
                    logger.error(f"播放自定义声音文件失败（just_playback）：{e}。")
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            else:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        else:
             print('\a', end='', flush=True) 
             logger.warning("非 Windows 系统，使用终端铃声作为替代。")

    def _log_vm_usage_periodically(self, current_time, vram_system_used_gb):
        """
        周期性记录虚拟内存（VM）使用量和增量。
        """
        if self.first_vm_record_time is None:
            self.first_vm_record_time = current_time
            self.first_vm_used_gb = vram_system_used_gb
            self.last_vm_record_time = current_time
            self.last_vm_used_gb = vram_system_used_gb
            log_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_time))
            logger.info("--- 虚拟内存 (VM) 周期记录启动 ---")
            logger.info(f"【首次记录】{log_time_str} | VM 大小: {vram_system_used_gb:.1f} GB")
            return

        time_since_last_record = current_time - self.last_vm_record_time
        
        if time_since_last_record >= CONFIG.VM_LOG_INTERVAL_SECONDS - 1.0: 
            increase_gb = vram_system_used_gb - self.last_vm_used_gb
            log_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_time))
            
            logger.info(f"【周期记录】{log_time_str} | VM 大小: {vram_system_used_gb:.1f} GB | 增加量: {increase_gb:.1f} GB (相比上次记录)")
            
            self.last_vm_record_time = current_time
            self.last_vm_used_gb = vram_system_used_gb

    def _fetch_all_data(self):
        """
        【后台线程】负责所有阻塞式的数据获取工作。
        """
        current_time = time.time()
        
        # 1. GPU 专有数据
        gpu_stats = self.data_collector.get_gpu_stats_windows()

        # 2. 系统数据
        system_stats = self.data_collector.get_system_stats()
        if system_stats is None:
             raise Exception("系统数据获取失败。")
        
        # 3. 网络 I/O 统计
        net_stats = self.data_collector.get_net_io_stats(current_time)
        
        # 4. Webui 生成状态
        is_webui_alert_active, webui_status_msg, current_file_count = self.data_collector.check_webui_generation_status(current_time)

        # 格式化数据并合并
        system_stats['vram_system_used_gb'] = system_stats['vram_system_used_bytes'] / 1024**3
        system_stats['vram_system_total_gb'] = system_stats['vram_system_total_bytes'] / 1024**3
        gpu_stats['mem_used_gb'] = gpu_stats['mem_used_bytes'] / 1024**3
        gpu_stats['mem_total_gb'] = gpu_stats['mem_total_bytes'] / 1024**3
        
        return {
            **system_stats, **gpu_stats, **net_stats,
            'MAX_BANDWIDTH_MBPS': CONFIG.MAX_BANDWIDTH_MBPS,
            'current_time': current_time,
            'is_webui_alert_active': is_webui_alert_active, 
            'webui_status_msg': webui_status_msg, 
            'current_file_count': current_file_count, 
            'error': None
        }

    def update_gpu_info(self):
        """
        【主线程】启动后台任务获取数据，并安排在后台任务完成后更新UI。
        """
        self.total_checks += 1
        
        future = self.executor.submit(self._fetch_all_data)
        future.add_done_callback(self._on_data_fetch_complete)
        
        self.master.after(CONFIG.UPDATE_INTERVAL_MS, self.update_gpu_info)

    def _on_data_fetch_complete(self, future):
        """
        【工作线程】任务完成后执行的回调函数。将UI更新任务调度回主线程。
        """
        try:
            fetched_data = future.result()
            self.master.after(0, lambda: self._process_fetched_data(fetched_data=fetched_data))

        except concurrent.futures.CancelledError:
            logger.info("数据获取任务被取消 (线程池关闭)。")
        except Exception as e:
            self.master.after(0, lambda: self._process_fetched_data(error=e))
            logger.error(f"后台线程数据获取失败: {e}")
    
    def _process_fetched_data(self, fetched_data=None, error=None):
        """
        【主线程】负责处理从后台获取的数据，更新UI、执行警报逻辑和日志记录。
        """
        
        if error or fetched_data is None:
            self.failure_count += 1
            error_msg = f"数据获取失败: {error or '空数据'}"
            logger.error(error_msg)
            # 使用 UI 模块更新错误信息
            self.ui_manager.update_labels_on_error(error_msg, self.total_checks, self.success_count, self.failure_count)
            return

        # --- 警报逻辑 ---
        mem_used_bytes = fetched_data.get('mem_used_bytes', 0)
        mem_used_gb = fetched_data.get('mem_used_gb', 0)
        vram_system_used_gb = fetched_data.get('vram_system_used_gb', 0)
        is_webui_alert_active = fetched_data.get('is_webui_alert_active', False)
        current_file_count = fetched_data.get('current_file_count', 0)
        
        # VRAM 或 Webui 警报
        is_vram_warn_met = mem_used_bytes < CONFIG.MEMORY_WARN_THRESHOLD_BYTES
        is_interrupt_warn_met = is_vram_warn_met or is_webui_alert_active
        
        # 更新 UI
        self.ui_manager.update_labels_with_data(fetched_data, is_interrupt_warn_met)
        
        if is_interrupt_warn_met:
            
            if not self.is_alarm_active:
                self.consecutive_warn_count += 1
                
                if self.consecutive_warn_count >= CONFIG.WARN_COUNT_THRESHOLD:
                    
                    if self.alarm_start_time is None:
                         self.alarm_start_time = time.time()
                         start_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.alarm_start_time))
                         
                         warn_reasons = []
                         if is_vram_warn_met:
                              warn_reasons.append(f"VRAM 低于 {CONFIG.MEMORY_WARN_THRESHOLD_GB} GB")
                         if is_webui_alert_active:
                              warn_reasons.append(f"Webui 文件数 {current_file_count} 持续未增加")
                              
                         logger.critical(f"正式警报已启动！报警开始时间: {start_time_str}。原因: {', '.join(warn_reasons)}")
                    
                    self.is_alarm_active = True
                    self.failure_count += 1
                    self.consecutive_warn_count = 0
                    self._play_beep_alarm()
                else:
                     current_warn_parts = []
                     if is_vram_warn_met:
                          current_warn_parts.append(f"VRAM 低于 {CONFIG.MEMORY_WARN_THRESHOLD_GB} GB")
                     if is_webui_alert_active:
                          current_warn_parts.append("Webui 文件数持续未增加")
                          
                     logger.warning(f"中断警报条件满足 ({', '.join(current_warn_parts)})，连续计数: {self.consecutive_warn_count}/{CONFIG.WARN_COUNT_THRESHOLD}。")
                     
            elif self.is_alarm_active and self.playback and not self.playback.active:
                 self._play_beep_alarm()
                     
        else: # 警报条件不满足 (VRAM >= 8 GB 且 Webui 状态正常)
            
            if self.is_alarm_active:
                self.is_alarm_active = False
                
                if self.alarm_start_time is not None:
                     duration = time.time() - self.alarm_start_time
                     logger.critical(f"警报已解除。警报持续时间: {duration:.1f} 秒，歌曲循环播放总次数: {self.playback_count} 次。")
                     self.alarm_start_time = None
                     self.playback_count = 0
                
                if self.playback and self.playback.active:
                    self.playback.stop()
                    logger.info("中断警报条件解除，已强制停止警报音乐。")
                    
            if self.consecutive_warn_count > 0:
                logger.info(f"所有警报条件解除，连续计数器重置 (原值: {self.consecutive_warn_count})。")
                self.consecutive_warn_count = 0
            
            self.success_count += 1
            
            log_msg = f"状态正常 | GPU Util: {fetched_data.get('gpu_util', 0):.2f}% | VRAM: {mem_used_gb:.2f} GB | VM: {vram_system_used_gb:.1f} GB | CPU Util: {fetched_data.get('cpu_percent', 0):.1f}% | Net Recv: {fetched_data.get('recv_speed_mbps', 0):.2f} MB/s | Webui File Count: {current_file_count}"
            logger.info(log_msg)
            
        # 周期性 VM 使用量记录
        self._log_vm_usage_periodically(fetched_data['current_time'], vram_system_used_gb)
        
        # 更新日志计数器
        self.ui_manager.update_log_count(self.total_checks, self.success_count, self.failure_count)


# --------------------------------------------------------------------------

if __name__ == '__main__':
    try:
        root = tk.Tk()
        print("=" * 70)
        print("程序核心功能 1: 持续监控 VRAM 使用情况，确保 Webui 等任务持续运行 (VRAM >= 8GB)。")
        print("程序核心功能 2: 持续监控 Webui 输出目录文件数量，未增加则警报。")
        print("次要功能: 监控 VM 使用量，超过 80GB 时给出橙色风险提醒，并周期性记录其增长量。")
        print("【架构优化】：已拆分为 Config, Data, UI, App 四个模块，便于维护。")
        print("=" * 70)
        app = IntelArcMonitorApp(root)
        root.mainloop()
            
    except Exception as main_e:
        logger.critical(f"程序主循环发生致命错误: {main_e}")