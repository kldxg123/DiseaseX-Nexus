from pathlib import Path
from datetime import date
import json, math, hashlib
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(r'F:\作业\aibola\reports\nc_submission_revision_20260824\NC_Submission_Updated_20260824')
OUT=ROOT/'Supplementary'/'Python_Generated_Figures_20260824'; OUT.mkdir(parents=True,exist_ok=True)
FONTDIR=Path(r'C:\Windows\Fonts')
def F(size,bold=False): return ImageFont.truetype(str(FONTDIR/('arialbd.ttf' if bold else 'arial.ttf')),size)
NAVY='#17365d'; BLUE='#2f6fb0'; SKY='#dbeafe'; GREEN='#2f9e72'; LIME='#dff2e8'; PURPLE='#7354a3'; LAV='#eee8f7'; ORANGE='#e7992f'; GOLD='#faedcf'; RED='#b42318'; ROSE='#f9e1df'; TEXT='#172033'; GRID='#d8e0ea'; GREY='#6b7280'

def draw_arrow(d,a,b,color=NAVY,width=8,head=22):
    d.line([a,b],fill=color,width=width)
    ang=math.atan2(b[1]-a[1],b[0]-a[0]); p1=(b[0]-head*math.cos(ang-.55),b[1]-head*math.sin(ang-.55)); p2=(b[0]-head*math.cos(ang+.55),b[1]-head*math.sin(ang+.55)); d.polygon([b,p1,p2],fill=color)
