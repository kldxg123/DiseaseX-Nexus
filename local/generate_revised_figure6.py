from pathlib import Path
import csv
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(r"F:\作业\aibola\reports\nc_submission_revision_20260824\NC_Submission_Updated_20260824")
DATA = ROOT / "Supplementary" / "Table4_Figure6_Experiment_20260824" / "Figure_6_Data.csv"
OUTDIR = ROOT / "Supplementary" / "Table4_Figure6_Experiment_20260824"

rows = list(csv.DictReader(DATA.open(encoding="utf-8")))
series = rows[:3]
macro = {r["label"]: float(r["full_wape"]) * 100 for r in rows[3:]}

labels = [r["label"] for r in series]
full = np.array([float(r["full_wape"]) * 100 for r in series])
baseline = np.array([float(r["best_baseline_wape"]) * 100 for r in series])
coverage = np.array([float(r["coverage_80"]) * 100 for r in series])
precision = np.array([float(r["warning_precision"]) * 100 if r["warning_precision"] else np.nan for r in series])
recall = np.array([float(r["warning_recall"]) * 100 if r["warning_recall"] else np.nan for r in series])
f1 = np.array([float(r["warning_f1"]) * 100 if r["warning_f1"] else np.nan for r in series])

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9.3, "axes.titlesize": 11.2, "axes.labelsize": 9.7})
fig, axes = plt.subplots(2, 2, figsize=(12.0, 7.9), constrained_layout=False)
fig.subplots_adjust(left=.07, right=.985, top=.89, bottom=.11, wspace=.25, hspace=.36)
fig.suptitle("Figure 6 | Retrospective multi-pathogen evaluation and component ablation", fontsize=15, fontweight="bold")

# A: paired WAPE
ax = axes[0, 0]
x = np.arange(len(labels)); width = .34
b1 = ax.bar(x-width/2, full, width, label="DiseaseX governed system", color="#2f6fb0")
b2 = ax.bar(x+width/2, baseline, width, label="Best fixed baseline", color="#a7b4c4")
ax.axhline(30, color="#b42318", ls="--", lw=1.3, label="WAPE gate (30%)")
ax.set_title("A  Paired forecast error")
ax.set_ylabel("WAPE (%) ↓")
ax.set_xticks(x, labels, rotation=12, ha="right")
ax.set_ylim(0, 33)
ax.grid(axis="y", alpha=.22)
for bars in (b1,b2):
    for b in bars: ax.text(b.get_x()+b.get_width()/2,b.get_height()+.45,f"{b.get_height():.1f}%",ha="center",va="bottom",fontsize=8.3)
ax.text(x[2]+width/2, baseline[2]-2.4, "baseline lower", color="#8f1d14", ha="center", fontsize=7.8, fontweight="bold")
ax.legend(frameon=False, fontsize=8, ncol=3, loc="upper left")

# B: empirical coverage
ax = axes[0, 1]
ax.axhspan(70, 90, color="#dcebd8", alpha=.78, label="Prespecified acceptable band")
ax.axhline(80, color="#355d7a", ls="--", lw=1.3, label="Nominal 80%")
cols=["#3b82c4","#39a777","#ef982d"]
bars=ax.bar(x, coverage, color=cols, width=.58)
ax.set_title("B  Retrospectively calibrated intervals")
ax.set_ylabel("Empirical coverage (%)")
ax.set_xticks(x, labels, rotation=12, ha="right")
ax.set_ylim(0, 105); ax.grid(axis="y", alpha=.2)
for b in bars: ax.text(b.get_x()+b.get_width()/2,b.get_height()+1.5,f"{b.get_height():.1f}%",ha="center",fontsize=8.5)
ax.legend(frameon=False, fontsize=8, loc="upper right")

# C: warning metrics with explicit N/A
ax=axes[1,0]
w=.23
valid=np.arange(2)
for vals,off,color,name in [(precision[:2],-w,"#3b82c4","Precision"),(recall[:2],0,"#39a777","Recall"),(f1[:2],w,"#ef982d","F1")]:
    bars=ax.bar(valid+off,vals,w,color=color,label=name)
    for b in bars: ax.text(b.get_x()+b.get_width()/2,b.get_height()+1.2,f"{b.get_height():.1f}",ha="center",fontsize=8)
ax.bar(2,100,.66,color="#eceff3",edgecolor="#7a8492",hatch="//")
ax.text(2,50,"N/A\nno positive event",ha="center",va="center",fontsize=8.5,color="#4b5563")
ax.axhline(60,color="#a85d55",ls=":",lw=1,label="Precision gate 60%")
ax.axhline(70,color="#705a8a",ls="--",lw=1,label="Recall gate 70%")
ax.set_title("C  Fixed-threshold early-warning evaluation")
ax.set_ylabel("Score (%)")
ax.set_xticks(x,labels,rotation=12,ha="right")
ax.set_ylim(0,108); ax.grid(axis="y",alpha=.2)
ax.legend(frameon=False,fontsize=8,ncol=3,loc="lower left")

# D: component ablation
ax=axes[1,1]
names=["Full system","No KG","Dynamic config","No feedback","No proposal"]
keys=["full_system","ablation_no_kg","ablation_dynamic_execution_config","ablation_no_feedback","ablation_no_proposal"]
vals=np.array([macro[k] for k in keys])
y=np.arange(len(names))
colors=["#2f6fb0"]+["#b9c2cf"]*4
bars=ax.barh(y,vals,color=colors)
ax.set_title("D  Macro-WAPE ablation")
ax.set_xlabel("Macro WAPE (%) ↓")
ax.set_yticks(y,names); ax.invert_yaxis(); ax.set_xlim(0,30.5); ax.grid(axis="x",alpha=.2)
for i,b in enumerate(bars):
    delta=vals[i]-vals[0]
    label=f"{vals[i]:.2f}%" if i==0 else f"{vals[i]:.2f}%  (Δ {delta:+.2f})"
    ax.text(b.get_width()+.25,b.get_y()+b.get_height()/2,label,va="center",fontsize=8.3)

fig.text(.5,.035,"Frozen observations; origin-safe horizons 1–4. Retrospective optimization, not fresh held-out evidence. Missing periods were neither filled nor interpolated.",ha="center",fontsize=8.5,color="#4b5563")

png=OUTDIR / "Figure_6_Retrospective_Multi_Pathogen_Evaluation_v2.png"
svg=OUTDIR / "Figure_6_Retrospective_Multi_Pathogen_Evaluation_v2.svg"
fig.savefig(png,dpi=320,bbox_inches="tight",facecolor="white")
fig.savefig(svg,bbox_inches="tight",facecolor="white")
print(png)
print(svg)
