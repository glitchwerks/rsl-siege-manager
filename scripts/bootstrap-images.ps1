# Usage:
#   .\bootstrap-images.ps1 -Env dev  [-EnvFile .env.deploy.dev]
#   .\bootstrap-images.ps1 -Env prod [-EnvFile .env.deploy.prod]

param(
    [Parameter(Mandatory = $true)]
    [string]$Env,

    [Parameter(Mandatory = $false)]
    [string]$EnvFile = '.env.deploy.prod'
)

$ErrorActionPreference = 'Stop'

$SecretsLoaded = $false
$EnvDeployPath = Join-Path $PSScriptRoot $EnvFile
if (Test-Path $EnvDeployPath) {
    Get-Content $EnvDeployPath | Where-Object { $_ -notmatch '^\s*#' -and $_ -match '=' } | ForEach-Object {
        $parts = $_ -split '=', 2
        [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim())
    }
    $SecretsLoaded = $true
    Write-Host "==> Loaded secrets from $EnvFile"
}
else {
    Write-Host "    (no $EnvFile found - secrets must be set in the environment)" -ForegroundColor Yellow
}

$ImageTag = git -C $PSScriptRoot rev-parse --short HEAD

if ($Env -notin @('dev', 'prod')) {
    Write-Host "ERROR: -Env must be 'dev' or 'prod'." -ForegroundColor Red
    Write-Host ""
    Write-Host "Usage:"
    Write-Host "  .\bootstrap-images.ps1 -Env dev"
    Write-Host "  .\bootstrap-images.ps1 -Env prod"
    exit 1
}

$ResourceGroup = if ($Env -eq 'dev') { 'siege-web-dev' } else { 'siege-web-prod' }
$ParamFile = if ($Env -eq 'dev') { 'infra/main.dev.bicepparam' } else { 'infra/main.prod.bicepparam' }

Write-Host "==> Looking up ACR login server in resource group $ResourceGroup..."
$Registry = az acr list --resource-group $ResourceGroup --query "[0].loginServer" -o tsv

if (-not $Registry) {
    Write-Error "No ACR found in resource group $ResourceGroup. Ensure the Bicep deployment has completed and you are logged in to the correct Azure subscription."
    exit 1
}

Write-Host "    Registry:  $Registry"
Write-Host "==> Image tag: $ImageTag"

$Images = @(
    @{ Name = 'siege-api'; Context = './backend' },
    @{ Name = 'siege-frontend'; Context = './frontend' },
    @{ Name = 'siege-bot'; Context = './bot' }
)

Write-Host '==> Logging in to Azure Container Registry...'
az acr login --name $Registry

foreach ($Image in $Images) {
    $Tag = "$Registry/$($Image.Name):$ImageTag"
    $LatestTag = "$Registry/$($Image.Name):latest"

    Write-Host ""
    Write-Host "==> Building $Tag from $($Image.Context)..."
    docker build -t $Tag $Image.Context

    Write-Host "==> Pushing $Tag..."
    docker push $Tag

    Write-Host "==> Tagging and pushing $LatestTag..."
    docker tag $Tag $LatestTag
    docker push $LatestTag
}

Write-Host ""
Write-Host "==> All images pushed successfully."
Write-Host ""
if ($SecretsLoaded) {
    Write-Host "==> Secrets loaded from $EnvFile" -ForegroundColor Green
}
else {
    Write-Host "    WARNING: Before running the deploy command below, set these environment variables" -ForegroundColor Yellow
    Write-Host "    or the deployment will proceed with blank secrets:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "        `$env:PG_ADMIN_PASSWORD    = '...'" -ForegroundColor Cyan
    Write-Host "        `$env:DISCORD_TOKEN        = '...'" -ForegroundColor Cyan
    Write-Host "        `$env:DISCORD_BOT_API_KEY  = '...'" -ForegroundColor Cyan
    Write-Host "        `$env:BOT_API_KEY          = '...'" -ForegroundColor Cyan
    Write-Host "        `$env:BOT_SERVICE_TOKEN    = '...'" -ForegroundColor Cyan
    Write-Host "        `$env:DISCORD_GUILD_ID     = '...'" -ForegroundColor Cyan
    Write-Host "        `$env:SLACK_ALERT_WEBHOOK_URL = '...'" -ForegroundColor Cyan
    Write-Host ""
}
Write-Host "    Deploy:" -ForegroundColor Yellow
Write-Host "        az deployment group create ``" -ForegroundColor White
Write-Host "            --resource-group $ResourceGroup ``" -ForegroundColor White
Write-Host "            --template-file infra/main.bicep ``" -ForegroundColor White
Write-Host "            --parameters $ParamFile ``" -ForegroundColor White
Write-Host "            --parameters imageTag=$ImageTag ``" -ForegroundColor White
Write-Host "            --parameters postgresAdminPassword=`$env:PG_ADMIN_PASSWORD ``" -ForegroundColor White
Write-Host "                         discordToken=`$env:DISCORD_TOKEN ``" -ForegroundColor White
Write-Host "                         discordBotApiKey=`$env:DISCORD_BOT_API_KEY ``" -ForegroundColor White
Write-Host "                         botApiKey=`$env:BOT_API_KEY ``" -ForegroundColor White
Write-Host "                         botServiceToken=`$env:BOT_SERVICE_TOKEN ``" -ForegroundColor White
Write-Host "                         discordGuildId=`$env:DISCORD_GUILD_ID ``" -ForegroundColor White
Write-Host "                         slackAlertWebhookUrl=`$env:SLACK_ALERT_WEBHOOK_URL" -ForegroundColor White

if ($SecretsLoaded) {
    Write-Host ""
    Write-Host "==> Deploying Bicep template..." -ForegroundColor Cyan
    az deployment group create `
        --resource-group $ResourceGroup `
        --template-file infra/main.bicep `
        --parameters $ParamFile `
        --parameters imageTag=$ImageTag `
        --parameters postgresAdminPassword=$env:PG_ADMIN_PASSWORD `
        discordToken=$env:DISCORD_TOKEN `
        discordBotApiKey=$env:DISCORD_BOT_API_KEY `
        botApiKey=$env:BOT_API_KEY `
        botServiceToken=$env:BOT_SERVICE_TOKEN `
        discordGuildId=$env:DISCORD_GUILD_ID `
        slackAlertWebhookUrl=$env:SLACK_ALERT_WEBHOOK_URL
    if ($LASTEXITCODE -ne 0) {
        Write-Host "==> Deployment failed (exit code $LASTEXITCODE)." -ForegroundColor Red
        exit 1
    }
    Write-Host "==> Deployment complete." -ForegroundColor Green
}
