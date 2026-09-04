# SISAT — Sistema de Control de Asistencia

Aplicación de **escritorio para PC**. Autor: **WAMBOO TIC**.

Control de relojes biométricos ZKTeco, asistencia multi-sede, tardanzas, Excel y reportes. Los datos quedan en este equipo (`data/attendance.db`).

## Instalar en una PC

1. Python 3.12 o superior (marque *Add python.exe to PATH*).
2. Ejecute `CREAR_INSTALADOR.bat` en el equipo de desarrollo. Sale un ZIP en `dist\`.
3. En la PC destino: descomprima el ZIP y ejecute `SETUP.bat`.
4. O, en esta misma carpeta, ejecute `INSTALAR.bat`.

Acceso inicial: usuario `admin` / contraseña `admin123`. Cámbiela al entrar.

## Cómo se abre

| Archivo | Uso |
|---|---|
| `INICIAR.bat` | Ventana de escritorio |
| `INICIAR_CONSOLA.bat` | Igual, con consola para ver errores |
| `INICIAR_SERVIDOR.bat` | Servidor web en `http://127.0.0.1:8000` (red local / ADMS) |
| `CREAR_INSTALADOR.bat` | Genera el ZIP instalador en `dist\` |
| `PUBLICAR_GITHUB.bat` | Sube el código a GitHub para actualizar otras PCs |
| `DESINSTALAR.bat` | Quita accesos directos |

## Actualizar desde GitHub

1. Suba el código con `PUBLICAR_GITHUB.bat` al repo `https://github.com/wamboo5610/SISAT`.
2. En cada PC: menú **Sistema** → **Buscar actualización** → **Instalar versión nueva**.
3. Si el repositorio es privado, pegue un token de GitHub (permiso `repo`) en esa misma pantalla.

La base de datos no se toca al actualizar.

## Exportar o restaurar la base

En **Sistema** (o en Relojes):

- **Exportar base de datos** — ZIP con `attendance.db` y cuentas.
- **Restaurar base de datos** — reemplaza los datos actuales (solo administrador).

## Módulos

- Relojes biométricos, asistencia y tardanzas
- Empleados y horarios
- Importar Excel y tardanzas RRHH
- Descarga local sin internet
- Conexión remota ADMS
- Usuarios del sistema
- Sistema: versión, GitHub, exportar/restaurar

## Autor

**WAMBOO TIC**
