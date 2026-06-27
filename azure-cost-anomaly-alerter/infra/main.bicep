// ─────────────────────────────────────────────────────────────────────────────
// cloud-cost-anomaly-alerter — infrastructure
// Deploys: Storage Account, App Service Plan, Function App
// Region:  UK South (data residency)
// ─────────────────────────────────────────────────────────────────────────────

@description('Base name used for all resources')
param baseName string = 'costanomalyalerter'

@description('Azure region for all resources')
param location string = 'uksouth'

@description('Environment tag')
@allowed(['dev', 'staging', 'prod'])
param environment string = 'prod'

@description('Your Azure Subscription ID — used by the function to query Cost Management')
param subscriptionId string

@description('Optional Slack or Teams webhook URL for anomaly alerts')
param alertWebhookUrl string = ''

@description('Spend multiplier above 7-day average to trigger an anomaly alert')
param anomalyThreshold string = '1.8'

var uniqueSuffix     = uniqueString(resourceGroup().id)
var storageAccName   = 'st${replace(baseName, '-', '')}${uniqueSuffix}'
var functionAppName  = 'func-${baseName}-${uniqueSuffix}'
var hostingPlanName  = 'asp-${baseName}-${uniqueSuffix}'
var containerName    = 'dashboarddata'

var commonTags = {
  Environment:  environment
  Project:      'cloud-cost-anomaly-alerter'
  ManagedBy:    'Bicep'
  CostCentre:   'Engineering'
}

// ─── Storage Account ──────────────────────────────────────────────────────────
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name:     storageAccName
  location: location
  tags:     commonTags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion:        'TLS1_2'
    allowBlobPublicAccess:    true   // Required: dashboard reads spend.json publicly
    supportsHttpsTrafficOnly: true
    accessTier:               'Hot'
    encryption: {
      services: {
        blob: { enabled: true }
        file: { enabled: true }
      }
      keySource: 'Microsoft.Storage'
    }
  }
}

// ─── Blob container for dashboard data ───────────────────────────────────────
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storageAccount
  name:   'default'
}

resource dashboardContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name:   containerName
  properties: {
    publicAccess: 'Blob'  // spend.json is read by the public static dashboard
  }
}

// ─── Consumption App Service Plan (serverless — free tier) ───────────────────
resource hostingPlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name:     hostingPlanName
  location: location
  tags:     commonTags
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  properties: {
    reserved: true  // Linux
  }
}

// ─── Function App ─────────────────────────────────────────────────────────────
resource functionApp 'Microsoft.Web/sites@2023-01-01' = {
  name:     functionAppName
  location: location
  tags:     commonTags
  kind:     'functionapp,linux'
  identity: {
    type: 'SystemAssigned'  // Managed identity — no stored credentials
  }
  properties: {
    serverFarmId: hostingPlan.id
    httpsOnly:    true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      appSettings: [
        { name: 'AzureWebJobsStorage',      value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=core.windows.net' }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME',    value: 'python' }
        { name: 'AZURE_SUBSCRIPTION_ID',       value: subscriptionId }
        { name: 'AZURE_STORAGE_CONN_STR',      value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=core.windows.net' }
        { name: 'STORAGE_CONTAINER',           value: containerName }
        { name: 'ANOMALY_THRESHOLD',           value: anomalyThreshold }
        { name: 'ALERT_WEBHOOK_URL',           value: alertWebhookUrl }
      ]
      ftpsState:           'Disabled'
      minTlsVersion:       '1.2'
      use32BitWorkerProcess: false
    }
  }
}

// ─── RBAC: Grant Function App identity Cost Management Reader ─────────────────
// This follows least-privilege — the function only needs to read cost data,
// not manage any resources.
var costManagementReaderRoleId = 'e56962a6-4747-49cd-b67b-bf8b01975c4f'

resource costReaderAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name:  guid(functionApp.id, costManagementReaderRoleId, subscription().subscriptionId)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', costManagementReaderRoleId)
    principalId:      functionApp.identity.principalId
    principalType:    'ServicePrincipal'
    description:      'Grants the Cost Anomaly Alerter function read access to Cost Management data. Least-privilege — read only.'
  }
}

// ─── Outputs ──────────────────────────────────────────────────────────────────
output functionAppName     string = functionApp.name
output functionAppUrl      string = 'https://${functionApp.properties.defaultHostName}'
output storageAccountName  string = storageAccount.name
output spendJsonUrl        string = 'https://${storageAccount.name}.blob.core.windows.net/${containerName}/spend.json'
output managedIdentityId   string = functionApp.identity.principalId
