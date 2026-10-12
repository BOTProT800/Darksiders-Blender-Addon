"""Comprueba el zip que `scripts/build.py` da a instalar a Blender.

Lo que se vigila aquí es el contrato de empaquetado, que es donde se falla en
silencio: Blender busca `blender_manifest.toml` en la **raíz** del zip, así que
envolver el addon en una carpeta produce un archivo que se genera sin quejarse
y que Blender rechaza al instalar. Del mismo modo, colar `__pycache__` deja
dentro los `.pyc` de otra versión de Python, y un import de algo que Blender no
trae solo falla ya dentro de Blender.

No se ejecuta Blender: eso lo hace `scripts/qa_install.py`. Se comprueba el
reparto del zip, que es la parte que sí se puede afirmar sin él.
"""

from __future__ import annotations

import importlib.util
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "io_darksiders"


def load_builder():
    """Importa el script, que vive en `scripts/` y no es un paquete."""
    path = ROOT / "scripts" / "build.py"
    spec = importlib.util.spec_from_file_location("build_addon", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def builder():
    return load_builder()


def _manifest(addon: Path, body: str) -> None:
    addon.mkdir(parents=True, exist_ok=True)
    (addon / "blender_manifest.toml").write_text(body, encoding="utf-8")


def test_manifiesto_del_addon_real_es_instalable(builder):
    manifest = builder.read_manifest(ADDON)
    assert manifest["id"] == ADDON.name == "io_darksiders"
    assert manifest["type"] == "add-on"
    assert manifest["blender_version_min"] == "4.2.0"


def test_la_version_del_manifiesto_y_la_de_bl_info_coinciden():
    import io_darksiders

    with (ADDON / "blender_manifest.toml").open("rb") as handle:
        manifest = tomllib.load(handle)
    version = ".".join(str(part) for part in io_darksiders.bl_info["version"])
    assert manifest["version"] == version


def test_el_manifiesto_queda_en_la_raiz_del_zip(builder, tmp_path):
    bundle = builder.build(ADDON, tmp_path)
    with zipfile.ZipFile(bundle) as archivo:
        nombres = archivo.namelist()

    # En la raíz, sin carpeta contenedora: es lo que Blender espera al instalar.
    assert "blender_manifest.toml" in nombres
    assert "__init__.py" in nombres
    assert "operators.py" in nombres
    assert not any(nombre.startswith("io_darksiders/") for nombre in nombres)
    assert bundle.name == f"io_darksiders-{builder.read_manifest(ADDON)['version']}.zip"


def test_el_zip_lleva_los_parsers_dentro(builder, tmp_path):
    bundle = builder.build(ADDON, tmp_path)
    with zipfile.ZipFile(bundle) as archivo:
        nombres = set(archivo.namelist())

    for modulo in ("mesh", "native", "obp", "skeleton", "skin", "animation", "quality"):
        assert f"formats/{modulo}.py" in nombres
    assert not any(nombre.startswith("_vendor/") for nombre in nombres)


def test_no_se_cuela_pycache(builder, tmp_path):
    addon = tmp_path / "io_darksiders"
    (addon / "__pycache__").mkdir(parents=True)
    (addon / "__pycache__" / "plan.cpython-311.pyc").write_bytes(b"\x00")
    (addon / "plan.pyc").write_bytes(b"\x00")
    (addon / "__init__.py").write_text("", encoding="utf-8")
    _manifest(addon, 'schema_version = "1.0.0"\nid = "io_darksiders"\n'
                     'version = "9.9.9"\nname = "x"\ntype = "add-on"\n')

    empaquetados = {p.name for p in builder.files_to_pack(addon)}
    assert empaquetados == {"__init__.py", "blender_manifest.toml"}


def test_id_que_no_casa_con_la_carpeta_se_rechaza(builder, tmp_path):
    # Blender instala bajo el id del manifiesto: si no coincide con la carpeta,
    # los imports relativos del addon apuntan a un paquete que no existe.
    addon = tmp_path / "io_darksiders"
    _manifest(addon, 'schema_version = "1.0.0"\nid = "otro_nombre"\n'
                     'version = "1.0.0"\nname = "x"\ntype = "add-on"\n')

    with pytest.raises(SystemExit, match="no coincide con la carpeta"):
        builder.read_manifest(addon)


def test_manifiesto_incompleto_se_rechaza(builder, tmp_path):
    addon = tmp_path / "io_darksiders"
    _manifest(addon, 'schema_version = "1.0.0"\nid = "io_darksiders"\n')

    with pytest.raises(SystemExit, match="version"):
        builder.read_manifest(addon)


def test_el_addon_real_no_importa_nada_que_blender_no_traiga(builder):
    """Autosuficiente: lo propio en relativo, lo demás de la biblioteca estándar,
    de la API de Blender o numpy."""
    assert builder.foreign_imports(ADDON) == []


def test_un_import_ajeno_rompe_el_empaquetado(builder, tmp_path):
    addon = tmp_path / "io_darksiders"
    (addon / "formats").mkdir(parents=True)
    (addon / "__init__.py").write_text("from . import formats\n", encoding="utf-8")
    (addon / "formats" / "mesh.py").write_text(
        "import numpy\nfrom services.common import cancel\n", encoding="utf-8"
    )
    _manifest(addon, 'schema_version = "1.0.0"\nid = "io_darksiders"\n'
                     'version = "9.9.9"\nname = "x"\ntype = "add-on"\n')

    assert builder.foreign_imports(addon) == ["formats/mesh.py importa services.common"]
    with pytest.raises(SystemExit, match="services.common"):
        builder.build(addon, tmp_path / "dist")
    assert not (tmp_path / "dist").exists()


def test_el_addon_no_busca_nada_fuera_de_su_carpeta():
    """Ni variable de entorno ni rutas añadidas a `sys.path`. Que las
    preferencias no tengan rutas lo comprueba `tests/test_addon.py`."""
    codigo = "\n".join(path.read_text(encoding="utf-8") for path in ADDON.rglob("*.py"))
    for rastro in ("os.environ", "sys.path", "getenv"):
        assert rastro not in codigo, rastro
