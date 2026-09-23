targetScope = 'resourceGroup'

param location string
param logAnalyticsWorkspaceId string
param operationsActionGroupResourceId string = ''
param upstreamActionGroupResourceId string = ''
param operatorActionGroupResourceId string = ''
param productionActionGroupResourceId string = ''
param enabled bool = true

var routeActionGroups = {
  operations: operationsActionGroupResourceId
  'upstream-dispatch-owner': upstreamActionGroupResourceId
  'publication-operator': operatorActionGroupResourceId
  'production-owner': productionActionGroupResourceId
}

// The application emits the reviewed warning/critical classification from durable
// state. These rules preserve the matching evaluation window, explicit route, and
// missing-data contract in deployable infrastructure.
var alertDefinitions = [
  {
    name: 'distribution-pending-age-warning'
    event: 'distribution_provider_state'
    metric: 'distribution_pending_age_seconds'
    signalSeverity: 'warning'
    severity: 2
    frequency: 'PT5M'
    window: 'PT10M'
    route: 'operations'
    missingData: 'healthy only when authoritative outbox depth is zero'
  }
  {
    name: 'distribution-pending-age-critical'
    event: 'distribution_provider_state'
    metric: 'distribution_pending_age_seconds'
    signalSeverity: 'critical'
    severity: 0
    frequency: 'PT5M'
    window: 'PT5M'
    route: 'production-owner'
    missingData: 'healthy only when authoritative outbox depth is zero'
  }
  {
    name: 'dispatch-missing-azure-arrival-warning'
    event: 'dispatch_arrival_state'
    metric: 'dispatch_missing_azure_arrival_seconds'
    signalSeverity: 'warning'
    severity: 2
    frequency: 'PT5M'
    window: 'PT10M'
    route: 'upstream-dispatch-owner'
    missingData: 'missing upstream intent telemetry warns'
  }
  {
    name: 'dispatch-missing-azure-arrival-critical'
    event: 'dispatch_arrival_state'
    metric: 'dispatch_missing_azure_arrival_seconds'
    signalSeverity: 'critical'
    severity: 0
    frequency: 'PT5M'
    window: 'PT5M'
    route: 'production-owner'
    missingData: 'missing upstream intent telemetry warns'
  }
  {
    name: 'dispatch-telemetry-missing'
    event: 'dispatch_arrival_state'
    metric: 'dispatch_telemetry_missing'
    signalSeverity: 'warning'
    severity: 2
    frequency: 'PT5M'
    window: 'PT10M'
    route: 'upstream-dispatch-owner'
    missingData: 'absence is warning when a scheduled publication is expected'
  }
  {
    name: 'distribution-provider-unknown'
    event: 'distribution_provider_state'
    metric: 'distribution_provider_unknown'
    signalSeverity: 'critical'
    severity: 0
    frequency: 'PT5M'
    window: 'PT5M'
    route: 'production-owner'
    missingData: 'missing state metric with active outbox warns'
  }
  {
    name: 'distribution-claim-latency-warning'
    event: 'distribution_provider_state'
    metric: 'distribution_claim_latency_seconds'
    signalSeverity: 'warning'
    severity: 2
    frequency: 'PT5M'
    window: 'PT15M'
    route: 'operations'
    missingData: 'missing heartbeat with active claim is critical'
  }
  {
    name: 'distribution-lease-loss'
    event: 'distribution_provider_state'
    metric: 'distribution_lease_loss'
    signalSeverity: 'critical'
    severity: 0
    frequency: 'PT5M'
    window: 'PT5M'
    route: 'production-owner'
    missingData: 'missing heartbeat with active claim is critical'
  }
  {
    name: 'distribution-manual-handoff-warning'
    event: 'distribution_provider_state'
    metric: 'distribution_manual_handoff'
    signalSeverity: 'warning'
    severity: 2
    frequency: 'PT5M'
    window: 'PT5M'
    route: 'publication-operator'
    missingData: 'missing state metric with known manual records warns'
  }
  {
    name: 'distribution-manual-handoff-critical'
    event: 'distribution_provider_state'
    metric: 'distribution_manual_handoff'
    signalSeverity: 'critical'
    severity: 0
    frequency: 'PT5M'
    window: 'PT5M'
    route: 'production-owner'
    missingData: 'missing state metric with known manual records warns'
  }
  {
    name: 'distribution-youtube-non-public-warning'
    event: 'distribution_provider_state'
    metric: 'distribution_youtube_non_public'
    signalSeverity: 'warning'
    severity: 2
    frequency: 'PT5M'
    window: 'PT10M'
    route: 'operations'
    missingData: 'missing state metric with active YouTube objective warns'
  }
  {
    name: 'distribution-youtube-non-public-critical'
    event: 'distribution_provider_state'
    metric: 'distribution_youtube_non_public'
    signalSeverity: 'critical'
    severity: 0
    frequency: 'PT5M'
    window: 'PT5M'
    route: 'production-owner'
    missingData: 'missing state metric with active YouTube objective warns'
  }
  {
    name: 'distribution-spotify-draft-warning'
    event: 'distribution_provider_state'
    metric: 'distribution_spotify_draft'
    signalSeverity: 'warning'
    severity: 2
    frequency: 'PT5M'
    window: 'PT10M'
    route: 'publication-operator'
    missingData: 'missing state metric with active Spotify objective warns'
  }
  {
    name: 'distribution-spotify-draft-critical'
    event: 'distribution_provider_state'
    metric: 'distribution_spotify_draft'
    signalSeverity: 'critical'
    severity: 0
    frequency: 'PT5M'
    window: 'PT5M'
    route: 'production-owner'
    missingData: 'missing state metric with active Spotify objective warns'
  }
  {
    name: 'distribution-public-verification-lag-warning'
    event: 'distribution_provider_state'
    metric: 'distribution_public_verification_lag_seconds'
    signalSeverity: 'warning'
    severity: 2
    frequency: 'PT5M'
    window: 'PT10M'
    route: 'operations'
    missingData: 'missing verification telemetry with consumed intent warns'
  }
  {
    name: 'distribution-public-verification-lag-critical'
    event: 'distribution_provider_state'
    metric: 'distribution_public_verification_lag_seconds'
    signalSeverity: 'critical'
    severity: 0
    frequency: 'PT5M'
    window: 'PT5M'
    route: 'production-owner'
    missingData: 'missing verification telemetry with consumed intent warns'
  }
  {
    name: 'distribution-poisoned'
    event: 'distribution_provider_state'
    metric: 'distribution_poisoned'
    signalSeverity: 'critical'
    severity: 0
    frequency: 'PT5M'
    window: 'PT5M'
    route: 'production-owner'
    missingData: 'missing state metric with known poison record warns'
  }
  {
    name: 'distribution-identity-conflict'
    event: 'distribution_provider_state'
    metric: 'distribution_identity_conflict'
    signalSeverity: 'critical'
    severity: 0
    frequency: 'PT5M'
    window: 'PT5M'
    route: 'production-owner'
    missingData: 'weekly decision telemetry is required while an active publication exists'
  }
  {
    name: 'distribution-weekly-non-green'
    event: 'distribution_provider_state'
    metric: 'distribution_weekly_non_green'
    signalSeverity: 'critical'
    severity: 0
    frequency: 'PT5M'
    window: 'PT5M'
    route: 'production-owner'
    missingData: 'weekly decision telemetry is required while an active publication exists'
  }
  {
    name: 'distribution-scheduler-telemetry-missing'
    event: 'distribution_scheduler_state'
    metric: 'distribution_scheduler_heartbeat'
    signalSeverity: 'info'
    severity: 0
    frequency: 'PT5M'
    window: 'PT15M'
    route: 'production-owner'
    missingData: 'absence of the authoritative scheduler heartbeat is critical'
  }
  {
    name: 'distribution-active-depth-without-state'
    event: 'distribution_scheduler_state'
    metric: 'distribution_active_outbox_depth'
    signalSeverity: 'warning'
    severity: 2
    frequency: 'PT5M'
    window: 'PT10M'
    route: 'operations'
    missingData: 'authoritative depth is emitted by every scheduler run'
  }
  {
    name: 'distribution-active-claim-heartbeat-missing'
    event: 'distribution_scheduler_state'
    metric: 'distribution_claim_heartbeat_missing'
    signalSeverity: 'critical'
    severity: 0
    frequency: 'PT5M'
    window: 'PT5M'
    route: 'production-owner'
    missingData: 'authoritative claim scan emits a row when a heartbeat is overdue'
  }
]

