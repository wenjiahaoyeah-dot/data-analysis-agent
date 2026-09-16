"""Create an unfilled human-review worksheet from saved evaluation artifacts.

No API calls and no claim that a human has annotated any case.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evaluation', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    source = json.loads(args.evaluation.read_text())
    out = ['# Spider 回答人工复核工作表', '',
           '> 未标注：此表由程序根据已有结果生成，不代表人工已完成审核，也不产生人工准确率。', '',
           '先对照问题、完整参考结果及原查询明细，独立判断最终回答是否正确、完整、有证据；必要时运行 SQL 或核查数据库。',
           '每题填写审核人、日期、三维标签（通过/不通过/无法判断）和具体理由。此表不展示自动评分；完成标注后再对照评分，减少先入为主。', '']
    for case in source['items']:
        if not case.get('eligible'): continue
        idx = case['index']; task = case.get('task') or {}
        out.extend([f'## 题目 {idx} · {case["db_id"]}', '', f'**问题：** {case["question"]}', '',
                    '**参考 SQL：**', '', '```sql', case['reference_sql'], '```', '',
                    '**完整参考结果：**', '', '```json', json.dumps(case['reference_rows'],ensure_ascii=False,indent=2,default=str), '```', '',
                    '**Agent 主要 SQL：**', '', '```sql', task.get('sql') or '(无)', '```', '',
                    '**Agent 最终回答：**', '', task.get('analysis') or '(无)', ''])
        out.extend(['', '**人工复核：** 审核人：________ 日期：________', '',
                    '- 正确性：□通过 □不通过 □无法判断',
                    '- 完整性：□通过 □不通过 □无法判断',
                    '- 证据支撑：□通过 □不通过 □无法判断',
                    '- 判断依据及与裁判分歧：____________________________', ''])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text('\n'.join(out),encoding='utf-8')
    print(f'已生成 {sum(1 for x in source["items"] if x.get("eligible"))} 道待人工复核题')

if __name__ == '__main__': main()
