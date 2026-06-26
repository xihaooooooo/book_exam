"""画像预测有效性离线评估。

不改业务逻辑，只读 attempts，用 BKT 做 step-ahead 预测并和真实结果对比，
回答"这个画像到底有没有预测价值"。

用法：
    python -m exam.student_profile.evaluate_profile --db cache/attempts.db --student default
    python -m exam.student_profile.evaluate_profile --db cache/attempts.db --all-students --out output/profile_eval.json
"""

import argparse
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field

_project_root = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from exam.student_profile.profile_engine import normalize_section_id
from exam.student_profile.schemas import BKTParams


# ── BKT prediction ──


@dataclass
class Prediction:
    """Single step-ahead prediction record."""
    student_id: str
    section_id: str
    topic: str
    p_predicted: float     # P(correct) before seeing answer
    p_L_before: float      # P(L) before learning transition
    is_correct: int        # actual outcome


@dataclass
class EvalResult:
    """Aggregated evaluation metrics."""
    student_id: str = ""
    n_predictions: int = 0
    n_topics: int = 0
    accuracy: float = 0.0       # threshold at 0.5
    logloss: float = 0.0
    brier: float = 0.0
    auc: float = 0.0
    buckets: list[dict] = field(default_factory=list)
    baseline_global: dict = field(default_factory=dict)
    baseline_section: dict = field(default_factory=dict)


def bkt_predict(params: BKTParams, p_L: float) -> float:
    """BKT step-ahead prediction: P(correct | current P(L)).

    P_before = P(L) + (1 - P(L)) * P(T)
    P(correct) = P_before * (1 - P(S)) + (1 - P_before) * P(G)
    """
    p_before = p_L + (1.0 - p_L) * params.p_T
    p_correct = p_before * (1.0 - params.p_S) + (1.0 - p_before) * params.p_G
    return p_correct


def bkt_update(params: BKTParams, p_L: float, is_correct: bool) -> float:
    """Bayesian update after observing an outcome."""
    # Learning transition first
    p_L = p_L + (1.0 - p_L) * params.p_T

    if is_correct:
        p_correct_given_known = 1.0 - params.p_S
        p_correct_given_unknown = params.p_G
        p_obs = p_L * p_correct_given_known + (1.0 - p_L) * p_correct_given_unknown
        if p_obs > 0:
            p_L = p_L * p_correct_given_known / p_obs
    else:
        p_wrong_given_known = params.p_S
        p_wrong_given_unknown = 1.0 - params.p_G
        p_obs = p_L * p_wrong_given_known + (1.0 - p_L) * p_wrong_given_unknown
        if p_obs > 0:
            p_L = p_L * p_wrong_given_known / p_obs

    return max(0.001, min(0.999, p_L))


def _group_key(row) -> tuple:
    """Group attempts by (student_id, section_id, topic)."""
    sid = normalize_section_id(row["section_id"] or "")
    topic = (row["topic"] or "").strip()
    return (row["student_id"], sid, topic)


