# Darksiders II: implementación y plan de calidad de animaciones

> Escrito en ANANSI (`docs/darksiders-animation-quality-plan.md`) y trasladado
> aquí el 2026-10-10. Las cifras y rutas de `temp/` y `dist/` son las de
> aquella máquina y aquel repositorio; los scripts y tests tienen ya los
> nombres de este. La validación se repitió el 2026-10-10 con el zip 2.0.0
> (al final del documento).

Inicio: 2026-09-16. Actualización: 2026-09-17. Estado: importador `.anm` 1.2.0
implementado, empaquetado y probado en Blender real con los límites indicados.

## Objetivo y alcance

Importar clips nativos de Deathinitive Edition como acciones de Blender sobre
el esqueleto correcto, preservando tiempos, movimiento de raíz y transformaciones.
La primera entrega cubre importación y reproducción. La exportación de animaciones
al juego y su carga por Darksiders2DLL requieren una fase independiente.

El plan se actualizará con resultados medidos. Un clip creado sin errores no
equivale a un movimiento correcto. No se inventarán claves para ocultar datos
desconocidos, huesos sin resolver ni variantes de compresión no interpretadas.

## Fases y criterios de aceptación

1. **Inventario y especificación.** Contar archivos y bytes por firma/versión;
   incluir pequeños, grandes y familias distintas: reposo, desplazamiento,
   combate y facial. Identificar duración, frecuencia, pistas, tiempos y
   compresión. Explicar todos los bytes estructurales; rechazar truncamientos,
   desbordamientos y variantes desconocidas con su motivo.
2. **Asociación con el esqueleto.** Resolver identificadores contra los nombres
   originales del `.o3d`, detectar colisiones y no asociar por orden de huesos.
   Informar pistas ausentes y distinguir nodos auxiliares de huesos deformantes.
3. **Transformaciones.** Determinar si los valores son locales, absolutos o
   relativos al reposo. Verificar traslación, escala y orden del cuaternión con
   muestras reales. Mantener idéntica conversión de ejes que la malla y el rig.
   No alterar la pose de descanso para hacer encajar una animación.
4. **Integración en Blender.** Crear acciones con nombres y procedencia;
   preservar acciones existentes; mantener segundos aunque cambien los FPS de
   la escena; respetar tiempos irregulares y pistas constantes. Probar ambos
   modos de orientación y guardado/reapertura del `.blend`.
5. **Validación numérica independiente.** Comparar matrices globales evaluadas
   por Blender con la evaluación del clip en el servicio, en claves y tiempos
   intermedios. Registrar errores máximos de traslación y rotación y tolerancias
   derivadas de la cuantización. Comprobar valores finitos y cuaterniones válidos.
6. **Validación visual.** Inspeccionar secuencias o láminas de varios instantes
   sobre mallas reales; buscar huesos intercambiados, saltos, reflejos, pérdida
   de movimiento de raíz y deformaciones. Conservar escenas y resultados.
7. **Regresión y entrega.** Pruebas sintéticas con valores conocidos y entradas
   dañadas, corpus real con resultados por archivo, addon empaquetado y probado
   desde sus módulos incluidos. Repetir la vuelta `.2` sin cambios para asegurar
   que la incorporación de animaciones no rompe el trabajo anterior.

## Estados que deben mostrarse

- **Validado:** estructura y evaluación correctas para la variante comprobada.
- **Sospechoso:** se decodifica, pero hay discrepancias o pistas sin resolver;
  informar cuáles y no describir el clip como completo.
- **Rechazado:** datos corruptos o variante sin implementar; no crear una acción
  parcial silenciosamente. Un fallo no debe borrar acciones del usuario.

## Evidencia inicial

La carpeta `media/characters/death` contiene 1.057 `.anm`; el árbol completo
contiene 6.865 (134.862.492 bytes). Todos usan `ANM`, versión **u8** 1,
endianness **u8** 0 y 30 FPS. La estructura completa se recorre sin bytes
sobrantes. No hay un campo de versión u16: esa interpretación inicial era errónea.
Los hashes se recuperan de las cadenas serializadas del `.o3d`, sin adivinar
nombres ni asociar por orden. El parser conserva también nombres inline.

El primer recorrido numérico detectó 94 clips con identificadores repetidos:
el parser ahora los conserva, porque son datos válidos del contenedor. La
asociación rechaza dos pistas dirigidas al mismo hueso; no decide silenciosamente
cuál gana. Hay claves posteriores a la duración declarada en numerosos clips:
se conservan para interpolar; el rango de reproducción termina en `frame_count-1`.

### Evidencia de evaluación

Se contrastaron componentes de las curvas con las rutinas del ejecutable local
emuladas por Unicorn. El script exige el SHA256 exacto del build antes de usar
sus RVAs. No inicia el juego ni contiene sus bytes. Comprueba claves y puntos
medios, incluidas traslación fina, escalas half y canal escalar.
Nueve clips cubren las seis variantes de flags de pista del corpus, con
1.242.372 evaluaciones contrastadas. Errores máximos por componente:
posición 0,0001221 unidades; cuaternión 2,213e-7; escala 0; escalar 9,818e-8.
El oráculo rechaza diferencias mayores que 0,002 unidades, 3e-6 por componente
de cuaternión, 1e-5 en escala o 1e-6 en el escalar. La prueba de Blender rechaza
errores de posición/matriz superiores a 0,05 y rotaciones superiores a 0,003
radianes. La inspección visual sigue siendo obligatoria: comparar dos rutas
que comparten una convención equivocada puede producir un falso positivo.

