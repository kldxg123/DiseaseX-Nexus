from pathlib import Path

source_path = Path(r"F:\作业\aibola\generate_python_main_figures.py")
source = source_path.read_text(encoding="utf-8")
source = source.replace("left,right=170,2670", "left,right=280,2670")
source = source.replace("d.text((70,(y0+y1)//2),label", "d.text((145,(y0+y1)//2),label")
exec(compile(source, str(source_path), "exec"))
