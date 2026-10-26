"""Verify frozen statistics and package a minimal, credential-free evidence set."""
from pathlib import Path
import csv, difflib, hashlib, io, json, sys, unittest
from zipfile import ZipFile, ZIP_DEFLATED
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_nested_validation_20261023 as r
import test_nested_validation_20261023 as tests

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    out=r.OUT;root=r.ROOT
    frozen={p.name:digest(p) for p in out.glob('*') if p.name in ('protocol.json','predictions.csv','fold_manifest.json')}
    cis=json.loads((out/'paired_confidence_intervals.json').read_text(encoding='utf-8'))
    pred=r.read_csv(out/'predictions.csv');old=r.read_csv(r.OLD/'predictions.csv');adaptive=r.read_csv(r.OLD/'adaptive_predictions.csv')
    for ci in cis:
        disease=ci['disease'];model=ci['comparison'].split(' minus ')[1]
        pool=pred if ci['experiment']=='nested' else old
        a=[x for x in (pred if ci['experiment']=='nested' else adaptive) if x['disease']==disease and (ci['experiment']!='nested' or x['model']=='nested_selected')]
        b=[x for x in pool if x['disease']==disease and x['model']==model]
        check=r.bootstrap(a,b,ci['block_origins'],ci['draws'])
        assert all(check[k]==ci[k] for k in check),'frozen statistics changed'
    stream=io.StringIO();suite=unittest.defaultTestLoader.loadTestsFromModule(tests)
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    if not result.wasSuccessful():raise RuntimeError(stream.getvalue())
    (out/'tests.txt').write_text(stream.getvalue(),encoding='utf-8')
    # Broader dataset-only metadata audit: no credentials, databases or model registries read.
    files=[]
    for directory in ('data','kg_paper_benchmark_work/data','sci_q1_benchmark_work'):
        base=root/directory
        if not base.exists():continue
        for p in sorted(base.rglob('*')):
            if p.is_file() and p.suffix.lower() in ('.csv','.parquet') and not any(s in p.as_posix().lower() for s in ('secret','credential','token','password')):
                rel=p.relative_to(root).as_posix()
                files.append({'path':rel,'bytes':p.stat().st_size,'sha256':digest(p),
                    'eligibility':'template_or_review_material_not_independent_labels' if 'template' in rel or 'queue' in rel or 'dictionary' in rel else 'development exposure not excluded; no evidence of untouched holdout'})
    r.save('extended_material_audit.json',{'method':'dataset-file metadata and earlier benchmark provenance audit; no remote access, no model registry or credential contents read',
        'files':files,'decision':'No verifiably untouched historical dataset established in examined local materials; templates, covariates and prior development inputs are not independent outcome labels.',
        'limit':'Not an exhaustive claim about all external public datasets or undocumented human exposure. Uncertain exposure is not treated as blindness.'})
    # This is a delivered-snapshot diff, not an invented Git commit history.
    baseline=root/'output/proposal_submission_20261023/三病种实验_代码数据与复核材料_20261023.zip'
    paths=['packages/predictor_agent/chronological_validation.py','tools/run_three_disease_20261023.py','tools/finish_three_disease_20261023.py',
        'packages/predictor_agent/nested_validation.py','tools/run_nested_validation_20261023.py','tools/test_nested_validation_20261023.py','tools/complete_nested_evidence_20261023.py']
    changes=[];patch=[]
    with ZipFile(baseline) as z:
        for rel in paths:
            before=z.read(rel) if rel in z.namelist() else b'';after=(root/rel).read_bytes()
            changes.append({'path':rel,'status':'unchanged' if before==after else 'modified' if before else 'added_to_delivered_snapshot',
                'baseline_sha256':hashlib.sha256(before).hexdigest() if before else None,'current_sha256':digest(root/rel)})
            patch.extend(difflib.unified_diff(before.decode('utf-8').splitlines(True),after.decode('utf-8').splitlines(True),fromfile='before/'+rel if before else '/dev/null',tofile='after/'+rel))
    r.save('code_change_manifest.json',{'baseline_archive':baseline.name,'baseline_archive_sha256':digest(baseline),'files':changes,
        'scope':'exact changes relative to last delivered snapshot; no inference of lifetime authorship from file differences'})
    (out/'changes.patch').write_text(''.join(patch),encoding='utf-8')
    assert all(digest(out/name)==sha for name,sha in frozen.items())
    r.save('verification_extended.json',{'unit_tests_run':result.testsRun,'unit_tests_passed':result.wasSuccessful(),'paired_ci_records_recomputed':len(cis),
       'frozen_files_unchanged':frozen,'original_outputs_hashes':{name:digest(r.OLD/name) for name in ('predictions.csv','adaptive_predictions.csv','metrics.json','adaptive_metrics.json')},
       'statistics_repair':'Resumed statistics from frozen predictions after CSV numeric conversion error; no forecasts, protocol or selection rules changed. Added stricter pairing input validation.'})
    names={'Dengue Fever':'登革热','Campylobacterenterosis':'弯曲杆菌肠炎','Mumps':'腮腺炎'}
    metrics=json.loads((out/'metrics.json').read_text(encoding='utf-8'))
    lines=['三病种差值置信区间、代码差分与嵌套滚动回测补充报告（2026-10-23）','',
      '一、个人贡献与审查结论',
      '按用户声明记录：安浩然确认项目代码由本人完成。此项为作者声明，不是第三方核验或签字。本轮补充代码由 AI 在作者指示下协助实现与核验；公共数据、软件库和既有算法仍保留原归属。',
      '审查了既有基准输入、历史实验输出，并扩展检查本地 data、基准数据目录中的数据文件元数据。未找到能够确认未参与开发的独立历史标签集。埃博拉相关处理数据已有开发流程痕迹；天气、人口等协变量不等于独立预测标签；待填模板不等于新增数据。暴露情况不明的数据不作为盲测。未连接服务器，不声称已穷尽外部公共资料。',
      '', '二、固定的验证流程',
      '沿用官方缓存的三病种周数据，外层测试按 2020、2021、2022 年分别滚动。每个外层测试块之前依次设置不重叠的 52 周选择窗和 52 周校准窗。候选固定为末值、四周均值、52 周季节朴素和 log-AR(2)。内层按统一 1—4 周目标的绝对误差选择模型，每年固定一次；外层逐周扩展拟合，只使用预测起点之前的观测，目标不得跨年度测试块。此前测试年度的观测在后续年度成为已知历史，这是滚动评估而非一次性隔离测试。',
      '共 9 个病种—年度外层折，每病种 157 个预测起点、610 个有效起点—步长配对。旧实验每病种 622 个配对，因年度边界处理不同，旧新指标不可直接当作同一测试集比较。',
      '本轮是严格时间隔离的嵌套历史回测，但这些历史年份此前已被查看，因此不能称为未见数据盲测，亦不能消除人工选择研究方案所产生的历史暴露偏差。原有对比实验保留不改。',
      '', '三、差值置信区间方法',
      '差值定义为“待评估方案减去比较模型”，WAPE 以百分点表示，负值为误差较低。每个预测起点的所有步长一起配对，以连续起点移动块重采样 5000 次；主块长 8 周，4、13 周作敏感性分析。每次重新计算整体 WAPE 比值，不平均每点百分比误差；同时报告 WIS 差值。置信区间为 2.5%—97.5% 百分位区间。该方法依赖序列依赖可由块近似的假设，对疫情结构突变仍可能不稳健；多组探索性比较未作多重检验校正。跨零不代表等效，未预设等效界值，不能据此证明“不相上下”。',
      '', '四、年度嵌套选择方案结果（非完整平台的端到端验证）']
    for m in metrics:
        if m['model']=='nested_selected' and m['horizon']=='all':lines.append(f"{names[m['disease']]}：WAPE={m['wape']*100:.2f}%，WIS={m['wis']:.4f}，95%名义预测区间覆盖率={m['coverage95']*100:.2f}%。")
    lines+=['覆盖率明显低于名义 95%，尤其是登革热，说明历史残差区间在分布变化下校准不足，不能写成可靠的 95% 实际覆盖保证。', '']
    for experiment,title in [('previous_adaptive','五、原滚动自适应方案配对差值（622 个配对）'),('nested','六、新年度嵌套选择方案配对差值（610 个配对）')]:
        lines.append(title)
        for c in cis:
            if c['experiment']==experiment and c['block_origins']==8:
                lo,hi=c['wape_ci95_pp'];lines.append(f"{names[c['disease']]} | {c['comparison']}：ΔWAPE={c['delta_wape_pp']:+.3f} 个百分点，95%区间[{lo:+.3f}, {hi:+.3f}]；ΔWIS={c['delta_wis']:+.4f}，95%区间[{c['wis_ci95'][0]:+.4f}, {c['wis_ci95'][1]:+.4f}]。")
        lines.append('')
    lines+=['七、可以支持的结论与不能支持的结论',
      '旧滚动自适应实验中，弯曲杆菌肠炎与腮腺炎相对 log-AR(2) 的 WAPE 差值区间低于零，但这仍是探索性结果。新年度嵌套方案相对四周均值在上述两个病种上更差，说明年度更新频率与较早选择窗可能不适合这些序列；这是解释假设，不是已验证机制。本轮未据此继续调参或替换不利结果。登革热相对末值和 log-AR(2) 的差值区间均跨零，不足以证明优越或等效。',
      '新实验测试的是固定候选集的时间隔离选择与校准流程，不等于验证完整知识图谱或大语言模型架构，也不能据此宣称对未知病原的泛化已得到证明。后续若优化选择频率，应注册为新版本，并使用未参与开发的数据或继续独立外层评估。',
      '', '八、代码可追溯与复核',
      'changes.patch 和 code_change_manifest.json 给出相对上一交付压缩包的逐行差分及 SHA-256。3 个原实验代码文件未改，新增嵌套模块、运行器、测试与证据打包器。不伪造 Git 提交记录。protocol.json 在预测前写入，预测后仅修复 CSV 数值读取以恢复统计，原始预测和协议的哈希保持不变。',
      f'离线测试 {result.testsRun} 项通过；54 组块长—病种—比较的置信区间全部从冻结预测重算一致。tests.txt、verification_extended.json 可复核。',
      '运行环境：现有项目 Python 环境与 NumPy。测试命令：python tools/test_nested_validation_20261023.py。完整预测命令：python tools/run_nested_validation_20261023.py（仅在全新的输出目录中运行；已有协议拒绝覆盖）。统计和打包复核命令：python tools/complete_nested_evidence_20261023.py。交付为项目增量材料，合并至原项目副本使用；非完整软件依赖发行包。',
      '公共周数据来源与许可保留在三病种原始 provenance.json；本次仅附公开汇总病例缓存和必要代码，不包含数据库、密钥、服务器信息、私有记录或大模型权重。']
    report=root/'output/proposal_submission_20261023/三病种验证补充报告_20261023.txt'
    report.write_text('\n'.join(lines)+'\n',encoding='utf-8-sig')
    destination=report.parent/'差值置信区间_嵌套回测与代码差分_20261023.zip'
    include=[root/p for p in paths]+list(out.glob('*'))+[r.OLD/n for n in ('official_response.json','provenance.json','predictions.csv','adaptive_predictions.csv','metrics.json','adaptive_metrics.json','protocol.json','adaptive_protocol.json')]+[report]
    include=[p for p in include if p.is_file()]
    manifest={p.relative_to(root).as_posix():digest(p) for p in include}
    with ZipFile(destination,'w',ZIP_DEFLATED) as z:
        for p in include:z.write(p,p.relative_to(root).as_posix())
        z.writestr('SHA256SUMS.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps({'report':str(report),'archive':str(destination),'tests_passed':result.testsRun,'ci_verified':len(cis),'audited_dataset_files':len(files)},ensure_ascii=False))

if __name__=='__main__':main()