La primera revisión visual detectó la convención pasiva de los cuaterniones:
decodificar XYZW correctamente no bastaba, y la matriz activa inicial retorcía
la malla. Se corrigió la conversión mediante conjugación/transposición; la
rotación constante de mandíbula/dedos y las nuevas vistas confirmaron el cambio.
Hay una prueba sintética explícita para evitar esta regresión.

Blender real: Belial (`Run_F`, `Idle`, `Atk_TridentStrike_Strike`) y cabeza de
Death (`D_Dash_F`, `Death_Visemes`), cada uno con ambas orientaciones. FPS de
escena 24 y 59,94. Se comparan matrices globales también entre muestras.
En las diez combinaciones, el mayor error de posición medido es 0,000194
unidades. Guardar/reabrir `.blend`, volver a importar y conservar acciones
anteriores pasan. Las vistas muestran siluetas coherentes después de corregir
la convención; telas y efectos físicos siguen fuera de alcance.
Estas comprobaciones también pasan cargando únicamente los módulos incluidos
del ZIP 1.2.0. La regresión `.2` conserva bytes idénticos en los tres modelos,
ambas orientaciones y al reabrir; mantiene la edición de posición, UV y pesos.
Exportar una malla con pose animada se rechaza expresamente.

La evidencia numérica consolidada está en
[`historial/animacion-validacion-2026-09-17.json`](historial/animacion-validacion-2026-09-17.json).
El ledger completo por archivo, con 19.999.238 claves decodificadas, se guarda
fuera del repo en
`C:\Users\vicen\Documents\Extractions\Darksiders\investigacion-anm\anm-corpus-final.json`,
junto a los scripts de investigación `anm_oracle.py` y `anm_disasm.py`. Las
escenas y renders válidos del ZIP (`temp/qa-anm-zip-01/`) y la regresión `.2`
(`temp/qa-anm-native-model-regression/`) se borraron el 2026-10-11; de ellos
queda el resumen consolidado de arriba. Estos archivos con assets son locales
y no se incluyen en git ni en la extensión.

ZIP validado por `blender --command extension validate`:
`dist/io_anansi_darksiders-1.2.0.zip` (86.281 bytes), SHA256
`8fd97fa37936e77792b9f1f9985672e20f03ba8d21723714dbcb97dd0705af51`.

Pruebas específicas del lector: **112 aprobadas**. Ruff y `git diff --check`
sin errores. Suite general final: **1.272 aprobadas, 30 omitidas, 458 subpruebas
aprobadas y un fallo** (580 segundos; `temp/anm-pytest-final.xml`). El fallo
es ajeno a Darksiders y está en
`tests/test_slayer_materials.py::Juego::test_el_aliado_resuelve_su_difusa_de_cuerpo`:
espera `body_2048_d.x2_syt`, pero la extracción local actual de Battleship
resuelve `body_blue_d.png`. Se ejecutó el resolutor original de `HEAD` y devolvió
exactamente el mismo PNG. No se modificaron esos módulos, la prueba ni los assets.

### Límites y estado de las acciones

En las muestras reales existen pistas que no aparecen en el `.o3d` elegido:
Belial tiene entre 2 y 8, Death entre 5 y 19. Por defecto el operador rechaza
la importación incompleta. La opción explícita `Permitir pistas sin hueso`
permite evaluar la parte asociada y deja la acción como `suspicious`, con lista
de identificadores. Esto no prueba que el rig sea adecuado: Reaper requiere
su propio esqueleto. La mera coincidencia de unos nodos auxiliares no basta.

Se mantienen como pendientes semánticos el flag de cabecera `0x2`, el canal
escalar aplicado a materiales/visibilidad, las mezclas de capas/pesos, eventos
y física. No hay exportador `.anm`, integración nueva con Darksiders2DLL ni
prueba de animaciones modificadas dentro del juego.

Las acciones usan muestras cada medio fotograma nativo; la interpolación
cuaternión lineal de Blender aproxima SLERP entre muestras. La duración se
adapta a los FPS al importar; cambiar posteriormente los FPS requiere reimportar.
El límite de 3 millones de claves rechaza clips que saturarían la sesión.

### Comprobaciones reproducibles

- `scripts/qa_animation_corpus.py`: ledger por archivo, bytes,
  variantes, duplicados, claves y motivo de rechazo; no afirma QA visual masiva.
- `scripts/qa_animation_native.py`: oráculo independiente opcional;
  dependencias de QA pefile/Unicorn, fuera del addon.
- `scripts/qa_animation_blender.py`: matrices, tiempos, guardado,
  acciones anteriores, rechazo de truncamientos, poses y renders.
- `scripts/qa_blender.py`: regresión de ida y vuelta `.2` y edición.
- `tests/test_animation.py`: valores conocidos, convención de
  rotación, asociación exacta, colisiones, truncamientos y variantes inválidas.

## Pendientes para ampliar soporte

- Resolver nodos omitidos y canales del motor para pasar de parcial a completo.
- Determinar precedencia de pistas duplicadas para el mismo hueso.
- Validar una muestra más amplia de rigs y un ejecutable Blender 4.2/4.3.
- Escritura `.anm` conservadora, roundtrip sin cambios byte idéntico y edición
  validada numéricamente; después compatibilidad del cargador y prueba en juego.

## Revalidación con el addon independiente (2026-10-10)

Mismo QA (`scripts/qa_animation_blender.py --expect-packaged`) contra el zip
`io_darksiders-2.0.0.zip` extraído, en Blender 5.2.2 LTS: los cinco clips en
ambas orientaciones pasan, con un error de posición máximo de 0,000193
unidades (Belial, `Atk_TridentStrike_Strike`) y de rotación de 1,75e-7
radianes. Las pistas sin hueso son las mismas que antes: Belial 2–8, Death
5–19. Los parsers se cargaron desde la carpeta del zip.
