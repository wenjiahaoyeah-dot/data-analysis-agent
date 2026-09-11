"""pytest 全局配置：隔离演示数据并禁止意外调用真实模型。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 必须在导入 app 之前设置
os.environ.setdefault("USE_MOCK_LLM", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from app.config import settings  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def isolated_storage():
    """把存储目录指向临时目录，避免污染项目的 storage/。"""
    with tempfile.TemporaryDirectory(prefix="daa_test_") as directory:
        tmp = Path(directory)
        settings.storage_dir = tmp
        settings.ensure_dirs()
        settings.force_mock = True
        yield tmp
        # 先关闭连接池并释放图缓存，再清理临时文件。
        from app.repository.factory import reset_repository

        reset_repository()


@pytest.fixture(scope="session")
def demo_dataset(isolated_storage):
    """上传 demo_sales.csv，返回 dataset_id。"""
    from app.services.upload_service import create_dataset

    csv_path = ROOT / "data" / "demo_sales.csv"
    if not csv_path.exists():
        pytest.skip("缺少 data/demo_sales.csv，请先运行 scripts/make_demo_data.py")
    meta = create_dataset([("demo_sales.csv", csv_path.read_bytes())])
    return meta


@pytest.fixture(scope="session")
def demo_db(demo_dataset):
    from app.services.upload_service import get_db

    return get_db(demo_dataset.dataset_id)
