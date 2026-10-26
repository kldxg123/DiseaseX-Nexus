from pathlib import Path
import csv
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(r"F:\作业\aibola\reports\nc_submission_revision_20260824\NC_Submission_Updated_20260824")
DATA = ROOT / "Supplementary" / "Table4_Figure6_Experiment_20260824" / "Figure_6_Data.csv"
OUTDIR = DATA.parent
rows = list(csv.DictReader(DATA.open(encoding="utf-8")))
series = rows[:3]
macro = {r["label"]: float(r["full_wape"]) * 100 for r in rows[3:]}

W, H = 3600, 2350
im = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(im)
font_dir = Path(r"C:\Windows\Fonts")
def font(size, bold=False):
    return ImageFont.truetype(str(font_dir / ("arialbd.ttf" if bold else "arial.ttf")), size)

NAVY="#17365d"; BLUE="#2f6fb0"; GREY="#a7b4c4"; GREEN="#3aa978"; ORANGE="#ef982d"; RED="#b42318"; GRID="#dbe2ea"; TEXT="#172033"

def txt(xy, s, size=34, fill=TEXT, bold=False, anchor=None):
    d.text(xy, s, font=font(size,bold), fill=fill, anchor=anchor)

def line(points, fill=GRID, width=3): d.line(points, fill=fill, width=width)

def panel(box, title):
    x0,y0,x1,y1=box
    d.rounded_rectangle(box, radius=24, fill="#fbfdff", outline="#bdc9d8", width=3)
    txt((x0+32,y0+24),title,38,NAVY,True)
    return x0+80,y0+105,x1-45,y1-75

