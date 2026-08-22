"""数据接入服务：CSV / Excel 上传 -> 清洗 -> 注册到 DuckDB -> 生成数据画像。

这一层处理的是「脏数据」问题，也是实际项目里最容易翻车的地方：
编码、空列、重名列、类型推断、日期字符串……这些都会直接影响后面 SQL 能不能跑。
"""
from __future__ import annotations

import io
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import settings
from app.database.duckdb_manager import DuckDBManager
from app.repository.factory import get_repository
from app.repository.models import ANONYMOUS_USER, DatasetMeta
from app.tools.profile_tool import profile_dataframe

ALLOWED_EXT = {".csv", ".xlsx", ".xls"}
DATE_PATTERN = re.compile(r"^\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?\s*$")


class UploadError(ValueError):
    pass


# DatasetMeta 已迁到 app/repository/models.py（字段一致，末尾多了 owner_id）。
# 这里通过 import 保留模块级名字，既有的 `from ...upload_service import DatasetMeta` 继续可用。


# ---------------------------------------------------------------------- #
# 路径
# ---------------------------------------------------------------------- #
def dataset_dir(dataset_id: str) -> Path:
    return settings.datasets_dir / dataset_id


def dataset_db_path(dataset_id: str) -> Path:
    return dataset_dir(dataset_id) / "data.duckdb"


def dataset_meta_path(dataset_id: str) -> Path:
    return dataset_dir(dataset_id) / "meta.json"


def get_db(dataset_id: str, timeout_seconds: int | None = None) -> DuckDBManager:
    """返回数据面连接。

    存在性判定仍然看 DuckDB 文件——元信息可能登记在 PG，但如果分析引擎的
    数据文件丢了，任务一样跑不起来，这里提前失败更清楚。
    """
    if not dataset_db_path(dataset_id).exists():
        raise FileNotFoundError(f"数据集 {dataset_id} 不存在")
    return DuckDBManager(dataset_db_path(dataset_id), timeout_seconds=timeout_seconds)


def load_meta(dataset_id: str) -> dict[str, Any]:
    """返回 dict（保持历史契约），数据来源改由 Repository 决定。"""
    meta = get_repository().get_dataset(dataset_id)
    if meta is None:
        raise FileNotFoundError(f"数据集 {dataset_id} 不存在")
    return meta.to_dict()


# ---------------------------------------------------------------------- #
# 主流程
# ---------------------------------------------------------------------- #
def create_dataset(
    files: list[tuple[str, bytes]],
    owner_id: str = ANONYMOUS_USER,
) -> DatasetMeta:
    if not files:
        raise UploadError("没有收到任何文件")

    dataset_id = uuid.uuid4().hex[:12]
    target = dataset_dir(dataset_id)
    target.mkdir(parents=True, exist_ok=True)

    db = DuckDBManager(dataset_db_path(dataset_id))
    meta = DatasetMeta(
        dataset_id=dataset_id,
        created_at=datetime.now().isoformat(timespec="seconds"),
        owner_id=owner_id,
    )

    try:
        for filename, content in files:
            suffix = Path(filename).suffix.lower()
            if suffix not in ALLOWED_EXT:
                raise UploadError(f"不支持的文件类型 {suffix}，仅支持 {', '.join(sorted(ALLOWED_EXT))}")
            if len(content) > settings.max_upload_mb * 1024 * 1024:
                raise UploadError(f"文件 {filename} 超过 {settings.max_upload_mb}MB 限制")

            df, read_warnings = _read_table(filename, content)
            df, clean_warnings = _clean_frame(df)

            if df.empty:
                raise UploadError(f"文件 {filename} 解析后没有任何数据行")

            table_name = _unique_table_name(_safe_table_name(Path(filename).stem), meta.tables)
            row_count = db.register_table(table_name, df)

            # 回读表结构，确保记录的是数据库里的真实类型（而不是 pandas 的类型）
            schema = db.get_schema()
            columns = schema[table_name]["columns"]

            meta.files.append(filename)
            meta.tables.append(
                {"table_name": table_name, "row_count": row_count, "columns": columns}
            )
            meta.profiles.append(profile_dataframe(df, table_name))
            meta.warnings.extend(read_warnings + clean_warnings)

        # 元信息写控制面（PG 或 JSON 由配置决定）
        get_repository().save_dataset(meta)
    except Exception:
        # 任何一步失败都要把已落盘的 DuckDB 文件清掉，避免留下"有文件没元信息"的孤儿数据集
        import shutil

        shutil.rmtree(target, ignore_errors=True)
        raise

    return meta


def list_datasets(limit: int = 20, owner_id: str | None = None) -> list[dict[str, Any]]:
    """返回 dict 列表（保持历史契约）。"""
    return [m.to_dict() for m in get_repository().list_datasets(owner_id=owner_id, limit=limit)]


