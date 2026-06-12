"""评分器离线评测：用标注样本验证 Reviewer 评分与人工标注的一致性，并扫描最优阈值

用法（需要真实 DEEPSEEK_API_KEY，约消耗 8 次 LLM 调用）：
    python -m evals.run_eval

数据集格式（evals/dataset.jsonl，每行一条）：
    {"topic": "主题", "label": "pass" 或 "fail", "material": "调研素材原文"}
"""
import asyncio
import json
import pathlib
import sys

from dotenv import load_dotenv

load_dotenv()

from agents.reviewer import _SCORE_THRESHOLD, evaluate_material  # noqa: E402

_DATASET = pathlib.Path(__file__).parent / "dataset.jsonl"


async def main():
    samples = [json.loads(line) for line in _DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"评测样本：{len(samples)} 条（pass={sum(s['label'] == 'pass' for s in samples)}，"
          f"fail={sum(s['label'] == 'fail' for s in samples)}）\n")

    results = []
    for i, sample in enumerate(samples, 1):
        score, _ = await evaluate_material(sample["topic"], sample["material"])
        results.append((sample["label"], score))
        print(f"[{i}/{len(samples)}] label={sample['label']:<4}  score={score}  topic={sample['topic']}")

    parsed = [(label, score) for label, score in results if score is not None]
    if len(parsed) < len(results):
        print(f"\n⚠️ {len(results) - len(parsed)} 条样本评分解析失败，已排除")
    if not parsed:
        print("没有可用结果")
        sys.exit(1)

    print(f"\n{'阈值':<6}{'准确率':<8}说明")
    for threshold in range(5, 10):
        correct = sum((score >= threshold) == (label == "pass") for label, score in parsed)
        acc = correct / len(parsed)
        marker = "  ← 当前线上阈值" if threshold == _SCORE_THRESHOLD else ""
        print(f"{threshold:<8}{acc:<10.0%}{marker}")

    print("\n若某个阈值的准确率显著高于当前线上阈值，修改 agents/reviewer.py 中的 _SCORE_THRESHOLD。")


if __name__ == "__main__":
    asyncio.run(main())
