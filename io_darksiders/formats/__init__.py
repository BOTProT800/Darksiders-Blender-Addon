"""Lectores y escritores de los formatos de Darksiders, sin dependencia de Blender.

Todo lo que hay aquí funciona con Python y numpy a secas, y por eso se prueba
en la suite sin Blender de por medio. El addon (`..operators`) solo traduce
estos objetos a la escena.

| Módulo | Formato |
|---|---|
| `mesh` | geometría `.2` de Darksiders II: Deathinitive Edition |
| `native` | edición conservadora del `.2` (copia que parte del original) |
| `skeleton` | árbol de nodos `.o3d` (contenedor `BOD`), de Darksiders 1 y 2 |
| `skin` | paletas de huesos y normalización de pesos |
| `animation` | clips `.anm` v1 |
| `obp` | contenedor `.obp`, del que solo se sacan modelos, esqueletos y animaciones |
| `quality` | puertas de calidad de una malla decodificada |
"""
