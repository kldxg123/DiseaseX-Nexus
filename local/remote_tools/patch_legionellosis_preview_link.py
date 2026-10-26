from pathlib import Path

APP = Path("USER_HOME/projects/openclaw/src/epidemic_core/dashboard/app.py")

text = APP.read_text(encoding="utf-8")
old = "<a href='/p1-12/trusted'>查看 P1-12 可信 Ebola / Influenza 正式数据</a> · <a href='/ebola-preview'>查看历史埃博拉候选 Preview</a>"
new = "<a href='/p1-12/trusted'>查看 P1-12 可信 Ebola / Influenza 正式数据</a> · <a href='/ebola-preview'>查看历史埃博拉候选 Preview</a> · <a href='/legionellosis-preview'>美国军团菌病 Preview</a>"
if "美国军团菌病 Preview" not in text:
    text = text.replace(old, new, 1)
    APP.write_text(text, encoding="utf-8")
    print("link patched")
else:
    print("link already present")
