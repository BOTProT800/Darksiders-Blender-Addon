"""Inventory/decode local ANM assets; never equate decoding with visual QA."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from io_darksiders.formats.animation import load_anm


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--assets-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('el informe de destino ya existe')
    rows, variants = [], {}
    for path in sorted(args.assets_root.rglob('*.anm')):
        row = {'path': str(path.relative_to(args.assets_root)), 'bytes': path.stat().st_size}
        try:
            clip = load_anm(path)
            ids = [t.identifier for t in clip.tracks]
            duplicate = len(ids) - len(set(ids))
            row.update(state='decoded', fps=clip.fps, frames=clip.frame_count,
                       flags=clip.flags, tracks=len(ids), duplicate_tracks=duplicate,
                       scalar_tracks=sum(t.scalar is not None for t in clip.tracks),
                       keys=sum(len(c.frames) for t in clip.tracks for c in
                                (t.rotation,t.position,t.scale,t.scalar) if c is not None))
            for t in clip.tracks:
                variants.setdefault(str(t.flags), row['path'])
        except Exception as exc:
            row.update(state='rejected', reason=f'{type(exc).__name__}: {exc}')
        rows.append(row)
        if len(rows) % 1000 == 0:
            print(f'{len(rows)} archivos comprobados', flush=True)
    summary = {'files':len(rows),'bytes':sum(r['bytes'] for r in rows),
               'states':dict(Counter(r['state'] for r in rows)), 'track_flag_examples':variants,
               'scope':'Structure and finite decoded values only; binding and visual QA are separate.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'summary':summary,'files':rows},indent=2),encoding='utf-8')
    print(json.dumps(summary),flush=True)


if __name__ == '__main__':
    main()
