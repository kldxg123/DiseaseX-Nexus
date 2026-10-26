from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

root=Path(r"F:\作业\aibola\reports\who_official_fresh_trials_20260814T104500Z_extracted\who_official_fresh_trials_20260814T104500Z")
out=root/"10_FRESH_TRIALS_FIGURE.png"
fp=r"C:\Windows\Fonts\msyh.ttc";fb=r"C:\Windows\Fonts\msyhbd.ttc"
def font(n,b=False): return ImageFont.truetype(fb if b else fp,n)
W,H=2400,1200
im=Image.new("RGB",(W,H),"#F8FAFC");d=ImageDraw.Draw(im)
navy="#0F172A";blue="#2563EB";gray="#64748B";red="#DC2626";green="#16A34A";paleg="#DCFCE7";paler="#FEE2E2";border="#CBD5E1"
title="WHO 官方 fluID 三条 Fresh 路径：数值过线不等于完成认证"
d.text((W//2,45),title,font=font(54,True),fill=navy,anchor="ma")
# panels
L=(70,150,1250,1040);R=(1290,150,2330,1040)
for box in (L,R): d.rounded_rectangle(box,26,fill="white",outline=border,width=3)
d.text((110,190),"A. Fresh 盲测：项目模型与强基线",font=font(34,True),fill=navy)
d.text((1330,190),"B. 严格认证门禁矩阵",font=font(34,True),fill=navy)
# left chart
names=["赞比亚 SARI 比率","老挝 ILI 比率","南非 SARI 病例数"]
model=[25.9632,33.9828,24.8759];base=[None,32.4760,20.1403];agree=[74.0368,66.0172,75.1241];cov=[89.7436,87.5,100.0]
x0,x1=360,1150;scale=(x1-x0)/40
for tick in range(0,41,10):
 x=x0+tick*scale;d.line((x,285,x,915),fill="#E2E8F0",width=2);d.text((x,930),f"{tick}%",font=font(22),fill=gray,anchor="ma")
xt=x0+30*scale;d.line((xt,270,xt,915),fill=red,width=4);d.text((xt,250),"30% 门槛",font=font(22,True),fill=red,anchor="ma")
ys=[390,590,790]
for i,y in enumerate(ys):
 d.text((330,y+10),names[i],font=font(25,True),fill=navy,anchor="ra")
 d.rounded_rectangle((x0,y-35,x0+model[i]*scale,y+10),10,fill=blue)
 d.text((x0+model[i]*scale+12,y-12),f"{model[i]:.2f}%",font=font(22,True),fill=blue)
 if base[i] is None:
  d.rounded_rectangle((x0,y+28,x0+145,y+72),10,fill="#E2E8F0")
  d.text((x0+155,y+50),"N/A：样本不一致",font=font(20),fill=gray,anchor="lm")
 else:
  d.rounded_rectangle((x0,y+28,x0+base[i]*scale,y+72),10,fill=gray)
  d.text((x0+base[i]*scale+12,y+50),f"{base[i]:.2f}%",font=font(22),fill=gray,anchor="lm")
 d.text((x0,y+103),f"吻合度 {agree[i]:.2f}%   区间覆盖率 {cov[i]:.2f}%",font=font(20),fill="#334155")
d.rounded_rectangle((520,980,565,1005),7,fill=blue);d.text((580,992),"项目模型",font=font(20),fill=navy,anchor="lm")
d.rounded_rectangle((760,980,805,1005),7,fill=gray);d.text((820,992),"最佳可认证基线",font=font(20),fill=navy,anchor="lm")
# right matrix
glabels=["WAPE\n≤30%","吻合度\n≥70%","覆盖率\n≥85%","同样本","优于基线\n≥1%","一次性\n消费","时间链"]
gate=[[1,1,1,0,0,1,1],[0,0,1,1,0,1,1],[1,1,1,1,0,1,1]]
mx,my=1470,310;cw,ch=115,155
for j,g in enumerate(glabels):
 d.multiline_text((mx+j*cw+cw/2,my-55),g,font=font(18,True),fill=navy,anchor="mm",align="center",spacing=2)
for i,name in enumerate(names):
 y=my+i*ch;d.text((1450,y+ch/2),name,font=font(21,True),fill=navy,anchor="ra")
 for j,val in enumerate(gate[i]):
  box=(mx+j*cw+4,y+7,mx+(j+1)*cw-4,y+ch-7)
  d.rounded_rectangle(box,12,fill=paleg if val else paler,outline="#86EFAC" if val else "#FCA5A5",width=2)
  d.text(((box[0]+box[2])/2,(box[1]+box[3])/2),"通过" if val else "失败",font=font(20,True),fill="#166534" if val else "#991B1B",anchor="mm")
d.rounded_rectangle((1360,835,2260,980),18,fill="#EFF6FF",outline="#93C5FD",width=2)
d.multiline_text((1400,870),"认证结果：0 / 3\n南非轨道虽达到 75.12% 吻合度，仍因季节基线更优而拒绝认证。",font=font(24,True),fill="#1E3A8A",spacing=12)
d.text((W//2,1115),"注：吻合度=max(0,1-WAPE)×100%，不是分类准确率；三条轨道均未部署、未 promotion。",font=font(25),fill="#334155",anchor="ma")
im.save(out,optimize=True)
print(out)
