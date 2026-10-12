# CLAUDE.md — Darksiders para Blender

[`AGENTS.md`](AGENTS.md) es la instrucción canónica para cualquier asistente de
IA en este repositorio. Se importa aquí porque Claude Code, cuando encuentra los
dos archivos, por defecto solo carga `CLAUDE.md`.

@AGENTS.md

## Comandos

```bash
python -m pytest            # suite completa, sin Blender
ruff check .
python scripts/build.py --blender "C:/Program Files (x86)/Steam/steamapps/common/Blender/blender.exe"
```
