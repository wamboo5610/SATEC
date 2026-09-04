# SATEC — Sistema de Asistencia Técnico

Aplicación de **escritorio para PC**. Autor: **WAMBOO TIC**.

No confundir con **SISAT** (sistema web). SATEC es el programa de escritorio.

## En esta carpeta (uso diario)

| Archivo | Uso |
|---|---|
| `INICIAR.bat` | Abre SATEC |
| `INSTALAR.bat` | Primera vez o reinstalación (no borra la base de datos) |
| `data\` | Bases de datos (`attendance.db` y cuentas) |

## Instalar en otras PCs

1. En esta PC: `herramientas\CREAR_INSTALADOR.bat`
2. Copie el ZIP de `dist\` a cada máquina
3. Descomprima y dé **doble clic en INSTALAR.bat**
4. Si no hay Python, el instalador descarga una copia portátil
5. Acceso: `admin` / `admin123` (cámbielo)

Reinstalar **no borra** marcaciones ni empleados.

## Actualizar (dentro de un mes o cuando publique)

1. En esta PC: `herramientas\PUBLICAR_GITHUB.bat` (repo `wamboo5610/SATEC`)
2. En las otras PCs, al abrir SATEC aparece el aviso **Hay una nueva versión disponible**
3. **Sistema → Instalar versión nueva**. La base de datos no se toca.

## Herramientas

Carpeta `herramientas\`: publicador GitHub, creador del instalador, consola y servidor LAN.
