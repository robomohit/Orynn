from pathlib import Path

p = Path('static/index_v2.html')
c = p.read_text(encoding='utf-8')

old = '<option value="computer">Desktop control (Active)</option>'
new = '<option value="computer">Desktop control (Active)</option>\n          <option value="computer_isolated">Isolated App (Background)</option>'

c = c.replace(old, new)
p.write_text(c, encoding='utf-8', newline='\r\n')
print("Successfully patched index_v2.html")
