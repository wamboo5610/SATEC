@echo off
title SISAT - Actualizar claves local y Vercel
cd /d "%~dp0"
echo ================================================
echo   ACTUALIZAR CLAVES (LOCAL + VERCEL)
echo ================================================
echo.
echo Usuario: admin
echo Clave:   Admin2026
echo.
python -c "from app import auth; import secrets; data=auth._migrate_auth(auth._load_auth()); u=next(x for x in data['users'] if x.get('role')=='admin'); salt=secrets.token_hex(16); u['salt']=salt; u['password_hash']=auth._hash_password('Admin2026', salt); u['username']='admin'; u['must_change_password']=False; auth._save_auth(data); assert auth.verify_credentials('admin','Admin2026'); print('Clave local actualizada.')"
if errorlevel 1 (
  echo Error al actualizar clave local.
  pause
  exit /b 1
)
echo.
echo Subiendo vercel.json y auth.json a GitHub...
git add vercel.json data/auth.json .gitignore
git commit -m "Actualizar claves admin para Vercel (admin / Admin2026)"
if errorlevel 1 (
  echo Sin cambios nuevos en git o commit fallo.
) else (
  git push origin main
  if errorlevel 1 (
    echo Error al subir a GitHub.
    pause
    exit /b 1
  )
  echo.
  echo Listo. Vercel aplicara las claves en 1-3 minutos.
)
echo.
echo Variables en vercel.json:
echo   SISAT_ADMIN_USER=admin
echo   SISAT_ADMIN_PASSWORD=Admin2026
echo   SESSION_SECRET=(configurado)
echo.
echo URL: https://sisat.vercel.app
echo Inicio de sesion: admin / Admin2026
pause