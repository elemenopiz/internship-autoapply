$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:AUTOAPPLY_DATA_DIR = Join-Path $root 'data'
$env:AUTOAPPLY_WORKBOOK = 'C:\Users\zsrum\Downloads\UT Austin Internship Database 2027 - Master.xlsx'
$env:OPENAI_API_KEY = [Environment]::GetEnvironmentVariable('OPENAI_API_KEY', 'User')
$python = Join-Path $root 'venv\Scripts\python.exe'
$code = @'
from config.settings import load_config
from core.internship_policy import missing_profile_fields
from core.internship_policy import is_target_internship
from bot.search.workbook import WorkbookSearcher
import os

config = load_config()
missing = missing_profile_fields(config)
print('Profile ready:', not missing)
if missing:
    print('Still needed:', ', '.join(missing))
print('Relevant workbook jobs currently eligible for review:', sum(1 for job in WorkbookSearcher().search(config.search_criteria) if is_target_internship(job)))
print('LLM provider/model:', config.llm.provider, '/', config.llm.model)
print('LLM API key present:', bool(config.llm.api_key))
print('Autonomous mode:', config.bot.apply_mode)
print('Max applications per day:', config.bot.max_applications_per_day)
print('Schedule enabled:', config.bot.schedule.enabled)
'@
$env:PYTHONPATH = Join-Path $root 'app'
& $python -c $code
