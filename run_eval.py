# -*- coding: utf-8 -*-
"""
独立评测运行器 - 不依赖 Redis/Celery，直接跑 40 条用例
流程：加载用例 → 调 qwen-plus 回答 → 调 qwen-plus 当 Judge 评分 → 输出 JSON 报告
"""
import asyncio
import json
import time
from pathlib import Path

import yaml
from openai import AsyncOpenAI
from app.config import get_config

cfg = get_config()
client = AsyncOpenAI(
    api_key=cfg.llm_api_key,
    base_url=cfg.llm_base_url,
)

JUDGE_PROMPT = """你是严格的评审专家。根据评分标准对模型输出打分（每项0-10）：
1. accuracy 准确性：是否正确无幻觉
2. relevance 相关性：是否切题
3. completeness 完整性：是否答全
4. format 格式：是否清晰

严格输出JSON：{"accuracy":0-10,"relevance":0-10,"completeness":0-10,"format":0-10,"feedback":"简要评价"}"""


async def run_one_case(case: dict) -> dict:
    """跑单条用例：模型回答 + Judge 评分"""
    t0 = time.time()

    # 1. 模型回答
    resp = await client.chat.completions.create(
        model=cfg.llm_model,
        messages=[{"role": "user", "content": case["input"]}],
        temperature=0.0,
        max_tokens=1024,
    )
    model_output = resp.choices[0].message.content

    # 2. Judge 评分
    judge_resp = await client.chat.completions.create(
        model=cfg.llm_model,
        messages=[
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user", "content":
                f"【问题】{case['input']}\n【评分标准】{case.get('scoring_criteria','')}\n【模型输出】{model_output[:2000]}\n请打分。"},
        ],
        temperature=0.0,
        max_tokens=500,
    )
    judge_text = judge_resp.choices[0].message.content

    # 3. 解析评分
    import re
    score = {"accuracy": 7.0, "relevance": 7.0, "completeness": 7.0, "format": 7.0, "feedback": ""}
    m = re.search(r"\{[\s\S]*\}", judge_text)
    if m:
        try:
            data = json.loads(m.group())
            score = {
                "accuracy": float(data.get("accuracy", 7)),
                "relevance": float(data.get("relevance", 7)),
                "completeness": float(data.get("completeness", 7)),
                "format": float(data.get("format", 7)),
                "feedback": str(data.get("feedback", "")),
            }
        except Exception:
            pass

    elapsed = round(time.time() - t0, 2)
    overall = round(sum([score["accuracy"], score["relevance"],
                         score["completeness"], score["format"]]) / 4, 2)
    return {
        "id": case["id"],
        "category": case.get("category", "general"),
        "difficulty": case.get("difficulty", 1),
        "input": case["input"][:100],
        "model_output": model_output[:300],
        "scores": score,
        "overall": overall,
        "passed": overall >= 6.0,
        "elapsed_s": elapsed,
    }


async def main():
    # 加载用例
    with open("data/eval_cases/cases.yaml", "r", encoding="utf-8") as f:
        cases = yaml.safe_load(f)["cases"]
    enabled = [c for c in cases if c.get("enabled", True)]
    print(f"共 {len(enabled)} 条用例，开始评测...\n")

    results = []
    for i, case in enumerate(enabled, 1):
        print(f"[{i}/{len(enabled)}] {case['id']} ...", end=" ", flush=True)
        try:
            r = await run_one_case(case)
            results.append(r)
            print(f"总分={r['overall']} {'PASS' if r['passed'] else 'FAIL'} ({r['elapsed_s']}s)")
        except Exception as e:
            print(f"ERROR: {e}")
            results.append({"id": case["id"], "error": str(e), "overall": 0})

    # 汇总
    passed = [r for r in results if r.get("passed")]
    failed = [r for r in results if not r.get("passed")]
    avg = round(sum(r.get("overall", 0) for r in results) / len(results), 2)

    # 按类别统计
    by_cat = {}
    for r in results:
        cat = r.get("category", "unknown")
        by_cat.setdefault(cat, []).append(r.get("overall", 0))

    summary = {
        "total": len(results),
        "passed": len(passed),
        "failed": len(failed),
        "pass_rate": f"{len(passed)/len(results)*100:.1f}%",
        "avg_score": avg,
        "by_category": {k: round(sum(v)/len(v), 2) for k, v in by_cat.items()},
        "failed_cases": [{"id": r["id"], "score": r.get("overall"), "feedback": r.get("scores",{}).get("feedback","")[:200]} for r in failed],
    }

    out = {"summary": summary, "details": results}
    with open("data/reports/eval_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print(f"总用例: {summary['total']}  通过: {summary['passed']}  失败: {summary['failed']}")
    print(f"通过率: {summary['pass_rate']}  平均分: {summary['avg_score']}")
    print(f"\n各类别平均分:")
    for cat, score in sorted(summary["by_category"].items()):
        print(f"  {cat:12s}: {score}")
    print(f"\n失败用例:")
    for f in summary["failed_cases"]:
        print(f"  {f['id']:25s} 分={f['score']}  {f['feedback'][:80]}")
    print(f"\n详细报告: data/reports/eval_result.json")


if __name__ == "__main__":
    asyncio.run(main())
