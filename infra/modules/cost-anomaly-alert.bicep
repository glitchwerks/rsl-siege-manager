targetScope = 'subscription'

@description('Short prefix used in the alert name')
param appPrefix string

@description('Email recipient and owner for the anomaly alert')
param alertEmail string

@description('Schedule start in UTC')
param startDate string

@description('Schedule end in UTC; Azure limits the schedule to one year')
param endDate string

// Cost Management InsightAlert is not an Azure Monitor action-group alert. It
// emails directly and covers the entire subscription, including non-Siege spend.
// The built-in anomaly view ID is required for portal visibility.
resource costAnomalyAlert 'Microsoft.CostManagement/scheduledActions@2023-09-01' = {
  name: '${appPrefix}-cost-anomaly'
  scope: subscription()
  kind: 'InsightAlert'
  properties: {
    displayName: 'Siege cost anomaly'
    notification: {
      subject: 'Azure subscription cost anomaly detected'
      to: [alertEmail]
    }
    notificationEmail: alertEmail
    schedule: {
      startDate: startDate
      endDate: endDate
      frequency: 'Daily'
    }
    status: 'Enabled'
    viewId: '${subscription().id}/providers/Microsoft.CostManagement/views/ms:DailyAnomalyByResourceGroup'
  }
}
