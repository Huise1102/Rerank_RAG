import json
import re
from collections import Counter
from pathlib import Path

REQUIRED_FIELDS = {"id", "question", "category", "relevant_doc_ids"}
SENSITIVE_PATTERNS = {
    "身份证号": re.compile(r"\b\d{17}[\dXx]\b"),
    "手机号": re.compile(r"\b1[3-9]\d{9}\b"),
    "邮箱": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
}


def load_testset(path: str | Path) -> list[dict]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"测试集不存在：{target}")
    items: list[dict] = []
    with target.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            try:
                item = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"第 {line_number} 行 JSON 解析失败：{exc}") from exc
            item["_line"] = line_number
            items.append(item)
    return items


def validate_testset(items: list[dict]) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    categories: Counter[str] = Counter()
    for item in items:
        line = item.get("_line", "?")
        identifier = str(item.get("id") or "").strip()
        if not identifier:
            errors.append(f"第 {line} 行缺少 id")
        elif identifier in seen_ids:
            errors.append(f"第 {line} 行 id 重复：{identifier}")
        else:
            seen_ids.add(identifier)
        missing = REQUIRED_FIELDS - set(item)
        if missing:
            errors.append(f"第 {line} 行缺少字段：{', '.join(sorted(missing))}")
        question = str(item.get("question") or "")
        if len(question) < 4:
            errors.append(f"第 {line} 行问题过短")
        if not isinstance(item.get("relevant_doc_ids"), list) or not item.get("relevant_doc_ids"):
            errors.append(f"第 {line} 行 relevant_doc_ids 必须是非空数组")
        categories[str(item.get("category") or "未分类")] += 1
        for label, pattern in SENSITIVE_PATTERNS.items():
            if pattern.search(question) or pattern.search(str(item.get("reference_answer") or "")):
                warnings.append(f"第 {line} 行疑似包含{label}，请脱敏后再入库")
    if len(items) < 300:
        warnings.append(f"当前测试集 {len(items)} 条，低于阶段二要求的 300 条下线")
    return {
        "total": len(items),
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "categories": dict(categories),
    }


def sample_template(count: int = 1) -> list[dict]:
    return [
        {
            "id": f"sample-{index:03d}",
            "question": "请填写校园制度问题",
            "category": "学籍管理",
            "relevant_doc_ids": ["1"],
            "expected_keywords": ["关键词"],
            "reference_answer": "",
            "difficulty": "easy",
        }
        for index in range(1, count + 1)
    ]
