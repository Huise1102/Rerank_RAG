param(
    [ValidateSet("init", "backend", "admin", "user")]
    [string]$Target = "backend"
)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $ProjectRoot "backend"
$FrontendRoot = Join-Path $ProjectRoot "frontend"
$Python = Join-Path $BackendRoot ".venv\Scripts\python.exe"

switch ($Target) {
    "init" {
        if (-not (Test-Path $Python)) {
            python -m venv (Join-Path $BackendRoot ".venv")
        }
        & $Python -m pip install -r (Join-Path $BackendRoot "requirements.txt")
        if (-not (Test-Path (Join-Path $ProjectRoot ".env"))) {
            Copy-Item (Join-Path $ProjectRoot ".env.example") (Join-Path $ProjectRoot ".env")
            Write-Host "已生成 .env，请填写数据库口令与大模型密钥后重新执行"
            exit 1
        }
        & $Python (Join-Path $BackendRoot "scripts\init_db.py")
    }
    "backend" {
        & $Python -m uvicorn src.main:app --app-dir $BackendRoot --host 127.0.0.1 --port 8004
    }
    "admin" {
        Push-Location $FrontendRoot
        npm run dev:admin
        Pop-Location
    }
    "user" {
        Push-Location $FrontendRoot
        npm run dev:user
        Pop-Location
    }
}
