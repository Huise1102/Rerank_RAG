"""按人工复核结果汇总评分，输出总分、分布与明细 CSV。

用法（在项目根目录执行）：
    backend\\.venv\\Scripts\\python.exe 测试语料\\问答测试_20261123\\scripts\\score_answers.py results_api_deepseek_100.jsonl

判分口径为三档：1.0 正确、0.5 部分正确、0.0 错误或拒答。
`OVERRIDES` 记录逐题复核结论，未列出的题按 1.0 计。换一批结果文件时需同步复核并更新此表。
"""
import csv
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TEST_DIR = SCRIPT_DIR.parent

# API 轮（results_api_deepseek_100.jsonl）的逐题扣分记录
OVERRIDES: dict[str, tuple[float, str]] = {
    "kb3-oral-011": (0.5, "只答三好学生 10%，遗漏优秀学生干部 5%/每班 1 名"),
    "kb4-oral-016": (0.0, "拒答：表 3 合并单元格缺失项目名，未能给出省级特等奖 5000 分"),
    "kb4-hop-001": (0.5, "只答就高不重复，缺 200 万奖励与 15000 分"),
    "kb4-hop-002": (0.5, "奖励 15.6 万正确，缺专利转化 1200 分计分"),
    "kb4-hop-007": (0.5, "只答 50%/50% 规则，缺 400 分与立项 200、结项 200"),
    "kb4-hop-009": (0.5, "只答 50% 折算，缺一级期刊 2000 分与 1000 分结果"),
}


def load_questions() -> dict[str, dict]:
    items: dict[str, dict] = {}
    for name in ("qa_kb3.jsonl", "qa_kb4.jsonl"):
        for line in (TEST_DIR / name).read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                items[item["id"]] = item
    return items


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("缺少结果文件名")
    results_name = sys.argv[1]
    stem = Path(results_name).stem
    if stem.startswith("results_"):
        stem = stem[len("results_") :]
    questions = load_questions()
    records = [
        json.loads(line)
        for line in (TEST_DIR / results_name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    rows = []
    for record in records:
        score, reason = OVERRIDES.get(record["id"], (1.0, "正确"))
        rows.append(
            {
                "id": record["id"],
                "kb_id": record["kb_id"],
                "category": record["category"],
                "score": score,
                "reason": reason,
                "question": record["question"],
                "answer": record.get("answer") or "",
                "reference": questions[record["id"]]["reference_answer"],
                "total_s": record.get("total_s"),
            }
        )

    total = sum(row["score"] for row in rows)
    summary = {
        "model": records[0].get("model", "") if records else "",
        "total_score": round(total, 2),
        "accuracy": round(total / len(rows) * 100, 1),
        "count": len(rows),
    }
    for key, name in (("kb_id", "kb"), ("category", "category")):
        groups: dict = {}
        for row in rows:
            label = f"KB{row[key]}" if key == "kb_id" else row[key]
            groups.setdefault(label, []).append(row["score"])
        summary[name] = {
            label: {
                "score": round(sum(scores), 2),
                "count": len(scores),
                "accuracy": round(sum(scores) / len(scores) * 100, 1),
            }
            for label, scores in sorted(groups.items())
        }
    summary["distribution"] = {
        "正确(1.0)": sum(1 for row in rows if row["score"] == 1.0),
        "部分正确(0.5)": sum(1 for row in rows if row["score"] == 0.5),
        "错误(0.0)": sum(1 for row in rows if row["score"] == 0.0),
    }
    summary["avg_total_s"] = round(
        sum(float(row["total_s"] or 0) for row in rows) / len(rows), 1
    )
    (TEST_DIR / f"scores_{stem}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with open(TEST_DIR / f"评分明细_{stem}.csv", "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["题号", "知识库", "题型", "得分", "判定说明", "问题", "参考答案", "模型答案", "耗时(s)"])
        for row in rows:
            writer.writerow(
                [
                    row["id"],
                    f"KB{row['kb_id']}",
                    row["category"],
                    row["score"],
                    row["reason"],
                    row["question"],
                    row["reference"],
                    row["answer"],
                    row["total_s"],
                ]
            )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
