from datetime import datetime, timezone
import pytz


def timestamp2datetime(timestamp: int) -> str:
    '''
    将UTC时间戳转换为东八区时间字符串，输出时间字符串格式为"%Y-%m-%d %H:%M:%S"

    Args:
        timestamp: UTC时间戳

    Returns:
        str: 东八区时间字符串，格式为"%Y-%m-%d %H:%M:%S"，如果失败则返回空字符串
    '''
    if not timestamp:
        return ""
    
    try:
        # 创建东八区时区对象（与datetime2timestamp保持一致）
        tz_china = pytz.timezone('Asia/Shanghai')
        
        # 从UTC时间戳创建UTC datetime对象
        dt_utc = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        
        # 转换到东八区时间
        dt_china = dt_utc.astimezone(tz_china)
        
        # 格式化为字符串
        return dt_china.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        print(f"时间转换失败: {e}")
        return ""




def datetime2timestamp(datatime: str) -> int:
    '''
    将东八区时间转换为UTC时间戳，输入时间字符串格式为"%Y-%m-%d %H:%M:%S"

    Args:
        datatime: 东八区时间字符串，格式为"%Y-%m-%d %H:%M:%S"

    Returns:
        int: UTC时间戳，如果失败则返回0
    '''

    if not datatime:
        return 0
    try:
        # 创建东八区时区对象
        tz_china = pytz.timezone('Asia/Shanghai')
        
        # 解析时间字符串为datetime对象（输入时间是东八区时间）
        dt = datetime.strptime(datatime, "%Y-%m-%d %H:%M:%S")
        
        # 将datetime对象标记为东八区时间
        dt_china = tz_china.localize(dt)
        
        # 转换为UTC时间戳
        return int(dt_china.timestamp())
    except Exception as e:
        print(f"时间转换失败: {e}")
        return 0
        