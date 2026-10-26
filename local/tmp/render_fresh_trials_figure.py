from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.colors import ListedColormap

root=Path(r"F:\作业\aibola\reports\who_official_fresh_trials_20260814T104500Z_extracted\who_official_fresh_trials_20260814T104500Z")
out=root/"10_FRESH_TRIALS_FIGURE.png"
font_path=Path(r"C:\Windows\Fonts\msyh.ttc")
font=FontProperties(fname=str(font_path)) if font_path.exists() else None
bold=FontProperties(fname=str(font_path),weight="bold") if font_path.exists() else None
names=["赞比亚 SARI 比率","老挝 ILI 比率","南非 SARI 病例数"]
model=np.array([25.9632,33.9828,24.8759])
base=np.array([np.nan,32.4760,20.1403])
agreement=[74.0368,66.0172,75.1241]
coverage=[89.7436,87.5,100.0]
gate=np.array([[1,1,1,0,0,1,1],[0,0,1,1,0,1,1],[1,1,1,1,0,1,1]],dtype=int)
glabels=["WAPE≤30%","吻合度≥70%","覆盖率≥85%","同样本","优于基线≥1%","一次性消费","时间链"]
fig=plt.figure(figsize=(16,8),facecolor="white")
gs=fig.add_gridspec(1,2,width_ratios=[1.25,1.0],wspace=.25)
ax=fig.add_subplot(gs[0,0])
y=np.arange(len(names)); h=.28
ax.barh(y-h/2,model,height=h,color="#2563EB",label="项目模型")
ax.barh(y+h/2,np.nan_to_num(base,nan=0),height=h,color="#64748B",label="最佳可认证基线")
ax.axvline(30,color="#DC2626",linestyle="--",linewidth=1.6,label="WAPE 30% 门槛")
for i,v in enumerate(model):
 ax.text(v+.5,i-h/2,f"{v:.2f}%  / 吻合度 {agreement[i]:.2f}%",va="center",fontsize=10,fontproperties=font)
for i,v in enumerate(base):
 if np.isnan(v): ax.text(.6,i+h/2,"N/A（样本不一致）",va="center",fontsize=10,color="#475569",fontproperties=font)
 else: ax.text(v+.5,i+h/2,f"{v:.2f}%",va="center",fontsize=10,color="#475569",fontproperties=font)
ax.set_xlim(0,43);ax.set_yticks(y,names,fontproperties=font);ax.invert_yaxis()
ax.set_xlabel("WAPE（%，越低越好）",fontproperties=font);ax.set_title("A. Fresh 盲测：项目模型与强基线",fontproperties=bold,fontsize=15)
ax.grid(axis="x",alpha=.18);ax.spines[["top","right"]].set_visible(False)
ax.legend(prop=font,loc="lower right")
ax2=fig.add_subplot(gs[0,1]);cmap=ListedColormap(["#FEE2E2","#DCFCE7"])
ax2.imshow(gate,aspect="auto",cmap=cmap,vmin=0,vmax=1)
ax2.set_xticks(range(len(glabels)),glabels,rotation=38,ha="right",fontproperties=font)
ax2.set_yticks(range(len(names)),names,fontproperties=font)
for i in range(gate.shape[0]):
 for j in range(gate.shape[1]):
  ax2.text(j,i,"通过" if gate[i,j] else "失败",ha="center",va="center",color="#166534" if gate[i,j] else "#991B1B",fontproperties=bold,fontsize=10)
ax2.set_title("B. 严格认证门禁矩阵",fontproperties=bold,fontsize=15)
for spine in ax2.spines.values(): spine.set_visible(False)
fig.suptitle("WHO 官方 fluID 三条 Fresh 路径：数值过线不等于完成认证",fontproperties=bold,fontsize=19,y=.98)
fig.text(.5,.02,"注：吻合度=max(0,1-WAPE)×100%，不是分类准确率；三条轨道均未通过全部门禁，因此未部署、未 promotion。",ha="center",fontproperties=font,fontsize=11,color="#334155")
fig.savefig(out,dpi=220,bbox_inches="tight")
plt.close(fig)
print(out)
