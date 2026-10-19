"""Build a reproducible unsigned Mozilla upload archive from explicit assets only."""

import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=root / 'artifacts')
    args = parser.parse_args()
    extension = root / 'extension'
    manifest = json.loads((extension / 'manifest.json').read_text())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"mcp-browser-bridge-{manifest['version']}.zip"
    assets = {name: extension / name for name in (
        'manifest.json', 'background.js', 'content.js', 'popup.html', 'popup.js', 'popup.css'
    )}
    assets.update({name: root / name for name in ('LICENSE', 'PRIVACY.md')})
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name, path in sorted(assets.items()):
            info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    print(output.resolve())
    print('UNSIGNED: upload this ZIP to Mozilla, then install the signed XPI they return.')


if __name__ == '__main__':
    main()
