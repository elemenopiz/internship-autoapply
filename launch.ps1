$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:AUTOAPPLY_DATA_DIR = Join-Path $root 'data'
$env:AUTOAPPLY_WORKBOOK = 'C:\Users\zsrum\Downloads\UT Austin Internship Database 2027 - Master.xlsx'
$env:OPENAI_API_KEY = [Environment]::GetEnvironmentVariable('OPENAI_API_KEY', 'User')
$python = Join-Path $root 'venv\Scripts\python.exe'
$app = Join-Path $root 'app\run.py'
& $python $app --gui
