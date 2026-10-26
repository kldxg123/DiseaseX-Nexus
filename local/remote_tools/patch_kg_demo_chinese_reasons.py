from __future__ import annotations

from pathlib import Path


ROOT = Path("USER_HOME/projects/openclaw")
JS = ROOT / "src/epidemic_core/dashboard/static/forecast-model-kg.js"


def main() -> int:
    text = JS.read_text(encoding="utf-8")
    old = """    let therefore;
    if (track === 'FORMAL') {
      therefore = `所以目前应采用 ${esc(primary.label_zh || primary.label_en || primary.model_id || '候选模型')} 与 ${esc(baselines.join('、') || '基线模型')} 做 rolling-origin 回测比较，只有 champion guard 通过后才发布 ${esc(target)} 预测。`;
    } else if (track === 'DEMO') {
      therefore = `所以目前只适合演示级预测：优先用 ${esc(primary.label_zh || primary.label_en || primary.model_id || '简单基线/轻量模型')}，必须标注 NOT_PRODUCTION_VALIDATED，不能当正式预测。`;
    } else if (track === 'SPARSE_OUTBREAK') {
      therefore = `所以目前不应伪装成连续周度预测，应采用事件级/场景级风险说明、最近观测或人工复核摘要，而不是补零训练模型。`;
    } else {
      therefore = `所以目前不应启动正式预测；应先补齐可信数据、覆盖率、最近性和地区数，再进入回测选型。`;
    }
    return `${because}；${therefore}`;
"""
    new = """    const primaryName = esc(primary.label_zh || primary.label_en || primary.model_id || '候选模型');
    const baselineNames = esc(baselines.join('、') || '基线模型');
    const reasonItems = (data.reasons || []).slice(0, 3).map(x => `规则原因：${esc(x)}`);
    if (warnings.length) reasonItems.push(`限制条件：${esc(warnings[0])}`);
    if (!reasonItems.length && track === 'FORMAL') reasonItems.push('数据长度、地区数量和质量门禁满足正式回测候选条件。');
    if (!reasonItems.length && track === 'DEMO') reasonItems.push('数据已有可用历史，但未达到正式生产级门槛。');
    let therefore;
    if (track === 'FORMAL') {
      therefore = `推荐模型：优先把 ${primaryName} 作为主候选，同时必须与 ${baselineNames} 做对照。推荐原因：${reasonItems.join('；')}。验证要求：先做 rolling-origin 回测，比较 MAE/WAPE/分 horizon 指标和预测区间覆盖率；只有 champion guard 优于基线且无数据泄漏时，才发布 ${esc(target)} 预测。`;
    } else if (track === 'DEMO') {
      therefore = `推荐模型：使用 ${primaryName} 做演示级预测，并与 ${baselineNames} 对照。推荐原因：${reasonItems.join('；')}。限制：当前只能标注 NOT_PRODUCTION_VALIDATED，不能作为正式生产预测。`;
    } else if (track === 'SPARSE_OUTBREAK') {
      therefore = `推荐方案：不推荐连续周度预测模型。推荐原因：${reasonItems.join('；') || '该病种/数据形态属于稀疏事件，未报告不等于零病例。'}。更合适的做法是事件级摘要、场景风险说明或人工复核，而不是补零训练模型。`;
    } else {
      therefore = `推荐方案：暂不启动正式预测。原因：${reasonItems.join('；') || '当前画像缺少足够历史、覆盖率、地区数或质量门禁。'}。下一步应先补齐可信数据，再进入回测选型。`;
    }
    return `${because}。${therefore}`;
"""
    if old not in text:
        raise RuntimeError("direct answer block not found")
    text = text.replace(old, new)
    JS.write_text(text, encoding="utf-8")
    print("patch=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
