import tkinter as tk
from datetime import datetime
import sys

# 设置全局变量以便在不使用loguru的情况下进行简单的控制台输出
# 实际生产环境中，您可能希望使用loguru或其他日志系统
# 但考虑到这是一个极简的UI应用，我们暂时使用print
def print_log(message):
    """简单的控制台日志输出函数"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")

class TimeApp:
    """
    一个简单的Tkinter应用，用于显示实时更新的当前时间。
    """
    def __init__(self, master):
        self.master = master
        master.title("实时时间显示器")
        
        # 设置窗口大小和位置 (可选)
        master.geometry("400x150")

        # 创建一个Label用于显示时间
        # 设置较大的字体以增强显示效果
        self.time_label = tk.Label(
            master, 
            text="正在加载时间...", 
            font=("Helvetica", 24, "bold"), 
            fg="blue"  # 字体颜色
        )
        self.time_label.pack(pady=30)
        
        print_log("UI界面初始化完成。")

        # 立即调用一次更新时间函数，并启动循环更新
        self.update_time()

    def update_time(self):
        """
        获取当前时间并更新Label文本。
        然后安排在1000ms后再次执行自身。
        """
        try:
            # 获取当前时间
            now = datetime.now()
            # 格式化时间：年-月-日 时:分:秒
            current_time = now.strftime("%Y-%m-%d %H:%M:%S")
            
            # 更新Label的文本
            self.time_label.config(text=current_time)
            
            # 使用after方法安排在1000毫秒后再次调用update_time方法
            # 这是Tkinter实现周期性任务的标准方法
            self.master.after(1000, self.update_time)
            
        except Exception as e:
            # 异常警报和日志输出
            error_msg = f"更新时间时发生异常: {e}"
            print_log(f"!!! 异常警报: {error_msg} !!!")
            # 可以在这里停止更新或尝试恢复
            # 对于这个应用，我们将允许它继续尝试
            self.master.after(1000, self.update_time)
            
# 主程序入口
if __name__ == "__main__":
    
    # 计数器/任务预览：对于简单的无限循环应用，主要计数器是启动成功/失败
    task_name = "启动实时时间显示器"
    try:
        print_log(f"--- 尝试 {task_name} ---")
        
        root = tk.Tk()
        app = TimeApp(root)
        
        print_log(f"--- {task_name} 成功启动 ---")
        
        # 进入Tkinter的主事件循环
        root.mainloop()

        print_log(f"--- {task_name} 成功退出 ---")

    except Exception as main_e:
        # 捕获主程序启动时的异常
        print_log(f"!!! {task_name} 启动失败 !!!")
        print_log(f"致命错误: {main_e}")
        # 确保在失败时退出程序
        sys.exit(1)