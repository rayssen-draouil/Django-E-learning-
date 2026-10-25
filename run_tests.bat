@echo off
rem SkillForge - suite de tests + rapport de couverture
rem Base Mongo dediee e_learning_test (jamais la base reelle).
cd /d "%~dp0"
set DJANGO_SETTINGS_MODULE=config.settings.testing
if "%1"=="cover" (
    .venv\Scripts\python.exe -m coverage run manage.py test
    .venv\Scripts\python.exe -m coverage report
    .venv\Scripts\python.exe -m coverage html
    echo Rapport HTML : htmlcov\index.html
) else (
    .venv\Scripts\python.exe manage.py test %*
)
