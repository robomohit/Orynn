from pathlib import Path

p = Path('static/index_v2.html')
c = p.read_text(encoding='utf-8')

# Fix setMode labels
c = c.replace(
    "const modeLabels = { computer: 'Desktop', computer_use: 'Browser' };",
    "const modeLabels = { computer: 'Desktop', computer_use: 'Browser', computer_isolated: 'Isolated' };"
)

# Fix onchange listener
old_onchange = """  $('mode-id').onchange = (e) => {
    setMode(e.target.value);
    $('isolated-app-wrap').style.display = e.target.value === 'computer' ? '' : 'none';
  };"""

new_onchange = """  $('mode-id').onchange = (e) => {
    const val = e.target.value;
    setMode(val, val === 'computer_isolated');
    $('isolated-app-wrap').style.display = (val === 'computer' || val === 'computer_isolated') ? '' : 'none';
  };"""

c = c.replace(old_onchange, new_onchange)

p.write_text(c, encoding='utf-8', newline='\r\n')
print("Successfully patched index_v2.html logic")
