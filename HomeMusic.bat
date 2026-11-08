@echo off
chcp 65001 >nul
rem Toggle the HomeMusic stack (Navidrome, Hub, tunnel): starts it when it is off or partly down, stops it when it runs.
rem Starts Docker Desktop if needed. Never stops Docker Desktop itself: other containers may run in it.
setlocal
title HomeMusic
cd /d "%~dp0"

docker info >nul 2>&1
if errorlevel 1 goto start_docker

set RUNNING=0
set SERVICES=0
for /f %%n in ('docker compose ps -q --status running 2^>nul ^| find /c /v ""') do set RUNNING=%%n
for /f %%n in ('docker compose config --services 2^>nul ^| find /c /v ""') do set SERVICES=%%n
if %SERVICES% equ 0 (
  echo Не удалось прочитать docker-compose.yml и .env в "%CD%".
  goto fail
)
if %RUNNING% geq %SERVICES% goto stop
goto start


:start_docker
echo Docker Desktop не запущен — запускаю...
start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
set WAITED=0
:wait_docker
timeout /t 5 /nobreak >nul
docker info >nul 2>&1
if not errorlevel 1 goto start
set /a WAITED+=5
if %WAITED% geq 240 (
  echo Docker Desktop не поднялся за 4 минуты.
  goto fail
)
goto wait_docker


:start
echo Запускаю HomeMusic: Navidrome, Хаб, туннель...
docker compose up -d
if errorlevel 1 goto fail
echo Жду, пока Хаб ответит...
set WAITED=0
:wait_hub
curl -fs -o nul http://localhost:8080/healthz 2>nul
if not errorlevel 1 goto started
set /a WAITED+=3
if %WAITED% geq 120 (
  echo Хаб не ответил за 2 минуты: docker logs --tail 50 homemusic_hub
  goto fail
)
timeout /t 3 /nobreak >nul
goto wait_hub

:started
echo.
echo HomeMusic включён.
echo   Хаб:       http://localhost:8080
echo   Navidrome: http://localhost:4533
goto done


:stop
echo HomeMusic работает.
set ACTIVE=0
for /f %%n in ('docker exec homemusic_hub python -c "import sqlite3;c=sqlite3.connect('file:/data/hub.sqlite3?mode=ro',uri=True);print(c.execute('select count(*) from download_items where status in (?,?,?,?,?)',('queued','searching','downloading','tagging','importing')).fetchone()[0])" 2^>nul') do set ACTIVE=%%n
if not "%ACTIVE%"=="0" echo Загрузок в очереди: %ACTIVE%. Они продолжатся после включения.
choice /c YN /m "Выключить HomeMusic (у пользователей пропадёт музыка)?"
if errorlevel 2 (
  echo Отменено.
  goto done
)
echo Выключаю...
docker compose stop
if errorlevel 1 goto fail
echo.
echo HomeMusic выключен. Docker Desktop продолжает работать.
echo После перезагрузки ПК HomeMusic останется выключенным, пока его не включить этим файлом.
goto done


:fail
echo.
echo Что-то пошло не так — подробности выше.
pause
exit /b 1

:done
echo.
timeout /t 15
exit /b 0
