@description('Azure region for all resources')
param location string

@description('Environment name (dev, prod)')
param environment string

@description('Short prefix for resource naming')
param appPrefix string

@description('Full database connection URL (postgresql+asyncpg://user:pass@host/db)')
@secure()
param databaseUrl string

@description('Discord bot token')
@secure()
param discordToken string

@description('Discord guild (server) ID')
param discordGuildId string

@description('API key used by backend to call the bot HTTP API')
@secure()
param discordBotApiKey string

@description('API key the bot HTTP API validates on inbound requests')
@secure()
param botApiKey string

@description('Bearer token used by the bot for authenticated calls to the backend')
@secure()
param botServiceToken string

@description('Secret key for signing JWT session cookies')
@secure()
param sessionSecret string

@description('Discord OAuth2 application client ID')
@secure()
param discordClientId string

@description('Discord OAuth2 application client secret')
@secure()
param discordClientSecret string

// Soft-delete retention: minimum is 7 days; Azure default (and recommended for
// prod) is 90 days. This means a deleted vault or secret can be recovered for
// up to retentionDays before it is permanently purged. Use 7 for dev (fast
// teardown), 90 for prod (maximum recovery window).
@description('Number of days to retain soft-deleted vault/secrets (7 for dev, 90 for prod)')
@minValue(7)
@maxValue(90)
param softDeleteRetentionDays int = 7

var vaultName = '${appPrefix}-kv-${environment}-${take(uniqueString(resourceGroup().id), 6)}'

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: vaultName
  location: location
  tags: {
    project: appPrefix
    environment: environment
  }
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    // RBAC authorization is preferred over legacy access policies — it integrates
    // with Azure AD Conditional Access and gives fine-grained per-secret control.
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: softDeleteRetentionDays
    publicNetworkAccess: 'Enabled'
  }
}

resource secretDatabaseUrl 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = {
  parent: keyVault
  name: 'database-url'
  properties: { value: databaseUrl }
}

resource secretDiscordToken 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = {
  parent: keyVault
  name: 'discord-token'
  properties: { value: discordToken }
}

resource secretDiscordGuildId 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = {
  parent: keyVault
  name: 'discord-guild-id'
  properties: { value: discordGuildId }
}

resource secretDiscordBotApiKey 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = {
  parent: keyVault
  name: 'discord-bot-api-key'
  properties: { value: discordBotApiKey }
}

resource secretBotApiKey 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = {
  parent: keyVault
  name: 'bot-api-key'
  properties: { value: botApiKey }
}

resource secretBotServiceToken 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = {
  parent: keyVault
  name: 'bot-service-token'
  properties: { value: botServiceToken }
}

resource secretSessionSecret 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = {
  parent: keyVault
  name: 'session-secret'
  properties: { value: sessionSecret }
}

resource secretDiscordClientId 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = {
  parent: keyVault
  name: 'discord-client-id'
  properties: { value: discordClientId }
}

resource secretDiscordClientSecret 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = {
  parent: keyVault
  name: 'discord-client-secret'
  properties: { value: discordClientSecret }
}

output vaultId string = keyVault.id
output vaultName string = keyVault.name
output vaultUri string = keyVault.properties.vaultUri
output secretDatabaseUrlUri string = secretDatabaseUrl.properties.secretUri
output secretDiscordTokenUri string = secretDiscordToken.properties.secretUri
output secretDiscordBotApiKeyUri string = secretDiscordBotApiKey.properties.secretUri
output secretBotApiKeyUri string = secretBotApiKey.properties.secretUri
output secretBotServiceTokenUri string = secretBotServiceToken.properties.secretUri