def generate_predictions(
    db_path: str,
    student_id: str = "",
) -> list[Prediction]:
    """Walk through attempts chronologically per topic group, generating step-ahead predictions."""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    where = ""
    params_sql: tuple = ()
    if student_id:
        where = "WHERE student_id = ?"
        params_sql = (student_id,)

    sql = f"SELECT * FROM attempts {where} ORDER BY student_id, section_id, topic, created_at, id"
    rows = (db.execute(sql, params_sql) if params_sql else db.execute(sql)).fetchall()
    db.close()

    # Group by (student, section_id, topic)
    groups: dict[tuple, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        groups[_group_key(r)].append(r)

    bkt_params = BKTParams()
    predictions: list[Prediction] = []

    for key, group in groups.items():
        stu, sid, topic = key
        if len(group) < 2:
            continue  # need at least 2 attempts to predict

        p_L = bkt_params.p_L0

        for i, row in enumerate(group):
            if i == 0:
                # First attempt: update BKT but don't predict (no history)
                p_L = bkt_update(bkt_params, p_L, bool(row["is_correct"]))
                continue

            # Predict before seeing this attempt
            p_correct = bkt_predict(bkt_params, p_L)

            predictions.append(Prediction(
                student_id=stu,
                section_id=sid,
                topic=topic,
                p_predicted=p_correct,
                p_L_before=p_L,
                is_correct=int(row["is_correct"]),
            ))

            # Update with actual outcome for next iteration
            p_L = bkt_update(bkt_params, p_L, bool(row["is_correct"]))

    return predictions


# ── Metrics ──


def _safe_log(x: float) -> float:
    return math.log(max(x, 1e-15))


def compute_metrics(predictions: list[Prediction]) -> EvalResult:
    """Compute all evaluation metrics."""
    n = len(predictions)
    if n == 0:
        return EvalResult(n_predictions=0)

    topics = set((p.student_id, p.section_id, p.topic) for p in predictions)

    # Accuracy at threshold 0.5
    correct_at_05 = sum(
        1 for p in predictions
        if (p.p_predicted >= 0.5) == bool(p.is_correct)
    )
    accuracy = correct_at_05 / n

    # LogLoss
    logloss = 0.0
    for p in predictions:
        prob = max(min(p.p_predicted, 1.0 - 1e-15), 1e-15)
        logloss -= (p.is_correct * _safe_log(prob) +
                    (1 - p.is_correct) * _safe_log(1.0 - prob))
    logloss /= n

    # Brier score
    brier = sum((p.p_predicted - p.is_correct) ** 2 for p in predictions) / n

    # AUC (manual trapezoidal)
    auc = _compute_auc(predictions)

    # Bucket analysis
    buckets = _bucket_analysis(predictions)

    # Baselines
    global_acc = sum(p.is_correct for p in predictions) / n

    # Section-level baseline
    section_acc: dict[str, list[int]] = defaultdict(list)
    for p in predictions:
        section_acc[p.section_id].append(p.is_correct)
    section_baseline_acc = 0.0
    for p in predictions:
        section_baseline_acc += sum(section_acc[p.section_id]) / len(section_acc[p.section_id])
    section_baseline_acc /= n

    # Baseline metrics
    baseline_global = {
        "accuracy": global_acc,
        "logloss": - (global_acc * _safe_log(global_acc) +
                      (1 - global_acc) * _safe_log(1.0 - global_acc)),
        "brier": sum((global_acc - p.is_correct) ** 2 for p in predictions) / n,
    }
    baseline_section = {
        "accuracy": section_baseline_acc,
        "brier": sum(
            ((sum(section_acc[p.section_id]) / len(section_acc[p.section_id])) - p.is_correct) ** 2
            for p in predictions
        ) / n,
    }

    return EvalResult(
        student_id=predictions[0].student_id if predictions else "",
        n_predictions=n,
        n_topics=len(topics),
        accuracy=accuracy,
        logloss=logloss,
        brier=brier,
        auc=auc,
        buckets=buckets,
        baseline_global=baseline_global,
        baseline_section=baseline_section,
    )


def _compute_auc(predictions: list[Prediction]) -> float:
    """Manual AUC via trapezoidal rule (no sklearn dependency)."""
    pairs = sorted(
        [(p.p_predicted, p.is_correct) for p in predictions],
        key=lambda x: -x[0],
    )

    # Count total positives and negatives
    n_pos = sum(1 for _, y in pairs if y == 1)
    n_neg = len(pairs) - n_pos

    if n_pos == 0 or n_neg == 0:
        return 0.5  # degenerate

    tpr, fpr = 0.0, 0.0
    auc = 0.0
    prev_fpr = 0.0
    prev_tpr = 0.0

    tp, fp = 0, 0
    i = 0
    while i < len(pairs):
        score = pairs[i][0]
        # Process all samples at this threshold
        while i < len(pairs) and pairs[i][0] == score:
            if pairs[i][1] == 1:
                tp += 1
            else:
                fp += 1
            i += 1

        tpr = tp / n_pos
        fpr = fp / n_neg
        # Trapezoidal area
        auc += (fpr - prev_fpr) * (tpr + prev_tpr) / 2.0
        prev_fpr, prev_tpr = fpr, tpr

    # Complete the curve to (1, 1)
    auc += (1.0 - prev_fpr) * (1.0 + prev_tpr) / 2.0
    return auc


def _bucket_analysis(predictions: list[Prediction]) -> list[dict]:
    """Split predictions by P(L) bucket and compute actual accuracy."""
    buckets_def = [
        (0.0, 0.3, "P(L) < 0.30"),
        (0.3, 0.5, "0.30 ≤ P(L) < 0.50"),
        (0.5, 0.7, "0.50 ≤ P(L) < 0.70"),
        (0.7, 0.85, "0.70 ≤ P(L) < 0.85"),
        (0.85, 1.0, "P(L) ≥ 0.85"),
    ]
    result = []
    for lo, hi, label in buckets_def:
        bucket_preds = [p for p in predictions if lo <= p.p_L_before < hi]
        n = len(bucket_preds)
        if n == 0:
            result.append({"bucket": label, "n": 0, "predicted_mean": None, "actual_accuracy": None})
            continue
        actual = sum(p.is_correct for p in bucket_preds) / n
        pred_mean = sum(p.p_predicted for p in bucket_preds) / n
        result.append({
            "bucket": label,
            "n": n,
            "predicted_mean": round(pred_mean, 4),
            "actual_accuracy": round(actual, 4),
        })
    return result


# ── Report ──


def print_report(result: EvalResult, student: str = ""):
    """Print human-readable evaluation report."""
    label = f" (student={student})" if student else ""
    print()
    print("=" * 60)
    print(f"  BKT 画像评估报告{label}")
    print("=" * 60)
    print(f"  预测样本数:     {result.n_predictions}")
    print(f"  知识点分组数:   {result.n_topics}")
    print()

    if result.n_predictions < 50:
        print("  [!] 样本数 < 50，以下指标仅供参考，不下结论。")
        print()

    print(f"  {'':20s} {'BKT':>10s} {'Global':>10s} {'Section':>10s}")
    print(f"  {'-'*50}")
    print(f"  {'Accuracy':20s} {result.accuracy:>10.4f} {result.baseline_global['accuracy']:>10.4f} {result.baseline_section['accuracy']:>10.4f}")
    print(f"  {'LogLoss':20s} {result.logloss:>10.4f} {result.baseline_global['logloss']:>10.4f} {'':>10s}")
    print(f"  {'Brier':20s} {result.brier:>10.4f} {result.baseline_global['brier']:>10.4f} {result.baseline_section['brier']:>10.4f}")
    print(f"  {'AUC':20s} {result.auc:>10.4f}")
    print()

    # Judgment
    bkt_better_brier = result.brier < result.baseline_global["brier"]
    bkt_better_logloss = result.logloss < result.baseline_global["logloss"]
    print(f"  Brier 优于 Global baseline: {'[OK]' if bkt_better_brier else '[FAIL]'}")
    print(f"  LogLoss 优于 Global baseline: {'[OK]' if bkt_better_logloss else '[FAIL]'}")
    if result.auc > 0.65:
        print(f"  AUC={result.auc:.4f} > 0.65: 有一定预测价值 [OK]")
    elif result.auc > 0.5:
        print(f"  AUC={result.auc:.4f}: 略优于随机，预测信号较弱 [!]")
    else:
        print(f"  AUC={result.auc:.4f} ≤ 0.5: 无预测价值 [FAIL]")
    print()

    # Buckets
    print(f"  {'P(L) 区间':<25s} {'样本':>6s} {'预测均值':>8s} {'实际正确率':>8s} {'校准':>6s}")
    print(f"  {'-'*55}")
    for b in result.buckets:
        n_str = str(b["n"])
        pred_str = f"{b['predicted_mean']:.4f}" if b["predicted_mean"] is not None else "-"
        actual_str = f"{b['actual_accuracy']:.4f}" if b["actual_accuracy"] is not None else "-"
        if b["predicted_mean"] is not None and b["actual_accuracy"] is not None:
            cal = "[OK]" if abs(b["predicted_mean"] - b["actual_accuracy"]) < 0.1 else "[!]"
        else:
            cal = "-"
        print(f"  {b['bucket']:<25s} {n_str:>6s} {pred_str:>8s} {actual_str:>8s} {cal:>6s}")
    print()


# ── Main ──


def evaluate(
    db_path: str,
    student_id: str = "",
) -> dict:
    """Run full evaluation, return dict suitable for JSON output."""
    predictions = generate_predictions(db_path, student_id=student_id)
    result = compute_metrics(predictions)

    if student_id:
        print_report(result, student=student_id)

    return {
        "student_id": student_id or "all",
        "n_predictions": result.n_predictions,
        "n_topics": result.n_topics,
        "accuracy": round(result.accuracy, 4),
        "logloss": round(result.logloss, 4),
        "brier": round(result.brier, 4),
        "auc": round(result.auc, 4),
        "buckets": result.buckets,
        "baseline_global": {k: round(v, 4) for k, v in result.baseline_global.items()},
        "baseline_section": {k: round(v, 4) for k, v in result.baseline_section.items()},
    }


def main():
    parser = argparse.ArgumentParser(
        description="BKT 画像预测有效性离线评估"
    )
    parser.add_argument("--db", default="cache/attempts.db",
                        help="attempts 数据库路径")
    parser.add_argument("--student", default="",
                        help="评估指定学生（默认评估所有数据）")
    parser.add_argument("--all-students", action="store_true",
                        help="按学生分别评估")
    parser.add_argument("--out", default="",
                        help="输出 JSON 文件路径")
    args = parser.parse_args()

    db_path = args.db if os.path.isabs(args.db) else os.path.join(_project_root, args.db)
    if not os.path.exists(db_path):
        print(f"Error: 数据库 {db_path} 不存在")
        sys.exit(1)

    results = []

    if args.all_students:
        db = sqlite3.connect(db_path)
        students = [
            r[0] for r in
            db.execute("SELECT DISTINCT student_id FROM attempts").fetchall()
        ]
        db.close()
        for stu in students:
            r = evaluate(db_path, student_id=stu)
            results.append(r)

        # Aggregate
        if results:
            print(f"\n{'='*60}")
            print(f"  全部 {len(results)} 个学生汇总")
            print(f"{'='*60}")
            for k in ["accuracy", "logloss", "brier", "auc"]:
                vals = [r[k] for r in results]
                print(f"  {k}: mean={sum(vals)/len(vals):.4f}  min={min(vals):.4f}  max={max(vals):.4f}")
    else:
        r = evaluate(db_path, student_id=args.student)
        results.append(r)

    if args.out:
        out_path = args.out if os.path.isabs(args.out) else os.path.join(_project_root, args.out)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n结果已写入: {out_path}")


if __name__ == "__main__":
    main()
