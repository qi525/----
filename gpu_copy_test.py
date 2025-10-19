import time
import subprocess
import random
from loguru import logger
import sys

#用于测试gpu copy占用率的脚本


# 设置 loguru，确保完美的日志输出
logger.remove()
# 强制将日志输出到标准错误流，确保在控制台显示，而不是隐藏的日志文件
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
)


# ====================================================================================
# 新功能：获取 Intel A770 16GB GPU Copy 占用率
# ------------------------------------------------------------------------------------
# 采用方法：通过 Python 的 subprocess 模块调用系统命令行工具（如 intel_gpu_top），解析输出。
# ------------------------------------------------------------------------------------

def get_gpu_copy_usage():
    """
    【⚠️ 重点：此函数当前使用**模拟数据**，您需要根据实际操作系统和工具进行修改】
    
    功能：获取 Intel A770 的 GPU Copy 占用率。
    
    :return: GPU Copy 占用百分比 (0-100)，如果获取失败则返回 None。
    """
    
    # --------------------------------------------------------------------------------
    # ⚠️ 替换以下模拟代码为实际的命令行调用和解析逻辑
    # --------------------------------------------------------------------------------
    
    # 【当前为模拟代码】
    # 模拟逻辑：大部分时间维持高占用 (>50%)，以测试失败条件。
    # 随机产生一个 0 到 1 之间的浮点数，如果小于 0.6 (60%的概率)，则生成高占用
    if random.random() < 0.6: 
        usage = random.randint(50, 100) # 模拟成功的高占用 (>= 50%)
    else:
        usage = random.randint(10, 29)  # 模拟低于 30% 的低占用 (失败条件)
    
    # ⚠️ 实际环境中您需要使用类似以下代码（以 Linux/intel_gpu_top 为例）：
    # try:
    #     # 仅运行一次，并以批处理模式输出
    #     cmd = ['intel_gpu_top', '-o', '-b', '-l', '1'] 
    #     result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=5)
    #     
    #     # 假设 'Copy' 引擎的占用信息在输出中是可解析的
    #     for line in result.stdout.splitlines():
    #         if 'Copy:' in line:
    #             # 需要根据实际输出格式进行精确解析
    #             # usage = float(line.split('Copy:')[1].strip().replace('%', '').split()[0])
    #             # return usage
    #             pass
    #     logger.warning("在命令行输出中未找到 Copy 引擎的占用信息。")
    #     return None 
    # except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
    #     logger.error(f"调用 GPU 监控工具失败或超时: {e}")
    #     return None
    
    return usage

def check_gpu_copy_status(target_low_usage=30, consecutive_fail_count=7, interval_seconds=1.5):
    """
    主监控逻辑。
    
    :param target_low_usage: 低于此百分比视为一次“失败”检查。
    :param consecutive_fail_count: 连续失败多少次判定为“任务失败”。
    :param interval_seconds: 检查间隔时间 (秒)。
    :return: True 如果任务成功维持高占用，False 如果判定为连续失败。
    """
    
    # 初始化计数器
    low_usage_counter = 0  # 连续低于阈值的次数
    total_checks = 0       # 总检查次数
    success_checks = 0     # 成功 (高于或等于阈值) 的次数
    
    # 预设总数量，方便日志计数器显示
    TOTAL_EXPECTED_CHECKS = 1000 
    
    print("-" * 50)
    print(f"方案一采用方法：调用系统命令行工具（如 intel_gpu_top）解析 GPU Copy 占用率。")
    print(f"监控目标：确保 Copy 占用率维持在 {target_low_usage}% 以上。")
    print(f"失败条件：连续 {consecutive_fail_count} 次检查低于 {target_low_usage}%。")
    print("-" * 50)

    logger.info(f"--- GPU Copy 占用监控启动 ---")

    try:
        while low_usage_counter < consecutive_fail_count:
            start_time = time.time()
            total_checks += 1
            
            # 实时任务预览日志 (符合您的日志计数器要求)
            logger.info(f"--- 实时预览 --- 总量:{TOTAL_EXPECTED_CHECKS} | 当前:{total_checks} | 成功:{success_checks} | 失败:{low_usage_counter}")
            
            usage = get_gpu_copy_usage()
            
            if usage is None:
                # 获取失败也视为一次低占用（最安全处理）
                current_status = f"【获取失败/中断】"
                is_low_usage = True
                low_usage_counter += 1
            elif usage < target_low_usage:
                low_usage_counter += 1
                success_checks = 0 # 连续失败，清空成功计数
                current_status = f"【低于阈值】 - Copy 占用率: {usage:.2f}%"
                is_low_usage = True
            else:
                low_usage_counter = 0  # 重置连续失败计数器
                success_checks += 1
                current_status = f"【正常运行】 - Copy 占用率: {usage:.2f}%"
                is_low_usage = False

            
            # 使用 print 确保重要的结果信息直接显示在控制台
            print(f"| 第 {total_checks} 次检测 | 占用率: {usage:.2f}% | 状态: {current_status.split('-')[0].strip()} | 连续失败: {low_usage_counter}/{consecutive_fail_count}")

            # 异常警报 (连续失败次数接近阈值时发出警告)
            if low_usage_counter >= consecutive_fail_count - 2:
                 logger.warning(f"⚠️ 连续低占用警报！已达到 {low_usage_counter} 次，请检查任务状态！")

            # 等待到下一个周期
            elapsed_time = time.time() - start_time
            sleep_time = interval_seconds - elapsed_time
            
            if sleep_time > 0:
                time.sleep(sleep_time)
            
            # 模拟程序运行结束的成功情况 (例如，检测 20 次成功后自动停止，避免无限循环)
            if success_checks >= 20 and low_usage_counter == 0:
                logger.success(f"已连续 {success_checks} 次维持高占用，判定为 '任务成功'。")
                return True


        # 循环结束，表示连续失败次数达到阈值
        print("-" * 50)
        logger.critical(f"连续 {consecutive_fail_count} 次低于 {target_low_usage}% 判定为 '任务失败'。")
        return False

    except KeyboardInterrupt:
        logger.warning("监控程序被用户中断。")
        return None
    except Exception as e:
        logger.exception(f"监控程序发生意外错误: {e}")
        return None

if __name__ == "__main__":
    logger.info("程序开始运行...")
    
    # 运行监控，并获取结果
    monitoring_result = check_gpu_copy_status(
        target_low_usage=30, 
        consecutive_fail_count=7, 
        interval_seconds=1.5
    )

    print("-" * 50)
    if monitoring_result is False:
        print("最终检测结果：失败 (Copy 占用率长时间过低)")
    elif monitoring_result is True:
        print("最终检测结果：成功 (Copy 占用率维持正常)")
    else:
        print("最终检测结果：中断或错误")
    print("-" * 50)
    
    logger.info("程序运行结束。")