def center_text(d,box,text,size=34,bold=False,fill=TEXT,spacing=8):
    d.multiline_text(((box[0]+box[2])//2,(box[1]+box[3])//2),text,font=F(size,bold),fill=fill,anchor='mm',align='center',spacing=spacing)
def box(d,xy,title,body,color,light):
    d.rounded_rectangle(xy,radius=24,fill='white',outline=color,width=5)
    d.rounded_rectangle((xy[0],xy[1],xy[2],xy[1]+92),radius=22,fill=color)
    d.rectangle((xy[0],xy[1]+65,xy[2],xy[1]+92),fill=color)
    center_text(d,(xy[0],xy[1],xy[2],xy[1]+92),title,32,True,'white')
    d.rounded_rectangle((xy[0]+20,xy[1]+120,xy[2]-20,xy[3]-22),radius=18,fill=light)
    center_text(d,(xy[0]+32,xy[1]+118,xy[2]-32,xy[3]-20),body,28,False,TEXT,9)

# Figure 1: deterministic architecture diagram
W,H=3400,1950; im=Image.new('RGB',(W,H),'white'); d=ImageDraw.Draw(im)
d.text((W//2,55),'Figure 1 | DiseaseX Nexus system architecture and governed learning loop',font=F(54,True),fill=TEXT,anchor='ma')
cols=[(80,240,610,1430),(690,240,1220,1430),(1300,240,1830,1430),(1910,240,2440,1430),(2520,240,3320,1430)]
box(d,cols[0],'Official evidence','WHO / CDC / FluNet\nGovernment open data\n\nPaper PDFs and metadata\n\nHuman-reviewed preview',BLUE,SKY)
box(d,cols[1],'Data governance','Raw artifact archive\nURL • retrieval time • SHA-256\n\nNormalization\nDisease • country • period • metric\n\nPostgreSQL layers\nRaw • Trusted • Forecast',GREEN,LIME)
box(d,cols[2],'Knowledge graph','Work • DocumentVersion\nEvidenceSpan • Locator\n\nDisease–Model–Parameter\nApplicability and scope\n\nEvidence scoring and\nExecutionContract',PURPLE,LAV)
box(d,cols[3],'Forecast execution','Origin-safe rolling evaluation\nBaselines and uncertainty\n\nQwen proposes only within\nwhitelisted models and ranges\n\nDeterministic validator\nand exactly-once held-out gates',ORANGE,GOLD)
box(d,cols[4],'Application and feedback','Authenticated read-only APIs\nDashboards and linked views\n\nHuman Review Console\nApprove • reject • request evidence\n\nFeedback ledger\nNo automatic Trusted or\nactive-pointer mutation',RED,ROSE)
for i in range(4): draw_arrow(d,(cols[i][2]+12,835),(cols[i+1][0]-12,835),NAVY,10,30)
d.text((650,790),'ingest',font=F(27,True),fill=NAVY,anchor='mm'); d.text((1260,790),'profile',font=F(27,True),fill=NAVY,anchor='mm'); d.text((1870,790),'contract',font=F(27,True),fill=NAVY,anchor='mm'); d.text((2480,790),'publish view',font=F(27,True),fill=NAVY,anchor='mm')
# governed feedback loop
d.rounded_rectangle((210,1530,3190,1830),radius=28,fill='#f4f7fb',outline=NAVY,width=4)
d.text((260,1580),'Governance controls',font=F(34,True),fill=NAVY)
controls=['Least privilege and authentication','Source and checksum provenance','Origin-safe backtesting','Global once-only held-out consumption','Human approval before promotion']
for i,t in enumerate(controls):
    x=280+i*575; d.rounded_rectangle((x,1650,x+520,1775),radius=18,fill='white',outline='#9fb0c5',width=3); center_text(d,(x+10,1655,x+510,1770),t,24,True,TEXT)
draw_arrow(d,(2920,1528),(2920,1440),RED,8,24); draw_arrow(d,(2550,1510),(1710,1450),PURPLE,7,22)
d.text((2740,1480),'reviewed feedback only',font=F(25,True),fill=RED,anchor='mm')
fig1=OUT/'Main_Figure_1_System_Architecture_Python.png'; im.save(fig1,optimize=True)

# Figure 3: data-driven Brazil dengue trusted series
src=OUT/'Brazil_Dengue_History_Live.json'; data=json.loads(src.read_text(encoding='utf-8'))
by={}
for r in data['items']: by.setdefault(r['metric_code'],[]).append(r)
for rows in by.values(): rows.sort(key=lambda r:r['period_end'])
cases=by['cases_new']; deaths=by['deaths_new']
def dt(s): return date.fromisoformat(s)
start=min(dt(r['period_end']) for r in data['items']); end=max(dt(r['period_end']) for r in data['items']); span=(end-start).days
W,H=3400,2300; im=Image.new('RGB',(W,H),'white'); d=ImageDraw.Draw(im)
d.text((W//2,55),'Figure 3 | Brazil dengue trusted observations and data-quality audit',font=F(52,True),fill=TEXT,anchor='ma')
d.text((W//2,125),'Authenticated live data export • weekly reports • 2024–2026',font=F(28),fill=GREY,anchor='ma')
left,right=170,2670; chart_w=right-left
def xof(s): return left+int((dt(s)-start).days/span*chart_w)
def axes(y0,y1,ymax,label):
    d.line((left,y0,left,y1),fill=TEXT,width=4); d.line((left,y1,right,y1),fill=TEXT,width=4)
    for k in range(6):
        val=ymax*k/5; yy=y1-int((y1-y0)*k/5); d.line((left,yy,right,yy),fill=GRID,width=2); d.text((left-25,yy),f'{val:,.0f}',font=F(25),fill=GREY,anchor='rm')
    d.text((70,(y0+y1)//2),label,font=F(31,True),fill=TEXT,anchor='mm')
def yof(v,y0,y1,ymax): return y1-int(v/ymax*(y1-y0))
axes(250,950,650000,'Weekly new cases')
pts=[]
for r in cases:
    x=xof(r['period_end']); y=yof(float(r['value_numeric']),250,950,650000); pts.append((x,y)); d.rectangle((x-8,y,x+8,950),fill='#274c77')
d.line(pts,fill=BLUE,width=6)
mx=max(cases,key=lambda r:float(r['value_numeric'])); d.text((xof(mx['period_end'])+20,yof(float(mx['value_numeric']),250,950,650000)-20),f"Peak {float(mx['value_numeric']):,.0f}",font=F(27,True),fill=NAVY)
axes(1090,1640,520,'Weekly new deaths'); pts=[]
for r in deaths:
    x=xof(r['period_end']); y=yof(float(r['value_numeric']),1090,1640,520); pts.append((x,y))
d.line(pts,fill=ORANGE,width=8)
for p in pts: d.ellipse((p[0]-7,p[1]-7,p[0]+7,p[1]+7),fill=ORANGE)
mx=max(deaths,key=lambda r:float(r['value_numeric'])); d.text((xof(mx['period_end'])+20,yof(float(mx['value_numeric']),1090,1640,520)-20),f"Peak {float(mx['value_numeric']):,.0f}",font=F(27,True),fill='#9a5b12')
# shared date ticks
for s in ['2024-01-13','2024-07-01','2025-01-01','2025-07-01','2026-01-01','2026-07-11']:
    x=xof(s); d.line((x,1640,x,1655),fill=TEXT,width=3); d.text((x,1675),s[:7],font=F(24),fill=GREY,anchor='ma')
# audit sidebar
d.rounded_rectangle((2760,240,3290,1640),radius=26,fill='#f3f6fa',outline='#9fb0c5',width=4)
d.text((3025,280),'Data-quality audit',font=F(36,True),fill=NAVY,anchor='ma')
accepted=sum(r['quality_status']=='accepted' for r in data['items']); revised=sum(int(r['revision_count'])>1 for r in data['items'])
facts=[('Source','WHO Global Dengue'),('Rows','148 total'),('Metrics','74 cases + 74 deaths'),('Quality',f'{accepted}/148 accepted'),('Revised rows',str(revised)),('Missing values','Not imputed'),('Latest period',max(r['period_end'] for r in data['items'])),('Use','Trusted read-only view')]
y=390
for a,b in facts:
    d.rounded_rectangle((2810,y,3240,y+120),radius=14,fill='white',outline='#cbd5e1',width=2); d.text((2840,y+24),a,font=F(23,True),fill=GREY); d.text((2840,y+63),b,font=F(25,True),fill=TEXT); y+=145
# revision timeline
d.rounded_rectangle((170,1810,3290,2150),radius=24,fill='#f7f9fc',outline='#b7c3d2',width=3)
d.text((220,1850),'Revision and lineage summary',font=F(33,True),fill=NAVY)
d.text((220,1915),'Each row retains source identity, reporting period, revision count and supersession lineage. Missing reporting periods remain gaps.',font=F(27),fill=TEXT)
d.text((220,1985),f"Cases: 51 first-version + 23 revised rows   |   Deaths: 59 first-version + 15 revised rows   |   Export SHA-256: {hashlib.sha256(src.read_bytes()).hexdigest()[:20]}…",font=F(27,True),fill=TEXT)
d.text((220,2055),'No interpolation, zero filling, training, prediction or publication mutation was performed for this figure.',font=F(27),fill=RED)
fig3=OUT/'Main_Figure_3_Brazil_Dengue_Python.png'; im.save(fig3,optimize=True)

for p in (fig1,fig3): print(p)