txt((W//2,62),"Figure 6 | Retrospective multi-pathogen evaluation and component ablation",54,TEXT,True,"ma")

labels=["AUT Legionellosis","FIN Hantavirus","BRA Dengue"]
full=[float(r["full_wape"])*100 for r in series]
base=[float(r["best_baseline_wape"])*100 for r in series]
coverage=[float(r["coverage_80"])*100 for r in series]
prec=[100.0,86.11,None]; rec=[100.0,88.57,None]; f1=[100.0,87.32,None]

# A
px0,py0,px1,py1=panel((110,190,1740,1090),"A  Paired forecast error")
chart=(px0,py0+60,px1,py1-95); x0,y0,x1,y1=chart
for v in range(0,31,5):
    yy=y1-(v/32)*(y1-y0); line([(x0,yy),(x1,yy)],GRID,2); txt((x0-18,yy),str(v),26,"#4b5563",False,"rm")
gate=y1-(30/32)*(y1-y0); line([(x0,gate),(x1,gate)],RED,4); txt((x1-5,gate-12),"30% WAPE gate",25,RED,True,"rb")
group=(x1-x0)/3
for i,(a,b,lbl) in enumerate(zip(full,base,labels)):
    cx=x0+group*(i+.5); bw=92
    for val,dx,col in [(a,-55,BLUE),(b,55,GREY)]:
        top=y1-(val/32)*(y1-y0); d.rectangle((cx+dx-bw/2,top,cx+dx+bw/2,y1),fill=col); txt((cx+dx,top-12),f"{val:.1f}%",26,TEXT,True,"mb")
    txt((cx,y1+28),lbl,26,TEXT,False,"ma")
txt((x0-55,(y0+y1)//2),"WAPE (%)",28,TEXT,True,"mm")
d.rectangle((x0,y1+66,x0+34,y1+100),fill=BLUE); txt((x0+48,y1+83),"DiseaseX governed system",25,anchor="lm")
d.rectangle((x0+480,y1+66,x0+514,y1+100),fill=GREY); txt((x0+528,y1+83),"Best fixed baseline",25,anchor="lm")
txt((x0+group*2.5+55,y1-(base[2]/32)*(y1-y0)+65),"baseline lower",22,RED,True,"ma")

# B
px0,py0,px1,py1=panel((1860,190,3490,1090),"B  Retrospectively calibrated intervals")
x0,y0,x1,y1=px0,py0+60,px1,py1-95
band_top=y1-(90/105)*(y1-y0); band_bottom=y1-(70/105)*(y1-y0); d.rectangle((x0,band_top,x1,band_bottom),fill="#dcebd8")
for v in range(0,101,20):
    yy=y1-(v/105)*(y1-y0); line([(x0,yy),(x1,yy)],GRID,2); txt((x0-18,yy),str(v),26,"#4b5563",False,"rm")
nom=y1-(80/105)*(y1-y0); line([(x0,nom),(x1,nom)],NAVY,4); txt((x1-5,nom-12),"Nominal 80%",25,NAVY,True,"rb")
group=(x1-x0)/3
for i,(val,lbl,col) in enumerate(zip(coverage,labels,[BLUE,GREEN,ORANGE])):
    cx=x0+group*(i+.5); bw=220; top=y1-(val/105)*(y1-y0); d.rectangle((cx-bw/2,top,cx+bw/2,y1),fill=col); txt((cx,top-14),f"{val:.1f}%",28,TEXT,True,"mb"); txt((cx,y1+28),lbl,26,TEXT,False,"ma")
txt((x0,y1+78),"Prespecified acceptable band: 70–90%",25,"#2f5d3d")

# C
px0,py0,px1,py1=panel((110,1170,1740,2070),"C  Fixed-threshold early-warning evaluation")
x0,y0,x1,y1=px0,py0+60,px1,py1-105
for v in range(0,101,20):
    yy=y1-(v/105)*(y1-y0); line([(x0,yy),(x1,yy)],GRID,2); txt((x0-18,yy),str(v),26,"#4b5563",False,"rm")
for val,col,label in [(60,"#a85d55","Precision gate"),(70,"#705a8a","Recall gate")]:
    yy=y1-(val/105)*(y1-y0); line([(x0,yy),(x1,yy)],col,3); txt((x1-5,yy-10),f"{label} {val}%",22,col,True,"rb")
group=(x1-x0)/3; bw=70
for i,lbl in enumerate(labels):
    cx=x0+group*(i+.5)
    if i<2:
        for val,dx,col in [(prec[i],-80,BLUE),(rec[i],0,GREEN),(f1[i],80,ORANGE)]:
            top=y1-(val/105)*(y1-y0); d.rectangle((cx+dx-bw/2,top,cx+dx+bw/2,y1),fill=col); txt((cx+dx,top-9),f"{val:.1f}",21,TEXT,True,"mb")
    else:
        d.rectangle((cx-150,y0+90,cx+150,y1),fill="#edf0f4",outline="#7a8492",width=3); txt((cx,(y0+y1)//2),"N/A\nNo positive event",27,"#4b5563",True,"mm",)
    txt((cx,y1+28),lbl,26,TEXT,False,"ma")
for j,(c,lbl) in enumerate([(BLUE,"Precision"),(GREEN,"Recall"),(ORANGE,"F1")]):
    xx=x0+j*300; d.rectangle((xx,y1+70,xx+34,y1+104),fill=c); txt((xx+48,y1+87),lbl,25,anchor="lm")

# D
px0,py0,px1,py1=panel((1860,1170,3490,2070),"D  Macro-WAPE component ablation")
x0,y0,x1,y1=px0+230,py0+45,px1,py1-30
names=["Full system","No KG","Dynamic config","No feedback","No proposal"]
keys=["full_system","ablation_no_kg","ablation_dynamic_execution_config","ablation_no_feedback","ablation_no_proposal"]
vals=[macro[k] for k in keys]
for v in range(0,31,5):
    xx=x0+(v/31)*(x1-x0); line([(xx,y0),(xx,y1)],GRID,2); txt((xx,y1+18),str(v),24,"#4b5563",False,"ma")
row=(y1-y0)/5
for i,(name,val) in enumerate(zip(names,vals)):
    cy=y0+row*(i+.5); txt((x0-18,cy),name,27,TEXT,False,"rm"); length=(val/31)*(x1-x0); d.rectangle((x0,cy-35,x0+length,cy+35),fill=BLUE if i==0 else "#b9c2cf"); delta=val-vals[0]; lab=f"{val:.2f}%" if i==0 else f"{val:.2f}%  (Δ {delta:+.2f})"; txt((x0+length+14,cy),lab,24,TEXT,True,"lm")
txt(((x0+x1)//2,y1+70),"Macro WAPE (%)",27,TEXT,True,"ma")

txt((W//2,2230),"Frozen observations; origin-safe horizons 1–4. Retrospective optimization, not fresh held-out evidence.",28,"#4b5563",False,"ma")
txt((W//2,2280),"Missing periods were neither filled nor interpolated.",28,"#4b5563",False,"ma")

png=OUTDIR / "Figure_6_Retrospective_Multi_Pathogen_Evaluation_v2.png"
im.save(png,optimize=True)
print(png)
