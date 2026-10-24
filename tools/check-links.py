#!/usr/bin/env python3
"""Comprueba enlaces relativos y rutas entre comillas invertidas en los .md de un proyecto.

Uso: python check_links.py RAIZ [--exclude comparisons] [--json salida.json]
La existencia se comprueba distinguiendo mayúsculas (como en la Pi, Linux).
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

EXT = {'.md', '.py', '.c', '.h', '.hpp', '.cpp', '.json', '.jsonl', '.csv', '.mp4', '.jpg', '.jpeg',
       '.png', '.pdf', '.bin', '.param', '.onnx', '.service', '.defaults', '.sh', '.txt', '.pcap',
       '.so', '.html', '.conf', '.log', '.i420', '.sha256', '.yaml', '.yml', '.toml', '.ini', '.svg'}
GENERATED = {'build'}
LINK = re.compile(r'!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*(<[^>]+>|[^)\s]+)(?:\s+"[^"]*")?\s*\)')
REFDEF = re.compile(r'^\s{0,3}\[[^\]]+\]:\s*(<[^>]+>|\S+)')
HTML = re.compile(r'(?:href|src)\s*=\s*"([^"]+)"')
CODE = re.compile(r'(`+)(.+?)\1')
FENCE = re.compile(r'^\s{0,3}(```+|~~~+)\s*([\w+-]*)')
SPLIT = re.compile(r"[\s,;'\"()\[\]{}<>|]+")
LINE_SUFFIX = re.compile(r'^(.+\.[A-Za-z0-9]+):\d[\d/,\-]*$')
PATHLIKE = re.compile(r'[A-Za-z0-9_.\-][A-Za-z0-9_.\-/#%+*]*')


def listing(directory, cache={}):
    if directory not in cache:
        try:cache[directory]=set(os.listdir(directory))
        except OSError:cache[directory]=None
    return cache[directory]


def exists_cs(root, rel):
    """Existencia distinguiendo mayúsculas; rel es relativo a root, con '/'."""
    current=str(root)
    for part in [p for p in rel.split('/') if p not in ('','.')]:
        names=listing(current)
        if names is None or part not in names:return False
        current=os.path.join(current,part)
    return True


def normalize(root, base_dir, target):
    """Devuelve la ruta relativa a root (con '/') o None si sale del proyecto."""
    full=os.path.normpath(os.path.join(base_dir,target))
    try:rel=os.path.relpath(full,root)
    except ValueError:return None
    rel=rel.replace('\\','/')
    if rel.startswith('..'):return None
    return '' if rel=='.' else rel


def slug(text):
    text=re.sub(r'<[^>]+>','',text).strip().lower()
    text=re.sub(r'[^\w\- ]','',text,flags=re.UNICODE)
    return text.replace(' ','-')


def anchors(path, cache={}):
    if path not in cache:
        found=set();fenced=False
        for line in path.read_text(encoding='utf-8').splitlines():
            if FENCE.match(line):fenced=not fenced;continue
            m=re.match(r'^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$',line)
            if m and not fenced:found.add(slug(m.group(1)))
        cache[path]=found
    return cache[path]


def build_index(root):
    index={}
    for dirpath,dirnames,filenames in os.walk(root):
        dirnames[:]=[d for d in dirnames if d not in ('.git','__pycache__')]
        rel_dir=os.path.relpath(dirpath,root).replace('\\','/')
        for name in filenames+dirnames:
            rel=name if rel_dir=='.' else f'{rel_dir}/{name}'
            index.setdefault(name,[]).append(rel)
    return index


class Checker:
    def __init__(self, root, exclude):
        self.root=root.resolve();self.exclude=exclude
        self.top=set(os.listdir(self.root))|GENERATED
        self.index=build_index(self.root);self.results=[]

    def add(self, md, line, kind, target, status, note=''):
        self.results.append({'file':md.relative_to(self.root).as_posix(),'line':line,'kind':kind,
                             'target':target,'status':status,'note':note})

    def suggestion(self, rel):
        if rel.startswith('comparisons/') and not rel.startswith('comparisons/old/'):
            moved='comparisons/old/'+rel[len('comparisons/'):]
            if exists_cs(self.root,moved.rstrip('/')):return moved
        name=rel.rstrip('/').split('/')[-1]
        tail='/'.join(rel.rstrip('/').split('/')[-2:])
        hits=[p for p in self.index.get(name,[]) if p.endswith(tail)] or self.index.get(name,[])
        return ' | '.join(sorted(hits)[:3])

    def check_path(self, md, line, kind, raw, rel_to_md_only=False):
        target=unquote(raw.split('#',1)[0].split('?',1)[0])
        anchor=raw.split('#',1)[1] if '#' in raw else ''
        if not target:  # enlace solo a un ancla del mismo documento
            if anchor and slug(anchor)!=anchor.lower() and anchor.lower() not in anchors(md):
                self.add(md,line,kind,raw,'ancla-rota')
            return
        if target.startswith(('/','~')):
            self.add(md,line,kind,raw,'sistema','ruta absoluta o de la Pi; no verificable aquí');return
        bases=[md.parent] if rel_to_md_only else [self.root,md.parent]
        for base in bases:
            rel=normalize(self.root,base,target)
            if rel is None:continue
            if rel.split('/')[0] in GENERATED:
                self.add(md,line,kind,raw,'generado','salida de make/build; no existe en el PC');return
            if '*' in rel:
                if list(self.root.glob(rel)):self.add(md,line,kind,raw,'ok','patrón con coincidencias');return
                continue
            if exists_cs(self.root,rel.rstrip('/')):
                if target.endswith('/') and not (self.root/rel).is_dir():continue
                if anchor and rel.endswith('.md') and anchor.lower() not in anchors(self.root/rel):
                    self.add(md,line,kind,raw,'ancla-rota',f'#{anchor} no encontrado en {rel}');return
                self.add(md,line,kind,raw,'ok',rel);return
            parent,_,stem=rel.rpartition('/')
            names=listing(str(self.root/parent)) if parent else listing(str(self.root))
            if names and any(n.startswith(stem+'.') for n in names):
                self.add(md,line,kind,raw,'ok-prefijo',f'existen {parent}/{stem}.*');return
        if '/' not in target.strip('/'):
            hits=self.index.get(target.rstrip('/'),[])
            if hits:
                self.add(md,line,kind,raw,'ok-nombre',' | '.join(sorted(hits)[:3]));return
        else:
            tail=target.rstrip('/');hits=[p for p in self.index.get(tail.split('/')[-1],[]) if p.endswith('/'+tail)]
            if hits:
                self.add(md,line,kind,raw,'ok-sufijo',' | '.join(sorted(hits)[:3]));return
        rel=normalize(self.root,md.parent if rel_to_md_only else self.root,target) or target
        self.add(md,line,kind,raw,'roto',self.suggestion(rel))

    def tokens(self, md, line, kind, text):
        for tok in SPLIT.split(text):
            if not tok or '://' in tok:continue
            if '=' in tok:tok=tok.rsplit('=',1)[1]
            if tok.startswith('-'):continue
            tok=tok.rstrip('.,:;')
            m=LINE_SUFFIX.match(tok)
            if m:tok=m.group(1)
            if not tok:continue
            if tok.startswith(('/','~')):
                if re.match(r'^~?/[A-Za-z~]',tok) and len(re.findall('[A-Za-z]',tok))>=2:
                    self.add(md,line,kind,tok,'sistema','ruta absoluta o de la Pi; no verificable aquí')
                continue
            if not PATHLIKE.fullmatch(tok):continue
            parts=tok.split('/')
            if any(p.startswith('.') and p not in ('.','..') for p in parts):continue
            ext=os.path.splitext(parts[-1])[1].lower()
            first=parts[0] if parts[0] not in ('.','..') else (parts[1] if len(parts)>1 else '')
            if ext in EXT or ('/' in tok and (first in self.top or tok.startswith(('./','../')))):
                self.check_path(md,line,kind,tok)

    def run(self):
        files=sorted(p for p in self.root.rglob('*.md')
                     if not any(part in self.exclude for part in p.relative_to(self.root).parts))
        for md in files:
            fence=None
            for number,line in enumerate(md.read_text(encoding='utf-8').splitlines(),1):
                m=FENCE.match(line)
                if m:
                    if fence is None:fence=(m.group(1)[0],m.group(2).lower());continue
                    if line.strip().startswith(fence[0]*3):fence=None;continue
                if fence is not None:
                    if fence[1]!='mermaid':self.tokens(md,number,'bloque-código',line)
                    continue
                for span in CODE.finditer(line):self.tokens(md,number,'código',span.group(2))
                plain=CODE.sub(' ',line)
                for m in LINK.finditer(plain):
                    target=m.group(1).strip('<>')
                    if re.match(r'^[a-z][a-z0-9+.-]*:',target,re.I):continue
                    self.check_path(md,number,'enlace',target,rel_to_md_only=True)
                m=REFDEF.match(plain)
                if m and not re.match(r'^[a-z][a-z0-9+.-]*:',m.group(1).strip('<>'),re.I):
                    self.check_path(md,number,'enlace-ref',m.group(1).strip('<>'),rel_to_md_only=True)
                for m in HTML.finditer(plain):
                    if not re.match(r'^[a-z][a-z0-9+.-]*:',m.group(1),re.I):
                        self.check_path(md,number,'html',m.group(1),rel_to_md_only=True)
        return files


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path);p.add_argument('--exclude',nargs='*',default=['comparisons'])
    p.add_argument('--json',type=Path);p.add_argument('--all',action='store_true',help='listar también las correctas')
    a=p.parse_args()
    checker=Checker(a.root,set(a.exclude));files=checker.run()
    counts={}
    for r in checker.results:counts[r['status']]=counts.get(r['status'],0)+1
    print(f'{len(files)} archivos .md; {len(checker.results)} referencias: '+', '.join(f'{k}={v}' for k,v in sorted(counts.items())))
    for r in checker.results:
        if a.all or r['status'] in ('roto','ancla-rota'):
            print(f"{r['status']}\t{r['file']}:{r['line']}\t{r['kind']}\t{r['target']}\t{r['note']}")
    if a.json:a.json.write_text(json.dumps({'files':[f.relative_to(checker.root).as_posix() for f in files],
                                           'counts':counts,'results':checker.results},ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
    return 1 if counts.get('roto') or counts.get('ancla-rota') else 0


if __name__=='__main__':
    sys.exit(main())