resource distributionAlerts 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = [
  for alert in alertDefinitions: if (enabled) {
    name: alert.name
    location: location
    properties: {
      displayName: alert.name
      description: 'Terminal-truth alert. Route: ${alert.route}. Missing data: ${alert.missingData}. Runbook: docs/ops/distribution-terminal-truth.md'
      severity: alert.severity
      enabled: true
      evaluationFrequency: alert.frequency
      windowSize: alert.window
      scopes: [
        logAnalyticsWorkspaceId
      ]
      criteria: {
        allOf: [
          {
            query: alert.name == 'distribution-scheduler-telemetry-missing' ? 'let observed = toscalar(ContainerAppConsoleLogs_CL | where TimeGenerated > ago(15m) | where Log_s contains "\\"event\\": \\"distribution_scheduler_state\\"" and Log_s contains "\\"metric\\": \\"distribution_scheduler_heartbeat\\"" | count); print observed | where observed == 0' : alert.name == 'distribution-active-depth-without-state' ? 'let activeDepth = toscalar(ContainerAppConsoleLogs_CL | where TimeGenerated > ago(10m) | where Log_s contains "\\"event\\": \\"distribution_scheduler_state\\"" and Log_s contains "\\"metric\\": \\"distribution_active_outbox_depth\\"" and Log_s contains "\\"state\\": \\"active\\"" | count); let stateRows = toscalar(ContainerAppConsoleLogs_CL | where TimeGenerated > ago(10m) | where Log_s contains "\\"event\\": \\"distribution_provider_state\\"" | count); print activeDepth, stateRows | where activeDepth > 0 and stateRows == 0' : 'ContainerAppConsoleLogs_CL | where Log_s contains "\\"event\\": \\"${alert.event}\\"" | where Log_s contains "\\"metric\\": \\"${alert.metric}\\"" | where Log_s contains "\\"severity\\": \\"${alert.signalSeverity}\\""'
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
        actionGroups: empty(routeActionGroups[alert.route]) ? [] : [
          routeActionGroups[alert.route]
        ]
      }
    }
  }
]
