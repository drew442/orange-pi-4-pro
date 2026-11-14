#!/usr/bin/env python3
"""Check local documentation targets and agent TOML without reading build logs."""
import json
from pathlib import Path
import re
import sys
import tomllib
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def check(root=ROOT):
    errors = []
    files = [root / 'README.md', root / 'AGENTS.md', *sorted((root / 'docs').rglob('*.md'))]
    links = 0
    retained_external_targets = 0
    for path in files:
        text = path.read_text()
        text = re.sub(r'^```[^\n]*\n.*?^```[^\n]*$', '', text, flags=re.M | re.S)
        for target in re.findall(r'\]\(([^\s)]+)(?:\s+"[^"]*")?\)', text):
            parts = urlsplit(target)
            if parts.scheme or parts.netloc or not parts.path or parts.path.startswith('/'):
                continue
            destination = (path.parent / unquote(parts.path)).resolve()
            if destination.is_relative_to(root) and destination.relative_to(root).parts[0] in ("out", "tmp"):
                retained_external_targets += 1
                continue
            links += 1
            if not destination.exists():
                errors.append({'file': str(path.relative_to(root)), 'missing_target': target})
    configs = sorted((root / '.codex').rglob('*.toml'))
    for path in configs:
        try:
            tomllib.loads(path.read_text())
        except (ValueError, OSError) as error:
            errors.append({'file': str(path.relative_to(root)), 'error': type(error).__name__})
    return {'status': 'FAIL' if errors else 'PASS', 'markdown_files': len(files),
            'local_file_targets': links, 'retained_external_targets_not_checked': retained_external_targets,
            'toml_files': len(configs), 'errors': errors}


if __name__ == '__main__':
    result = check()
    print(json.dumps(result, sort_keys=True))
    sys.exit(1 if result['errors'] else 0)
