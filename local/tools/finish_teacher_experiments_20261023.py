"""Cross-check experimental receipts and emit a compact Chinese results report."""
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
from collections import defaultdict
import csv,difflib,hashlib,io,json,sys,time,unittest
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'));sys.path.insert(0,str(ROOT/'packages'))
from run_teacher_experiments_20261023 import OUT,save as frozen_save,sha,csvsave
from run_recommendation_ablation_20261023 import POLICIES,TARGETS,DATA,GRAPH
from predictor_agent.chronological_validation import wape
import test_teacher_experiments_20261023 as tests

def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))

def save(name,obj):
    path=OUT/name
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8'))==obj:return
        if name=='current_code_diff_manifest.json':
            path.rename(OUT/('previous_packaging_manifest_'+sha(path)[:12]+'.json'))
        else:raise RuntimeError('Frozen derived artifact differs: '+name)
    frozen_save(name,obj)

def main():
    stream=io.StringIO();result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(tests))
    (OUT/'new_unit_tests.txt').write_text(stream.getvalue(),encoding='utf-8')
    assert result.wasSuccessful()
    folds=read('recommendation_fold_results.json')
    with (OUT/'recommendation_predictions.csv').open(encoding='utf-8',newline='') as f:pred=list(csv.DictReader(f))
    grouped=defaultdict(list)
    for r in pred:
        assert str(r['target_week']).startswith(r['fold'])
        assert int(r['target'])==int(r['origin'])+int(r['horizon'])-1
        grouped[r['disease'],r['policy'],int(r['budget'])].append(r)
    metrics=[]
    for (disease,policy,budget),rows in grouped.items():
        keys=[(r['origin'],r['horizon'],r['target']) for r in rows]
        assert len(keys)==len(set(keys))==610
        detail=[r for r in folds if r['disease']==disease and r['policy']==policy and r['budget']==budget]
        # Top-k is a ranking diagnostic only, not meaningful for nonranking fixed selectors.
        ranking=policy not in ('fixed_rule','frozen_no_feedback')
        metrics.append(dict(disease=disease,policy=policy,budget=budget,n=len(rows),wape=wape([r['actual'] for r in rows],[r['point'] for r in rows]),
            wis=float(np.mean([float(r['wis']) for r in rows])),coverage95=float(np.mean([float(r['coverage95']) for r in rows])),
            top1_hit_rate=sum(r['top1_hit'] for r in detail)/len(detail) if ranking else None,
            top3_hit_rate=sum(r['top3_hit'] for r in detail)/len(detail) if ranking else None,
            selection_fits=sum(r['selection_fits'] for r in detail),total_fits=sum(r['total_fits'] for r in detail),
            measured_seconds=sum(sum(r[k] for k in ('ranking_seconds','selection_seconds','calibration_seconds','forecast_seconds')) for r in detail)))
    save('recommendation_aggregate_metrics.json',metrics)
    # Verify that manuscript model identity matches archived metrics, instead of assuming the route alias.
    source=next((ROOT/'reports/hk_flu_independent_short_horizon_20260825T112248Z').glob('*.zip'))
    hk=read('hk_canonical_horizon_confidence_intervals.json')
    with ZipFile(source) as z:
        name=next(n for n in z.namelist() if n.endswith('02_METRICS_BY_HORIZON.csv'))
        old=list(csv.DictReader(io.StringIO(z.read(name).decode('utf-8-sig'))))
        for r in hk:
            if r['block']!=8 or r['horizon']=='H1-H8':continue
            ref=next(x for x in old if int(x['horizon'])==r['horizon'] and x['model']=='Project')
            assert abs(float(ref['wape'])*100-r['project_wape_pct'])<1e-9
            assert abs(float(ref['rmse'])-r['project_rmse'])<1e-9
    save('hk_identity_audit.json',{'canonical_column':'recursive_hist_poisson','archived_horizon_metric_rows_verified':9,
        'alternative_column':'PROJECT_RECURSIVE_ROUTE','initial_route_reanalysis':'retained in hk_horizon_confidence_intervals.json; not used as the manuscript Poisson method',
        'decision':'canonical model chosen by exact archived metric identity, not by relative performance'})
    sensitivity=read('admission_sensitivity.json');sens_summary=[]
    for minimum in (8,16,26):
        for seasonal in (104,156):
            for threshold in (40.,50.,60.):
                for k in (1,3,5):
                    rows=[r for r in sensitivity if (r['minimum'],r['seasonal'],r['threshold'],r['k'])==(minimum,seasonal,threshold,k)]
                    sens_summary.append(dict(minimum=minimum,seasonal=seasonal,threshold=threshold,k=k,n_profiles=len(rows),
                        rejection_rate=sum(r['rejected'] for r in rows)/len(rows),completed=len([r for r in rows if r['wape'] is not None]),
                        median_wape_completed=float(np.median([r['wape'] for r in rows if r['wape'] is not None])),
                        actual_fits=sum(r['fit_calls'] for r in rows),forecast_seconds=sum(r['forecast_seconds'] for r in rows)))
    save('admission_sensitivity_summary.json',sens_summary)
    files=['packages/predictor_agent/execution_contract.py','packages/predictor_agent/evidence_recommender.py',
      'tools/run_teacher_experiments_20261023.py','tools/test_teacher_experiments_20261023.py','tools/run_recommendation_ablation_20261023.py','tools/finish_teacher_experiments_20261023.py',
      'packages/predictor_agent/chronological_validation.py','packages/predictor_agent/nested_validation.py','tools/run_nested_validation_20261023.py',
      'tests/test_api_access_policy.py','tests/test_access_policy.py']
    baseline=ROOT/'output/proposal_submission_20261023/差值置信区间_嵌套回测与代码差分_20261023.zip'
    changes=[];patch=[]
    with ZipFile(baseline) as z:
        for rel in files:
            before=z.read(rel) if rel in z.namelist() else b'';after=(ROOT/rel).read_bytes()
            baseline_origin='previous delivered archive'
            if rel in ('tests/test_api_access_policy.py','tests/test_access_policy.py'):
                restored=after.decode('utf-8')
                if rel.endswith('test_api_access_policy.py'):
                    restored=restored.replace('payload["policies_checked"] == 7','payload["policies_checked"] == 6').replace('len(payload["policies"]) == 7','len(payload["policies"]) == 6')
                else:
                    restored=restored.replace('        "cdc_fluview",\n','')
                    for line in ('    assert policies["cdc_fluview"].max_requests_per_run == 1\n','    assert policies["cdc_fluview"].robots_check_required is True\n','    assert decide_live_access(policies["cdc_fluview"], allow_network=False).allowed is False\n'):
                        restored=restored.replace(line,'')
                # apply_patch normalizes line endings; verify original captured hash under both encodings.
                expected=next(x['sha256'] for x in read('workflow_regression_protocol.json')['files'] if x['path']==rel)
                candidates=[restored.encode('utf-8'),restored.replace('\r\n','\n').replace('\n','\r\n').encode('utf-8')]
                before=next((v for v in candidates if hashlib.sha256(v).hexdigest()==expected),None)
                assert before is not None,'Unable to reconstruct exact pre-edit test fingerprint'
                baseline_origin='initial regression file fingerprint, exact pre-edit bytes reconstructed and checked'
            changes.append({'path':rel,'status':'unchanged' if before==after else 'modified' if before else 'absent_from_previous_delivery','baseline_origin':baseline_origin,'before_sha256':hashlib.sha256(before).hexdigest() if before else None,'after_sha256':sha(ROOT/rel)})
            patch.extend(difflib.unified_diff(before.decode('utf-8').splitlines(True),after.decode('utf-8').splitlines(True),fromfile='before/'+rel if before else '/dev/null',tofile='after/'+rel))
    save('current_code_diff_manifest.json',{'baseline_archive_sha256':sha(baseline),'files':changes,'scope':'local incremental prototype only; no production service or remote deployment changed'})
    (OUT/'current_changes.patch').write_text(''.join(patch),encoding='utf-8')
    save('verification_receipt.json',{'unit_tests':result.testsRun,'unit_tests_passed':True,'legal_parameterized_scenarios':60,'illegal_parameterized_scenarios':150,'additional_contract_tests':5,'recommendation_tests':7,
        'policy_budget_runs':len(folds),'actual_prediction_rows':len(pred),'source_profile_cases':90,'sensitivity_configurations':54,'profile_configuration_rows':len(sensitivity),
        'outer_target_boundaries_checked':True,'duplicate_keys_checked':True,'each_candidate_point_matches_frozen_adapter_reference':True,'selection_fit_caps_checked':True,
        'human_review_status':'pending','real_llm_ablation':False,'container_startup':False,'future_calendar_validation':False,
        'qualification':'210 parameter combinations are not independent attack types; simulated roles are not real identity authentication. Snapshot evidence links have not received dual-human truth review.'})
    names={'Dengue Fever':'登革热','Campylobacterenterosis':'弯曲菌肠炎','Mumps':'腮腺炎'}
    lines=['教师批注对应新增实验结果报告','2026年10月23日','',
       '本次实际完成了香港分预测期区间复算、六维推荐离线实现及消融、准入参数敏感性、授权组件测试和应用层本地冒烟测试。旧实验和失败记录保留。未连接服务器，未启动线上服务，未使用真实凭据。',
       '', '一 香港预测分期评价',
       '从已交付档案读取4968条配对记录。正文对应的是recursive_hist_poisson；已逐预测期核对9行存档WAPE和RMSE。PROJECT_RECURSIVE_ROUTE是另一组路由输出，不与正文方法混用。两种身份的分析均留档，正式解释使用与原方法一致的列。H0单列为当期估计，H1—H8为向前预测；每预测期552个原点。',
       '按原点保留相关步长，用5000次移动块自助法计算“项目减AWBE”差值，主块长8，4/13作敏感性分析。历史重算不等于新训练或盲测，未作多重检验校正。']
    for r in hk:
        if r['block']==8:
            lo,hi=r['wape_ci95_pp'];lines.append(f"H{r['horizon']}".replace('HH','H')+f"：项目WAPE {r['project_wape_pct']:.2f}%，AWBE {r['awbe_wape_pct']:.2f}%，差值 {r['delta_wape_pp']:+.3f}个百分点，95%区间[{lo:+.3f},{hi:+.3f}]。")
    lines+=['H1差值区间跨零，不能据此宣布等效；H2—H8区间均高于零，表明这些历史目标上的误差较大。去掉H0后，向前预测汇总亦不支持原方法全预测期接近对照。',
       '', '二 六维推荐与消融',
       '新增可复算六维适配模块，候选固定为末值、4周均值、52周季节性和log-AR(2)。硬准入先于等权评分；维度为数据结构D、病种机制M、场景迁移T、科学证据E、参数依据P与执行就绪R。缺证据记0，不扩散不支持病种的知识。V1图谱中仅将明确病种规则下的基线要求关联为部分适用性证据，E=0.5，不视为预测优越性证据；本模块与原工程规则实现分开标记为新原型。',
       '外层为三病种×2020/2021/2022年9个任务，选择窗和校准窗各52周且目标不重叠。每候选只在5个选择原点执行拟合，每原点预测4步；按5/15/30次选择拟合上限比较。推荐最多3个候选，有限搜索可含4个。记录选择、校准和外层预测各阶段的实际拟合数与耗时。所有外层点预测均与同一候选适配器的冻结参考逐项核对。',
       '策略为六维、无图谱、去机制维度、去迁移维度、仅画像、随机、固定规则、有限搜索和冻结模型选择不更新，共243次策略预算运行、49410条逐项预测。冻结不更新只冻结模型选择，残差校准仍随年份更新，不能称为完全无反馈系统。随机仅使用一个固定随机种子，不支持总体随机策略优劣推断。',
       '30次选择拟合上限下的三年合并结果如下，WAPE按同一病种的610个键整体计算，不平均年度百分比。']
    for disease in TARGETS:
        for policy in ('six_dim','profile_only','no_graph','budget_search','fixed_rule','frozen_no_feedback'):
            r=next(x for x in metrics if (x['disease'],x['policy'],x['budget'])==(disease,policy,30))
            lines.append(f"{names[disease]} | {policy}：WAPE {r['wape']*100:.2f}%，WIS {r['wis']:.4f}，95%覆盖率 {r['coverage95']*100:.2f}%，选择拟合 {r['selection_fits']}次，总拟合 {r['total_fits']}次。")
    lines+=['六维与仅画像/无图谱等消融在本候选池中得到相同预测，配对差值为0，不能据此宣称知识图谱有增益，也不能推断所有图谱方法无效。原因是可执行候选很少、多个维度缺少区分证据，三候选短名单高度重叠；该解释可由分项评分轨迹核对。',
       '主预算下六维Top-3命中率为9/9，但仅有4个候选及9个相关任务，且仅画像亦为9/9，不能当作推荐增量证据。固定规则和冻结选择不使用当前排名，其Top-k不作为有效指标。与完整搜索相比，选择阶段拟合由每任务20次减至15次，少25%；总成本还包含52次校准及52/53次外层预测，不能声称整体节省25%或已达到30%目标。5/15/30分钟墙钟上限均未触及，不支持墙钟预算收益或GPU/大模型效率结论。',
       '以上研究设计在运行前写入协议，但历史年份此前已查看，属于探索性历史评价，不是前瞻预注册或未知数据盲测。大语言模型增量消融未执行，不能用确定性代码或模拟建议代替真实大模型。',
       '', '三 准入及参数敏感性',
       '使用3病种×3年度×10种真实历史截断长度，共90个画像病例，不插值或补零。历史长度为4/7/8/15/16/25/26/52/104/156，比较最低观测8/16/26、季节历史104/156、展示阈值40/50/60、候选数1/3/5，共54种配置和4860个画像—配置记录。候选数5受4个候选上限约束；多候选预测按预先指定等权平均，不按测试结果选模型。',
       '共1944条配置记录拒绝输出，比例40%；这是重复配置下的诊断比例，不是独立真实用户任务拒绝率。通过率、有效预测WAPE、拟合数和耗时按配置共同报告；拒绝任务不填0误差，不将仅成功任务的误差与全任务平均混淆。此结果用于边界与权衡检查，不在外层结果上挑选最优阈值并回报主实验。',
       '', '四 授权运行与测试',
       f'新增ContractGate离线执行边界，绑定数据快照、任务身份、图谱版本、模型、参数、原点、输出范围及风险复核状态；支持待审批、批准、运行、完成、失败、撤销与过期。{result.testsRun}项测试通过，其中60个合法和150个非法参数组合，另含5项授权生命周期/并发回放/失败处理检查和7项推荐边界检查。',
       '150个非法组合均未执行回调；60个合法组合均执行成功且相同幂等请求只执行一次。24个并发同键调用返回相同结果，实际回调1次。执行失败不发布半成品，不自动重试；返回对象采用副本，不能通过修改返回值改变已保存结果。243个预测运行实际经过该执行边界。',
       '这些是新组件的本进程、内存状态测试，210个组合不等于210种独立攻击；审批角色在离线脚本中模拟，不构成人工批准或真实身份认证。尚未接入生产API认证、持久化队列、多进程锁和真实权限体系，不能把组件测试通过扩大为完整系统授权验收。',
       '', '五 本地工作流与容器条件',
       '应用层冒烟测试最终通过，12个API端点均返回200；完成来源样例、清洗、特征、预测、反馈及worker任务链。网络访问使用本地样例，仅允许Windows异步事件循环所需本机回环，外部连接禁用。该测试使用合成样例和临时SQLite，只证明工作流，不用其误差指标作为真实疾病预测精度。',
       '首次临时目录运行因SQLite访问失败；改用工作区临时目录后，API又因全socket禁用无法建立Windows异步回环而失败；仅调整测试隔离环境后重跑通过，业务代码与断言未改，失败记录均保留。Compose静态检查通过，但本机无Docker CLI，未启动容器，亦未启动真实HTTP或Streamlit服务。',
       '', '六 尚需真人或外部条件的事项',
       '双人图谱真实性与适用性标注、5—8名目标用户任务评价、项目负责人或导师确认，不能由自动测试代签；12周影子评价需要真实日历及新发布数据。本机缺少torch、transformers与已确认可用的本地模型，真实LLM消融仍缺运行条件。Docker容器验收需安装或提供可用的本地Docker环境后运行。未检索或宣布新的历史资料为未参与开发数据。',
       '因此本轮已补齐一批可执行实验和结果，但不能表示全部教师批注已实质关闭；六维增量作用和真实权限链路仍需进一步验证。',
       '', '七 复核与归档',
       '运行：python tools/run_teacher_experiments_20261023.py hk；python tools/run_teacher_experiments_20261023.py system；python tools/run_recommendation_ablation_20261023.py；python tools/test_teacher_experiments_20261023.py。使用现有项目环境；冻结输出已存在时拒绝覆盖，应在项目副本的新输出目录中复跑。finish脚本可核对并打包。',
       '保留协议、逐项预测、分项评分、测试日志、失败记录、SHA-256清单和相对上一交付快照的代码差分。此包为项目增量材料，不包含数据库、凭据、远程配置和大模型权重。']
    destination=ROOT/'output/proposal_submission_20261023/教师批注新增实验结果_20261023.txt'
    if (OUT/'workflow_regression_retry.xml').exists():
        from xml.etree import ElementTree as ET
        suites=list(ET.parse(OUT/'workflow_regression_retry.xml').getroot().iter('testsuite'))
        regression={'tests':sum(int(s.get('tests',0)) for s in suites),'failures':sum(int(s.get('failures',0)) for s in suites),
                    'errors':sum(int(s.get('errors',0)) for s in suites),'skipped':sum(int(s.get('skipped',0)) for s in suites)}
        save('workflow_regression_counts.json',regression)
        lines+=['','八 既有工作流回归测试',
          f"另运行13个既有测试文件，重跑后共{regression['tests']}项，失败{regression['failures']}项、错误{regression['errors']}项、跳过{regression['skipped']}项。",
          '首次42项中40项通过，2项失败原因是旧测试仍断言访问策略只有6个来源，而现有示例配置已包含第7项CDC FluView。核对该来源默认禁用实时抓取、必须审核、每次最多1请求后，仅将两处来源数量断言更新为7，并增加该来源限额及默认拒绝断言，未修改访问策略或放宽权限。修复前后文件哈希及失败输出保留。测试覆盖API、看板格式与安全、worker、访问策略和Compose静态检查，不等于浏览器视觉验收或容器启动。',
          '当前V1知识版本可能晚于历史预测年份，因此图谱实验同样是当前知识下的历史分析，不证明历史时点实时可用性。Python 3.12.14、NumPy 2.5.0；本地Ollama/LM Studio命令及Ollama服务未发现可用运行条件。']
    destination.write_text('\n'.join(lines)+'\n',encoding='utf-8-sig')
    archive=destination.parent/'教师批注新增实验_代码数据与结果_20261023.zip'
    include=[ROOT/p for p in files]+[p for p in OUT.iterdir() if p.is_file() and p.suffix in ('.json','.csv','.txt','.patch')]+[destination,DATA,GRAPH,
        ROOT/'reports/nested_validation_20261023/predictions.csv',ROOT/'tools/package_local_snapshot.py']
    for n in ('provenance.json','protocol.json'):include.append(DATA.parent/n)
    with ZipFile(archive,'w',ZIP_DEFLATED) as z:
        manifest={}
        for p in include:
            z.write(p,p.relative_to(ROOT).as_posix());manifest[p.relative_to(ROOT).as_posix()]=sha(p)
        z.writestr('SHA256SUMS.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    with ZipFile(archive) as z:
        assert z.testzip() is None
        expected=json.loads(z.read('SHA256SUMS.json'))
        assert all(hashlib.sha256(z.read(name)).hexdigest()==value for name,value in expected.items())
    print(json.dumps({'tests':result.testsRun,'report':str(destination),'archive':str(archive),'size':archive.stat().st_size},ensure_ascii=False))

if __name__=='__main__':main()
