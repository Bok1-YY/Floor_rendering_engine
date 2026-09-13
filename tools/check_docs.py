"""Check local links, anchors, and source paths in maintained entry-point docs."""
from pathlib import Path
import re
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
DOCS = ['README.md', 'README.en.md', 'DEVGUIDE.md', 'RUNNABLE_README.md', 'web/README.md', 'docs/WINDOWS_RELEASE.md', 'docs/VALIDATION_CURRENT.md']

def anchors(text):
    result = set(re.findall(r'<a\s+(?:id|name)=["\']([^"\']+)', text))
    seen = {}
    for heading in re.findall(r'^#{1,6}\s+(.+?)\s*#*$', text, re.M):
        name = re.sub(r'[^\w\-\s]', '', heading.lower()).replace(' ', '-')
        n = seen.get(name, 0); seen[name] = n + 1
        result.add(name if n == 0 else f'{name}-{n}')
    return result

def main():
    errors = []
    for name in DOCS:
        doc = ROOT / name; text = doc.read_text(encoding='utf-8-sig')
        for raw in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', text):
            target = raw.strip().split(' "')[0].strip('<>')
            if re.match(r'^[a-z]+:', target, re.I): continue
            location, _, fragment = target.partition('#')
            path = (doc.parent / unquote(location)).resolve() if location else doc
            if not path.exists(): errors.append(f'{name}: missing link {target}'); continue
            if fragment and path.suffix.lower() == '.md' and unquote(fragment) not in anchors(path.read_text(encoding='utf-8-sig')):
                errors.append(f'{name}: missing anchor {target}')
        # Validate concrete source paths, excluding commands, API URLs and runtime data.
        for token in re.findall(r'(?<!`)`([^`\n]+)`(?!`)', text):
            if not re.fullmatch(r'[\w./-]+\.(?:py|tsx?|bat|ps1)', token): continue
            if not (doc.parent / token).exists() and not (ROOT / token).exists(): errors.append(f'{name}: missing source path {token}')
    if errors:
        print('\n'.join(errors)); return 1
    print(f'Documentation checks passed: {len(DOCS)} maintained documents.'); return 0

if __name__ == '__main__': sys.exit(main())
