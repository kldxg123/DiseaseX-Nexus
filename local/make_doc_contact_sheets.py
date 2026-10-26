from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(r'F:\作业\aibola\reports\nc_submission_revision_20260824\NC_Submission_Updated_20260824')
FONT=ImageFont.truetype(r'C:\Windows\Fonts\arial.ttf',28)
for folder in ['_qa_main_v6','_qa_si_v3','_qa_manifest_v3']:
    src=ROOT/folder; pages=sorted(src.glob('page-*.png'),key=lambda p:int(p.stem.split('-')[-1]))
    out=src/'contact_sheets';out.mkdir(exist_ok=True)
    for batch in range(0,len(pages),12):
        subset=pages[batch:batch+12]; thumbs=[]
        for p in subset:
            im=Image.open(p).convert('RGB'); im.thumbnail((430,600)); thumbs.append((p,im.copy()))
        sheet=Image.new('RGB',(4*470,3*650),'#d9dee6');d=ImageDraw.Draw(sheet)
        for i,(p,im) in enumerate(thumbs):
            x=(i%4)*470+20;y=(i//4)*650+20;sheet.paste(im,(x,y+32));d.text((x,y),p.stem,fill='black',font=FONT)
        sheet.save(out/f'contact-{batch//12+1}.jpg',quality=85)
        print(out/f'contact-{batch//12+1}.jpg')

