"""Editable draw.io graph and matching deterministic print illustration.

Desktop draw.io is unavailable here. The PNG/SVG are a coordinate-matched
fallback renderer, not purported native draw.io CLI exports.
"""
from pathlib import Path
import math,json
from lxml import etree as E
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'output/proposal_completion_20261023'

def main():
    OUT.mkdir(exist_ok=True)
    nodes={'pending':(60,330,'待审批','PENDING'),'approved':(600,330,'已批准','APPROVED'),'running':(1140,330,'执行中','RUNNING'),
        'completed':(1680,60,'已完成','COMPLETED'),'failed':(1680,580,'已失败','FAILED'),'revoked':(60,720,'已撤销','REVOKED'),'expired':(600,40,'已过期','EXPIRED')}
    edges=[('pending','approved',[(300,390),(600,390)],'复核批准',(355,340)),
        ('approved','running',[(840,390),(1140,390)],'绑定校验',(890,340)),
        ('running','completed',[(1380,370),(1510,370),(1510,120),(1680,120)],'成功',(1535,82)),
        ('running','failed',[(1380,420),(1510,420),(1510,640),(1680,640)],'失败',(1540,585)),
        ('pending','revoked',[(150,450),(150,720)],'撤销',(170,645)),
        ('approved','revoked',[(660,450),(660,620),(240,620),(240,720)],'撤销',(345,585)),
        ('pending','expired',[(240,330),(240,100),(600,100)],'到期检查',(340,45)),
        ('approved','expired',[(720,330),(720,160)],'到期检查',(750,235)),
        ('completed','completed',[(1920,120),(2040,120),(2040,20),(1800,20),(1800,60)],'幂等返回',(1930,180))]
    mx=E.Element('mxfile',host='drawio',version='26.0.0');diagram=E.SubElement(mx,'diagram',name='授权状态迁移');model=E.SubElement(diagram,'mxGraphModel');root=E.SubElement(model,'root')
    E.SubElement(root,'mxCell',id='0');E.SubElement(root,'mxCell',id='1',parent='0')
    svg=E.Element('svg',xmlns='http://www.w3.org/2000/svg',width='2100',height='930',viewBox='0 0 2100 930')
    E.SubElement(svg,'rect',width='2100',height='930',fill='white')
    image=Image.new('RGB',(2100,930),'white');draw=ImageDraw.Draw(image);font=ImageFont.truetype('C:/Windows/Fonts/simsun.ttc',48);small=ImageFont.truetype('C:/Windows/Fonts/simsun.ttc',42)
    def label(x,y,s,size=30):
        draw.text((x,y),s,font=font if size==30 else small,fill='#202020')
        actual_size=48 if size==30 else 42
        text=E.SubElement(svg,'text',x=str(x),y=str(y+actual_size),fill='#202020',**{'font-family':'SimSun,serif','font-size':str(actual_size)});text.text=s
    def line(points):
        draw.line(points,fill='#555555',width=3)
        E.SubElement(svg,'polyline',points=' '.join(f'{x},{y}' for x,y in points),fill='none',stroke='#555555',**{'stroke-width':'3'})
        a,b=points[-2:];angle=math.atan2(b[1]-a[1],b[0]-a[0]);head=[b,(b[0]-18*math.cos(angle-.45),b[1]-18*math.sin(angle-.45)),(b[0]-18*math.cos(angle+.45),b[1]-18*math.sin(angle+.45))]
        draw.polygon(head,fill='#555555');E.SubElement(svg,'polygon',points=' '.join(f'{x},{y}' for x,y in head),fill='#555555')
    for identifier,(x,y,zh,en) in nodes.items():
        cell=E.SubElement(root,'mxCell',id=identifier,value=zh+'<br>'+en,style='rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;fontSize=48;fontColor=#202020;',vertex='1',parent='1')
        E.SubElement(cell,'mxGeometry',x=str(x),y=str(y),width='240',height='120',**{'as':'geometry'})
        draw.rounded_rectangle((x,y,x+240,y+120),radius=14,fill='#f5f5f5',outline='#666666',width=3)
        E.SubElement(svg,'rect',x=str(x),y=str(y),width='240',height='120',rx='14',fill='#f5f5f5',stroke='#666666',**{'stroke-width':'3'})
        label(x+(240-draw.textlength(zh,font=font))/2,y+10,zh);label(x+(240-draw.textlength(en,font=font))/2,y+64,en)
    for number,(source,target,points,title,xy) in enumerate(edges):
        sx,sy,*_=nodes[source];tx,ty,*_=nodes[target]
        start,end=points[0],points[-1]
        style='edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;labelBackgroundColor=#ffffff;fontSize=42;'
        style+=f'exitX={(start[0]-sx)/240};exitY={(start[1]-sy)/120};entryX={(end[0]-tx)/240};entryY={(end[1]-ty)/120};'
        cell=E.SubElement(root,'mxCell',id='edge'+str(number),value=title,style=style,edge='1',parent='1',source=source,target=target)
        geometry=E.SubElement(cell,'mxGeometry',relative='1',**{'as':'geometry'})
        if len(points)>2:
            path=E.SubElement(geometry,'Array',**{'as':'points'})
            for x,y in points[1:-1]:E.SubElement(path,'mxPoint',x=str(x),y=str(y))
        line(points);label(*xy,title,26)
    label(1000,770,'执行前先持久化 RUNNING；中断后不自动重试',26)
    label(1000,820,'幂等返回仍检查有效期、绑定内容与请求键',26)
    label(60,875,'非法身份、过期或绑定错配均拒绝执行；到期状态在请求检查时更新。',26)
    (OUT/'授权状态迁移.drawio').write_bytes(E.tostring(mx,encoding='utf-8',xml_declaration=True))
    (OUT/'授权状态迁移.svg').write_bytes(E.tostring(svg,encoding='utf-8',xml_declaration=True));image.save(OUT/'授权状态迁移.png')
    (OUT/'diagram_generation.json').write_text(json.dumps({'native_drawio_cli_available':False,'source':'editable draw.io XML','preview':'deterministic coordinate-matched Pillow/SVG fallback','state_count':len(nodes),'edge_count':len(edges)},indent=2))

if __name__=='__main__':main()
