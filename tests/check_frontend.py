"""Compile shipped browser scripts without importing or running application code."""
import ast
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__();self.inline=False;self.parts=[];self.scripts=[]
    def handle_starttag(self,tag,attrs):
        if tag=='script':self.inline='src' not in dict(attrs);self.parts=[]
    def handle_data(self,data):
        if self.inline:self.parts.append(data)
    def handle_endtag(self,tag):
        if tag=='script' and self.inline:
            self.scripts.append(''.join(self.parts));self.inline=False


def embedded(html):
    parser=Scripts();parser.feed(html);return parser.scripts


def main():
    node=shutil.which('node')
    if not node:raise SystemExit('Node.js is required for this development-only syntax check, not for production.')
    sources=[('management/static/app.js',(ROOT/'management/static/app.js').read_text(encoding='utf-8'))]
    sources.extend(('portable/reporting.html script '+str(i),s) for i,s in enumerate(embedded((ROOT/'portable/reporting.html').read_text(encoding='utf-8')),1))
    tree=ast.parse((ROOT/'management/app.py').read_text(encoding='utf-8'))
    html=next(n.value.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='HOSTED_REPORT_SCRIPT' for t in n.targets))
    sources.extend(('managed report status script '+str(i),s) for i,s in enumerate(embedded(html),1))
    if len(sources)<3:raise SystemExit('Expected the console, reporting and managed-status scripts.')
    for name,script in sources:
        result=subprocess.run([node,'--check','-'],input=script,encoding='utf-8',capture_output=True)
        if result.returncode:raise SystemExit(name+' failed syntax validation:\n'+result.stderr)
        print('PASS: '+name)


if __name__=='__main__':main()
