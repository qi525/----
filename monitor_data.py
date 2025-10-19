# monitor_data.py - 负责所有数据采集、I/O 和 Webui 文件统计的逻辑

import subprocess
import psutil
import platform
import time
from loguru import logger
import os
import datetime
from monitor_config import CONFIG # 引入配置

class DataCollector:
    """
    负责从系统和文件系统采集所有监控数据。
    """
    def __init__(self):
        self.os_type = platform.system()
        # 网络状态追踪变量
        self.last_net_bytes_sent = 0
        self.last_net_bytes_recv = 0
        self.last_update_time = time.time() 
        
        # Webui 文件监控追踪变量
        self.last_webui_check_time = time.time()
        self.last_webui_file_count = -1
        self.consecutive_webui_no_increase_count = 0
        
        # 第一次运行时初始化网络计数器
        net_io = psutil.net_io_counters()
        self.last_net_bytes_sent = net_io.bytes_sent
        self.last_net_bytes_recv = net_io.bytes_recv

        
    def _get_windows_commit_charge(self):
        """
        [Windows 平台专用]
        通过 PowerShell 性能计数器获取系统的 “已提交” 内存（Commit Charge）总数 (Bytes)。
        """
        if self.os_type != "Windows":
             return 0.0 # 非 Windows 返回 0
             
        try:
            cmd = r'powershell -ExecutionPolicy Bypass -Command "(Get-Counter \"\Memory\Committed Bytes\").CounterSamples | Select-Object -ExpandProperty CookedValue"'
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            return float(result.stdout.strip() or 0)
        
        except Exception as e:
            logger.error(f"通过 PowerShell 获取 Committed Bytes 失败: {e}")
            return 0.0

    def get_system_stats(self):
        """
        使用 psutil 库获取系统 CPU、物理内存和虚拟内存数据及其占用百分比。
        """
        try:
            # 1. CPU 利用率
            cpu_percent = psutil.cpu_percent(interval=None) 
            
            # 2. 物理内存 (RAM)
            ram_stats = psutil.virtual_memory()
            
            # 3. 虚拟内存 (已提交使用量 - Commit Charge)
            swap_stats = psutil.swap_memory()
            
            if self.os_type == "Windows":
                 vram_system_used_bytes = self._get_windows_commit_charge()
            else:
                 # 非 Windows 系统，回退到 psutil 的 RAM + Swap 使用量
                 vram_system_used_bytes = swap_stats.used + ram_stats.used

            # 4. 虚拟内存 (总可提交量 - Commit Limit)
            vram_system_total_bytes = swap_stats.total + ram_stats.total
            
            return {
                'cpu_percent': cpu_percent, 
                'ram_used_gb': ram_stats.used / 1024**3, 
                'ram_total_gb': ram_stats.total / 1024**3, 
                'ram_percent': ram_stats.percent, 
                'vram_system_used_bytes': vram_system_used_bytes,
                'vram_system_total_bytes': vram_system_total_bytes,
            }
        
        except Exception as e:
            logger.error(f"通过 psutil 获取系统数据失败: {e}")
            return None


    def get_gpu_stats_windows(self):
        """
        [Windows 平台专用]
        通过 PowerShell 性能计数器获取 GPU **综合性能占用**和专有显存占用。
        """
        if self.os_type != "Windows":
             # 硬编码总显存，返回 0 占用
             return {'gpu_util': 0.0, 'mem_used_bytes': 0.0, 'mem_total_bytes': 16 * 1024**3, 'vram_local_percent': 0.0}

        try:
            # --- 1. GPU 综合性能占用 ---
            util_cmd = r'powershell -ExecutionPolicy Bypass -Command "((Get-Counter \"\GPU Engine(*)\Utilization Percentage\").CounterSamples | Select-Object -ExpandProperty CookedValue | Measure-Object -Average).Average"'
            result = subprocess.run(util_cmd, capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            gpu_util = float(result.stdout.strip() or 0.0) 
            
            # --- 2. 专有显存占用 (Local Usage) ---
            mem_cmd = r'powershell -ExecutionPolicy Bypass -Command "((Get-Counter \"\GPU Process Memory(*)\Local Usage\").CounterSamples | Select-Object -ExpandProperty CookedValue | Measure-Object -Sum).Sum"'
            result = subprocess.run(mem_cmd, capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            mem_used_bytes = float(result.stdout.strip() or 0)
            
            # 硬编码总显存 (Intel Arc A770 16GB)
            mem_total_bytes = 16 * 1024**3
            vram_local_percent = (mem_used_bytes / mem_total_bytes) * 100 if mem_total_bytes > 0 else 0
            
            return {
                'gpu_util': gpu_util, 
                'mem_used_bytes': mem_used_bytes, 
                'mem_total_bytes': mem_total_bytes, 
                'vram_local_percent': vram_local_percent
            }

        except Exception as e:
            logger.error(f"获取 GPU 数据时发生错误: {e}")
            return {'gpu_util': 0.0, 'mem_used_bytes': 0.0, 'mem_total_bytes': 16 * 1024**3, 'vram_local_percent': 0.0}


    def get_net_io_stats(self, current_time):
        """
        获取网络 I/O 统计并计算速度。
        """
        net_io = psutil.net_io_counters()
        current_bytes_sent = net_io.bytes_sent
        current_bytes_recv = net_io.bytes_recv
        
        time_diff = current_time - self.last_update_time
        
        recv_speed_mbps = 0.0
        sent_speed_mbps = 0.0
        recv_percent = 0.0
        sent_percent = 0.0
        
        # 避免除以零或初始值计算
        if time_diff > 0 and self.last_net_bytes_sent != 0:
             recv_speed_bps = (current_bytes_recv - self.last_net_bytes_recv) / time_diff
             sent_speed_bps = (current_bytes_sent - self.last_net_bytes_sent) / time_diff
             
             recv_speed_mbps = recv_speed_bps / 1024**2
             sent_speed_mbps = sent_speed_bps / 1024**2
             
             # 进度条的百分比计算
             recv_percent = (recv_speed_mbps / CONFIG.MAX_BANDWIDTH_MBPS) * 100
             sent_percent = (sent_speed_mbps / CONFIG.MAX_BANDWIDTH_MBPS) * 100
             
        # 更新上次的计数器和时间戳
        self.last_net_bytes_sent = current_bytes_sent
        self.last_net_bytes_recv = current_bytes_recv
        self.last_update_time = current_time
        
        return {
            'recv_speed_mbps': recv_speed_mbps, 
            'sent_speed_mbps': sent_speed_mbps, 
            'recv_percent': recv_percent, 
            'sent_percent': sent_percent
        }


    def _count_files_in_output_dir(self):
        """
        获取当天 Webui 输出目录的文件数量。
        """
        try:
            today_date_str = datetime.datetime.now().strftime("%Y-%m-%d")
            full_path = os.path.join(CONFIG.WEBUI_OUTPUT_BASE_DIR, today_date_str)
            
            if not os.path.exists(full_path):
                return 0

            file_count = 0
            for item in os.listdir(full_path):
                 item_path = os.path.join(full_path, item)
                 if os.path.isfile(item_path):
                      file_count += 1
                      
            return file_count
            
        except Exception as e:
            logger.error(f"统计 Webui 输出目录文件数量失败: {e}")
            return 0 

    def check_webui_generation_status(self, current_time):
        """
        检查 Webui 文件数量是否在周期内增加，用于判断生成任务是否中断。
        """
        
        current_file_count = self._count_files_in_output_dir()
        is_webui_alert_active = False
        webui_status_msg = f"Webui 状态: 正在生成 (文件数 {current_file_count})"
        
        if self.last_webui_file_count == -1:
             self.last_webui_file_count = current_file_count
             self.last_webui_check_time = current_time
             logger.info(f"Webui 监控初始化：当前文件数 {current_file_count}。")
             return False, f"Webui 状态: 监控初始化完成 (文件数 {current_file_count})", current_file_count
             
        time_since_last_check = current_time - self.last_webui_check_time
        
        # 检查是否达到一个完整的监测周期
        if time_since_last_check >= CONFIG.WEBUI_CHECK_INTERVAL_SECONDS - 1.0: 
             
             if current_file_count > self.last_webui_file_count:
                  # 文件数量有增加，重置计数器
                  self.consecutive_webui_no_increase_count = 0
             elif current_file_count <= self.last_webui_file_count:
                  # 文件数量未增加
                  self.consecutive_webui_no_increase_count += 1
                  
                  if self.consecutive_webui_no_increase_count >= CONFIG.WEBUI_WARN_CYCLE_THRESHOLD:
                       is_webui_alert_active = True
                       webui_status_msg = f"!!! 警报: Webui 生成任务可能中断 (文件数 {current_file_count} 持续 {CONFIG.WEBUI_CHECK_INTERVAL_SECONDS * CONFIG.WEBUI_WARN_CYCLE_THRESHOLD}s 未增加) !!!"
                  else:
                       webui_status_msg = f"Webui 警报: 文件数 {current_file_count} 未增加! 连续未增加周期: {self.consecutive_webui_no_increase_count}/{CONFIG.WEBUI_WARN_CYCLE_THRESHOLD}"

             # 更新上次记录值
             self.last_webui_file_count = current_file_count
             self.last_webui_check_time = current_time
             
        elif self.consecutive_webui_no_increase_count > 0:
             # 在周期内，但正在警报计数中
             webui_status_msg = f"Webui 警报: 文件数 {self.last_webui_file_count} 未增加! 连续未增加周期: {self.consecutive_webui_no_increase_count}/{CONFIG.WEBUI_WARN_CYCLE_THRESHOLD}"
             if self.consecutive_webui_no_increase_count >= CONFIG.WEBUI_WARN_CYCLE_THRESHOLD:
                 is_webui_alert_active = True
                 webui_status_msg = f"!!! 警报: Webui 生成任务可能中断 (文件数 {self.last_webui_file_count} 持续 {CONFIG.WEBUI_CHECK_INTERVAL_SECONDS * CONFIG.WEBUI_WARN_CYCLE_THRESHOLD}s 未增加) !!!"


        return is_webui_alert_active, webui_status_msg, current_file_count