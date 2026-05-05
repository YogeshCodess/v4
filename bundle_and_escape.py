import re
from pathlib import Path

dist = Path('hardware-pipeline-v5-react/dist')
src_html = (dist / 'index.html').read_text(encoding='utf-8')

# Inline CSS (fallback — vite-plugin-singlefile already handles this)
for css_file in dist.glob('assets/*.css'):
    tag = f'<link rel="stylesheet" crossorigin href="/assets/{css_file.name}">'
    src_html = src_html.replace(tag, f'<style>{css_file.read_text("utf-8")}</style>')

# Inline JS (fallback — vite-plugin-singlefile already handles this)
for js_file in dist.glob('assets/*.js'):
    tag = f'<script type="module" crossorigin src="/assets/{js_file.name}"></script>'
    src_html = src_html.replace(tag, f'<script type="module">{js_file.read_text("utf-8")}</script>')

# vite-plugin-singlefile preserves rel/crossorigin attrs on inlined <style> tags.
# Browsers tolerate these but they are invalid HTML — strip them for cleanliness.
src_html = re.sub(r'<style\s+rel="stylesheet"\s+crossorigin>', '<style>', src_html)

# Escape non-ASCII in entire HTML.
# Characters above U+FFFF (emoji etc.) need JS surrogate pairs — \uXXXX
# only covers BMP (U+0000–U+FFFF).  Without surrogate pairs the browser
# reads e.g. \u1F4CB as U+1F4C (garbage) + literal "B".
def escape_non_ascii(text):
    def escape_char(c):
        cp = ord(c)
        if cp > 0xFFFF:
            cp -= 0x10000
            high = 0xD800 + (cp >> 10)
            low  = 0xDC00 + (cp & 0x3FF)
            return f'\\u{high:04X}\\u{low:04X}'
        return f'\\u{cp:04X}'
    return re.sub(r'[^\x00-\x7F]', lambda m: escape_char(m.group()), text)

src_html = escape_non_ascii(src_html)

out = Path('frontend/bundle.html')
out.parent.mkdir(exist_ok=True)
out.write_text(src_html, encoding='ascii')
print(f'Bundle written: {out} ({out.stat().st_size // 1024} KB)')
