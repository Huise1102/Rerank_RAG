param(
    [string]$BackupRoot = "",
    [string]$MysqlHost = "127.0.0.1",
    [int]$MysqlPort = 3307,
    [string]$MysqlUser = "root",
    [string]$MysqlDatabase = "rag_campus"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($BackupRoot)) {
    $BackupRoot = Join-Path $projectRoot "backups"
}
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$target = Join-Path $BackupRoot $timestamp
New-Item -ItemType Directory -Path $target -Force | Out-Null

$password = $env:RAG_DB_PASSWORD
if ([string]::IsNullOrWhiteSpace($password)) {
    $password = $env:MYSQL_PASSWORD
}
if ([string]::IsNullOrWhiteSpace($password)) {
    throw "请先设置 RAG_DB_PASSWORD 或 MYSQL_PASSWORD 环境变量"
}
$env:MYSQL_PWD = $password

$dumpFile = Join-Path $target "$MysqlDatabase.sql"
$mysqldump = Get-Command mysqldump -ErrorAction SilentlyContinue
if ($null -eq $mysqldump) {
    Write-Warning "未找到 mysqldump，跳过数据库导出，请手工备份 MySQL 数据目录"
} else {
    & $mysqldump.Source --host=$MysqlHost --port=$MysqlPort --user=$MysqlUser `
        --single-transaction --routines --triggers $MysqlDatabase > $dumpFile
    if ($LASTEXITCODE -ne 0) {
        throw "mysqldump 执行失败"
    }
}

foreach ($name in @("storage", "indexes")) {
    $source = Join-Path $projectRoot $name
    if (Test-Path $source) {
        $destination = Join-Path $target $name
        Copy-Item -Path $source -Destination $destination -Recurse -Force
    }
}

$readme = @(
    "备份时间：$timestamp"
    "数据库：$MysqlDatabase（${MysqlHost}:${MysqlPort}）"
    "数据库文件：$MysqlDatabase.sql"
    "原始文档：storage/"
    "索引目录：indexes/"
    ""
    "恢复步骤："
    "1. 停止后端服务。"
    "2. 将 storage、indexes 覆盖回项目根目录。"
    "3. 执行 mysql -h $MysqlHost -P $MysqlPort -u $MysqlUser -p $MysqlDatabase < $MysqlDatabase.sql"
    "4. 启动后端，访问 /health 确认索引一致性。"
    "5. 如索引损坏，使用管理员账号触发知识库重建。"
) -join [Environment]::NewLine
$readme | Set-Content -Path (Join-Path $target "RESTORE.txt") -Encoding UTF8
Remove-Item Env:MYSQL_PWD -ErrorAction SilentlyContinue
Write-Output "备份完成：$target"
