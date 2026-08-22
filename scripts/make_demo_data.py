"""生成演示用的电商销售数据（orders + products 两张表）。

用法：
    python scripts/make_demo_data.py

生成文件：
    data/demo_sales.csv   订单明细表（约 2000 行，覆盖最近 6 个月）
    data/demo_products.csv 商品维表（用于多表关联测试）
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

random.seed(42)

PRODUCTS = [
    ("P001", "无线蓝牙耳机", "数码配件", 299.0),
    ("P002", "机械键盘", "数码配件", 459.0),
    ("P003", "便携充电宝", "数码配件", 159.0),
    ("P004", "智能手表", "智能穿戴", 1099.0),
    ("P005", "运动手环", "智能穿戴", 269.0),
    ("P006", "降噪头戴耳机", "数码配件", 899.0),
    ("P007", "手机支架", "数码配件", 39.0),
    ("P008", "平板保护壳", "数码配件", 89.0),
    ("P009", "智能音箱", "智能家居", 399.0),
    ("P010", "扫地机器人", "智能家居", 2399.0),
    ("P011", "智能灯泡", "智能家居", 79.0),
    ("P012", "空气净化器", "智能家居", 1599.0),
    ("P013", "电动牙刷", "个护健康", 349.0),
    ("P014", "筋膜枪", "个护健康", 599.0),
    ("P015", "颈椎按摩仪", "个护健康", 429.0),
    ("P016", "保温杯", "生活百货", 129.0),
    ("P017", "桌面收纳盒", "生活百货", 69.0),
    ("P018", "护眼台灯", "生活百货", 219.0),
    ("P019", "USB-C 数据线", "数码配件", 29.0),
    ("P020", "游戏手柄", "数码配件", 329.0),
]

PRODUCT_INFO = {pid: (name, cat, price) for pid, name, cat, price in PRODUCTS}

REGIONS = ["华东", "华南", "华北", "华中", "西南", "西北", "东北"]
# 区域权重：华东/华南是主力市场
REGION_WEIGHTS = [30, 24, 16, 12, 9, 5, 4]
CHANNELS = ["天猫", "京东", "抖音", "官网"]
CHANNEL_WEIGHTS = [38, 30, 22, 10]

START = date(2026, 5, 1)
DAYS = 184
END = START + timedelta(days=DAYS - 1)


def month_factor(d: date) -> float:
    """按月给出季节系数，制造真实的趋势变化。

    6 月大促冲高，8 月起整体回落（供「分析销售额下降原因」使用），
    10 月国庆回升。
    """
    m = d.month
    return {
        5: 1.00,
        6: 1.35,   # 618 大促
        7: 0.92,
        8: 0.85,
        9: 0.78,
        10: 1.05,
    }.get(m, 1.0)


def region_factor(region: str, d: date) -> float:
    """西南区域 8 月起明显下滑，用于演示「区域维度下的异常识别」。"""
    if region == "西南" and d >= date(2026, 8, 1):
        return 0.45
    if region == "抖音" and d >= date(2026, 9, 1):
        return 1.2
    return 1.0


def channel_factor(channel: str, d: date) -> float:
    """抖音渠道 9 月起发力。"""
    if channel == "抖音" and d >= date(2026, 9, 1):
        return 1.25
    if channel == "官网" and d >= date(2026, 8, 1):
        return 0.8
    return 1.0


def build_rows() -> list[dict]:
    rows: list[dict] = []
    order_no = 0
    for offset in range(DAYS):
        d = START + timedelta(days=offset)
        base = 10 * month_factor(d)
        n_orders = max(3, int(random.gauss(base, base * 0.25)))
        for _ in range(n_orders):
            order_no += 1
            pid = random.choices(
                [p[0] for p in PRODUCTS],
                weights=[8, 7, 9, 5, 7, 4, 6, 5, 6, 3, 4, 3, 5, 3, 4, 5, 4, 4, 9, 4],
            )[0]
            name, cat, price = PRODUCT_INFO[pid]
            region = random.choices(REGIONS, weights=REGION_WEIGHTS)[0]
            channel = random.choices(CHANNELS, weights=CHANNEL_WEIGHTS)[0]
            qty = random.choices([1, 2, 3, 4, 5, 6], weights=[40, 25, 14, 10, 7, 4])[0]
            # 渠道折扣
            discount = {"天猫": 0.95, "京东": 0.97, "抖音": 0.90, "官网": 1.0}[channel]
            unit_price = round(price * discount, 2)
            rows.append(
                {
                    "order_id": f"ORD{order_no:06d}",
                    "order_date": d.isoformat(),
                    "product": name,
                    "product_id": pid,
                    "category": cat,
                    "region": region,
                    "channel": channel,
                    "quantity": qty,
                    "unit_price": unit_price,
                    "amount": round(unit_price * qty, 2),
                    "customer_id": f"C{random.randint(1000, 4999):05d}",
                }
            )

    # 人为注入少量缺失/重复，便于演示数据质量探查
    for i in range(0, len(rows), 137):
        rows[i]["customer_id"] = ""
    rows.append(dict(rows[5]))  # 一条重复订单
    return rows


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    rows = build_rows()
    fields = list(rows[0].keys())
    with (DATA_DIR / "demo_sales.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    with (DATA_DIR / "demo_products.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["product_id", "product_name", "category", "cost", "launch_date", "supplier"])
        suppliers = ["星辰供应链", "云启贸易", "宏图电子", "远景实业"]
        for pid, name, cat, price in PRODUCTS:
            writer.writerow(
                [
                    pid,
                    name,
                    cat,
                    round(price * random.uniform(0.45, 0.65), 2),
                    (START - timedelta(days=random.randint(30, 900))).isoformat(),
                    random.choice(suppliers),
                ]
            )

    print(f"orders={len(rows)} -> data/demo_sales.csv")
    print(f"products=20 -> data/demo_products.csv")


if __name__ == "__main__":
    main()
