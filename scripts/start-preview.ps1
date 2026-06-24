$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

python gateway\server_console_gateway.py `
  --host 127.0.0.1 `
  --port 8765 `
  --mode demo
