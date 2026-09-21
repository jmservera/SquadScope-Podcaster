targetScope = 'resourceGroup'

param location string
param logAnalyticsWorkspaceId string
param actionGroupResourceId string = ''
param enabled bool = true

var alertDefinitions = [
  {
    name: 'distribution-pending-age'
    metric: 'distribution_pending_age_seconds'
    severity: 2
    route: 'operations'
  }
  {
    name: 'distribution-provider-unknown'
    metric: 'distribution_provider_unknown'
    severity: 0
    route: 'production-owner'
  }
  {
    name: 'distribution-claim-latency'
    metric: 'distribution_claim_latency_seconds'
    severity: 2
    route: 'operations'
  }
  {
    name: 'distribution-lease-loss'
    metric: 'distribution_lease_loss'
    severity: 0
    route: 'production-owner'
  }
  {
    name: 'distribution-manual-handoff'
    metric: 'distribution_manual_handoff'
    severity: 2
    route: 'publication-operator'
  }
  {
    name: 'distribution-youtube-non-public'
    metric: 'distribution_youtube_non_public'
    severity: 1
    route: 'production-owner'
  }
  {
    name: 'distribution-spotify-draft'
    metric: 'distribution_spotify_draft'
    severity: 2
    route: 'publication-operator'
  }
  {
    name: 'distribution-public-verification-lag'
    metric: 'distribution_public_verification_lag_seconds'
    severity: 1
    route: 'operations'
  }
  {
    name: 'distribution-poisoned'
    metric: 'distribution_poisoned'
    severity: 0
    route: 'production-owner'
  }
]

resource distributionAlerts 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = [
  for alert in alertDefinitions: if (enabled) {
    name: alert.name
    location: location
    properties: {
      displayName: alert.name
      description: 'Provider terminal-truth alert. Route: ${alert.route}. Runbook: docs/ops/distribution-terminal-truth.md'
      severity: alert.severity
      enabled: true
      evaluationFrequency: 'PT5M'
      windowSize: 'PT10M'
      scopes: [
        logAnalyticsWorkspaceId
      ]
      criteria: {
        allOf: [
          {
            query: 'ContainerAppConsoleLogs_CL | where Log_s contains "distribution_signal" | where Log_s contains "\\"metric\\": \\"${alert.metric}\\"" | where Log_s contains "\\"severity\\": \\"critical\\""'
            timeAggregation: 'Count'
            operator: 'GreaterThan'
            threshold: 0
            failingPeriods: {
              numberOfEvaluationPeriods: 1
              minFailingPeriodsToAlert: 1
            }
          }
        ]
      }
      autoMitigate: true
      checkWorkspaceAlertsStorageConfigured: false
      skipQueryValidation: false
      actions: {
        actionGroups: empty(actionGroupResourceId) ? [] : [
          actionGroupResourceId
        ]
      }
    }
  }
]
