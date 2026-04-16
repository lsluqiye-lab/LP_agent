import qlib
from qlib.constant import REG_US
from qlib.utils import exists_qlib_data
from qlib.tests.data import GetData
import os

def init_qlib_environment():
    provider_uri = "data/qlib_data"
    os.makedirs(provider_uri, exist_ok=True)
    
    # 初始化 Qlib (美股模式)
    qlib.init(provider_uri=provider_uri, region=REG_US)
    print(f"Qlib 初始化完成，数据目录: {provider_uri}")

    # 这里我们演示如何准备数据。由于 Qlib 下载全量美股数据很慢，
    # 我们先检查数据是否存在，如果不存在，提示需要下载。
    if not exists_qlib_data(provider_uri):
        print("未发现 Qlib 格式数据，正在尝试下载轻量级示例数据用于测试...")
        # GetData.get_qlib_data 会下载一个默认的轻量级数据集
        GetData().get_qlib_data(target_dir=provider_uri, region=REG_US)
    else:
        print("Qlib 数据已存在，跳过下载。")

if __name__ == "__main__":
    init_qlib_environment()