# ---------------------------------------------------------------------- #
# 读取与清洗
# ---------------------------------------------------------------------- #
def _read_table(filename: str, content: bytes) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        last_error: Exception | None = None
        for encoding in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
            try:
                df = pd.read_csv(io.BytesIO(content), encoding=encoding)
                if encoding not in ("utf-8-sig", "utf-8"):
                    warnings.append(f"{filename} 使用 {encoding} 编码解析成功")
                return df, warnings
            except UnicodeDecodeError as exc:
                last_error = exc
                continue
            except pd.errors.EmptyDataError as exc:
                raise UploadError(f"{filename} 是空文件") from exc
            except pd.errors.ParserError as exc:
                raise UploadError(f"{filename} 的 CSV 格式无效：{exc}") from exc
        raise UploadError(f"{filename} 编码无法识别，请另存为 UTF-8 后重试（{last_error}）")

    try:
        df = pd.read_excel(io.BytesIO(content))
    except Exception as exc:  # openpyxl 解析失败
        raise UploadError(f"{filename} 解析失败：{exc}") from exc
    return df, warnings


def _clean_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    original_cols = list(df.columns)

    # 1. 列名规范化：去空白、去不可见字符、空列名补位、重名去重
    seen: set[str] = set()
    new_cols: list[str] = []
    for idx, col in enumerate(original_cols):
        name = re.sub(r"\s+", "_", str(col).strip()) or f"col_{idx}"
        name = name.replace('"', "").replace("'", "")
        base = name
        suffix = 1
        # DuckDB 的标识符大小写不敏感，还需避免生成与已有列同名的后缀。
        while name.casefold() in seen:
            name = f"{base}_{suffix}"
            suffix += 1
        seen.add(name.casefold())
        new_cols.append(name)
    df = df.copy()
    df.columns = new_cols
    if new_cols != [str(c) for c in original_cols]:
        warnings.append(f"已规范化 {sum(1 for a, b in zip(new_cols, original_cols) if a != str(b))} 个列名")

    # 2. 删除全空行 / 全空列
    before = len(df)
    df = df.dropna(how="all").dropna(axis=1, how="all")
    if len(df) != before:
        warnings.append(f"删除 {before - len(df)} 行全空数据")

    # 3. 日期字符串转 datetime（DuckDB 里才能用 strftime / INTERVAL）
    for col in df.columns:
        df[col] = _maybe_datetime(df[col], col)

    # 4. 空字符串转 NaN，避免 SUM/AVG 被 "" 干扰
    # 注意：pandas 3.x 默认字符串 dtype 是 "str" 而非 "object"，不能只判 object
    obj_cols = [c for c in df.columns if pd.api.types.is_string_dtype(df[c].dtype) or df[c].dtype == object]
    for col in obj_cols:
        df[col] = df[col].apply(lambda v: v.strip() if isinstance(v, str) else v)
        df[col] = df[col].replace({"": None})

    # 5. 重复行统计（只提示，不删除，避免误伤合法重复记录）
    dup = int(df.duplicated().sum())
    if dup:
        warnings.append(f"检测到 {dup} 行完全重复的记录（已保留，可在分析时用 DISTINCT 处理）")

    return df.reset_index(drop=True), warnings


def _maybe_datetime(series: pd.Series, name: str) -> pd.Series:
    # pandas 3.x 的字符串列 dtype 是 "str"（StringDtype），不再是 "object"
    is_text = pd.api.types.is_string_dtype(series.dtype) or series.dtype == object
    if not is_text:
        return series
    sample = series.dropna()
    if sample.empty:
        return series
    # Permissive dateutil parsing can silently turn e.g. November14,2008 into
    # year 0001. Only infer dates when every value has an explicit ISO-like year.
    if not sample.astype(str).str.fullmatch(DATE_PATTERN).all():
        return series
    try:
        parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    except (ValueError, TypeError):
        return series
    # 不因类型推断把少量无法解析的原始值静默变成空值。
    if parsed[series.notna()].notna().all():
        return parsed
    return series


def _safe_table_name(stem: str) -> str:
    name = re.sub(r"[^0-9a-zA-Z_\u4e00-\u9fff]", "_", stem).strip("_").lower()
    name = re.sub(r"_+", "_", name)
    if not name:
        return "data"
    if name[0].isdigit():
        name = f"t_{name}"
    return name[:48]


def _unique_table_name(name: str, existing: list[dict[str, Any]]) -> str:
    used = {t["table_name"] for t in existing}
    if name not in used:
        return name
    idx = 2
    while f"{name}_{idx}" in used:
        idx += 1
    return f"{name}_{idx}"
