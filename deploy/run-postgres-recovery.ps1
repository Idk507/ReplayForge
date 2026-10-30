$ErrorActionPreference = 'Stop'

$subscription = 'c07a4525-9f83-401f-92b2-7f383d99d426'
$resourceGroup = 'replayforge-fullstack-rg'
$jobName = 'rf-db-grants-20261030'
$apiVersion = '2026-07-01'
$workspaceId = '2e6c8676-8b35-471a-a00c-cf92eb35d330'
$containerAppsEnvId = "/subscriptions/$subscription/resourceGroups/$resourceGroup/providers/Microsoft.App/managedEnvironments/rf-fullstack-env"
$serverHost = 'replayforge-pg-20261029-sea.postgres.database.azure.com'
$image = 'replayforgefullstack2026.azurecr.io/replayforge-api@sha256:6776320f431b4aaf194cad3042ff7761f0ceffaf2a151100ba7fb2cfa9d132b2'
$acrIdentity = "/subscriptions/$subscription/resourceGroups/$resourceGroup/providers/Microsoft.ManagedIdentity/userAssignedIdentities/rf-fullstack-acr-pull"
$protectedRoot = Join-Path $env:LOCALAPPDATA 'ReplayForge\deployment-20261029'
$journalPath = Join-Path (Get-Location) 'docs/evidence/azure-deployment-operation-journal-20261029.jsonl'
$scriptPath = Join-Path (Get-Location) 'deploy/postgres_recovery.py'
$operationId = [guid]::NewGuid().ToString('N')
$script:armToken = $null

function Add-JournalEvent([hashtable]$event) {
    $event.timestampUtc = [DateTime]::UtcNow.ToString('o')
    $json = $event | ConvertTo-Json -Compress -Depth 20
    [System.IO.File]::AppendAllText(
        $journalPath,
        $json + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )
}

function Get-PlaintextSecret([string]$fileName) {
    $file = Join-Path $protectedRoot $fileName
    $cipher = [System.IO.File]::ReadAllText($file).Trim()
    $secure = ConvertTo-SecureString -String $cipher -ErrorAction Stop
    $handle = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($handle)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($handle)
        $secure.Dispose()
        $cipher = $null
    }
}

function Invoke-Arm([string]$method, [string]$uri, [string]$body = $null) {
    if (-not $script:armToken) {
        $script:armToken = (az account get-access-token --resource https://management.azure.com/ --query accessToken -o tsv 2>$null | Select-Object -First 1).Trim()
    }
    if (-not $script:armToken) { throw 'ARM_TOKEN_UNAVAILABLE' }
    $client = [System.Net.Http.HttpClient]::new()
    $client.Timeout = [TimeSpan]::FromSeconds(90)
    $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::new($method), [Uri]$uri)
    $request.Headers.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $script:armToken)
    if ($null -ne $body) {
        $request.Content = [System.Net.Http.StringContent]::new($body, [Text.Encoding]::UTF8, 'application/json')
    }
    try {
        $response = $client.SendAsync($request).GetAwaiter().GetResult()
        $content = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        $requestId = ''
        foreach ($header in @('x-ms-request-id', 'x-ms-correlation-request-id')) {
            if ($response.Headers.Contains($header)) {
                $requestId = $response.Headers.GetValues($header) | Select-Object -First 1
                break
            }
        }
        return [pscustomobject]@{
            status = [int]$response.StatusCode
            requestId = $requestId
            content = $content
        }
    }
    finally {
        if ($request) { $request.Dispose() }
        if ($response) { $response.Dispose() }
        $client.Dispose()
    }
}

function Get-JobUri {
    return "https://management.azure.com/subscriptions/$subscription/resourceGroups/$resourceGroup/providers/Microsoft.App/jobs/$jobName`?api-version=$apiVersion"
}

function Get-ExecutionUri {
    return "https://management.azure.com/subscriptions/$subscription/resourceGroups/$resourceGroup/providers/Microsoft.App/jobs/$jobName/executions`?api-version=$apiVersion"
}

$adminPassword = $null
$migratorPassword = $null
$apiPassword = $null
$authPassword = $null
$requestBody = $null
$containerCommand = $null
$sourceB64 = $null

try {
    $account = az account show --query "{id:id,state:state}" -o json --only-show-errors | ConvertFrom-Json
    if ($account.id -ne $subscription -or $account.state -ne 'Enabled') {
        throw 'AZURE_SUBSCRIPTION_MISMATCH'
    }
    $logsState = az resource show --ids $containerAppsEnvId --api-version $apiVersion --query "{state:properties.provisioningState,destination:properties.appLogsConfiguration.destination,customerId:properties.appLogsConfiguration.logAnalyticsConfiguration.customerId}" -o json --only-show-errors | ConvertFrom-Json
    if ($logsState.state -ne 'Succeeded' -or $logsState.destination -ne 'log-analytics' -or $logsState.customerId -ne $workspaceId) {
        throw 'LOG_WORKSPACE_NOT_READY'
    }
    Add-JournalEvent @{
        event = 'execution_reconciled'
        operationId = '4cc105db88a04b76b2e815e08c88bd09'
        action = 'attach_existing_log_analytics_workspace_to_aca_environment'
        state = $logsState.state
        destination = $logsState.destination
        customerId = $workspaceId
        logSchemaTable = 'ContainerAppConsoleLogs'
        jobNameColumn = 'JobName'
        payloadColumn = 'Log'
        sharedKeyLogged = $false
    }

    $jobUri = Get-JobUri
    $existing = Invoke-Arm 'GET' $jobUri
    if ($existing.status -ne 404) { throw 'RECOVERY_JOB_NAME_ALREADY_EXISTS_NO_CREATE' }

    $adminPassword = Get-PlaintextSecret 'pg-admin.dpapi'
    $migratorPassword = Get-PlaintextSecret 'rf-migrator-password.dpapi'
    $apiPassword = Get-PlaintextSecret 'rf-api-password.dpapi'
    $authPassword = Get-PlaintextSecret 'rf-auth-password.dpapi'
    $source = [System.IO.File]::ReadAllText($scriptPath)
    $sourceBytes = [Text.Encoding]::UTF8.GetBytes($source)
    $scriptHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($sourceBytes)).ToLowerInvariant()
    $sourceB64 = [Convert]::ToBase64String($sourceBytes)
    $containerCommand = "exec(__import__('base64').b64decode('$sourceB64').decode('utf-8'))"

    $intentShape = [ordered]@{
        jobName = $jobName
        image = $image
        scriptSha256 = $scriptHash
        database = 'replayforge_demo'
        host = $serverHost
        migrationTarget = '0036'
        cpu = 1
        memory = '2Gi'
        retryLimit = 0
        timeoutSeconds = 1500
        parallelism = 1
        completionCount = 1
        secrets = @('pg-admin-password', 'rf-migrator-password', 'rf-api-password', 'rf-auth-password')
        startCount = 1
    }
    $intentJson = $intentShape | ConvertTo-Json -Compress -Depth 10
    $requestDigest = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($intentJson))).ToLowerInvariant()

    $job = @{
        location = 'Central India'
        identity = @{
            type = 'UserAssigned'
            userAssignedIdentities = @{ $acrIdentity = @{} }
        }
        properties = @{
            environmentId = $containerAppsEnvId
            configuration = @{
                triggerType = 'Manual'
                replicaRetryLimit = 0
                replicaTimeout = 1500
                manualTriggerConfig = @{ replicaCompletionCount = 1; parallelism = 1 }
                registries = @(@{ server = 'replayforgefullstack2026.azurecr.io'; identity = $acrIdentity })
                secrets = @(
                    @{ name = 'pg-admin-password'; value = $adminPassword },
                    @{ name = 'rf-migrator-password'; value = $migratorPassword },
                    @{ name = 'rf-api-password'; value = $apiPassword },
                    @{ name = 'rf-auth-password'; value = $authPassword }
                )
            }
            template = @{
                containers = @(@{
                    name = 'recovery'
                    image = $image
                    command = @('python')
                    args = @('-c', $containerCommand)
                    resources = @{ cpu = 1; memory = '2Gi' }
                    env = @(
                        @{ name = 'PG_HOST'; value = $serverHost },
                        @{ name = 'PG_ADMIN_PASSWORD'; secretRef = 'pg-admin-password' },
                        @{ name = 'RF_MIGRATOR_PASSWORD'; secretRef = 'rf-migrator-password' },
                        @{ name = 'RF_API_PASSWORD'; secretRef = 'rf-api-password' },
                        @{ name = 'RF_AUTH_PASSWORD'; secretRef = 'rf-auth-password' }
                    )
                })
            }
        }
    }
    $requestBody = $job | ConvertTo-Json -Depth 30 -Compress

    Add-JournalEvent @{
        event = 'submission_intent'
        operationId = $operationId
        action = 'create_private_postgres_recovery_job'
        jobName = $jobName
        requestDigest = $requestDigest
        scriptSha256 = $scriptHash
        configuration = $intentShape
        secretValuesLogged = $false
        retryLimit = 0
    }

    $create = $null
    try {
        $create = Invoke-Arm 'PUT' $jobUri $requestBody
    }
    catch {
        Add-JournalEvent @{
            event = 'provider_result'
            operationId = $operationId
            action = 'create_private_postgres_recovery_job'
            sendExceptionClass = $_.Exception.GetType().Name
            httpStatus = $null
            automaticRetry = $false
            reconciliation = 'GET_ONLY'
        }
    }
    if ($create) {
        Add-JournalEvent @{
            event = 'provider_result'
            operationId = $operationId
            action = 'create_private_postgres_recovery_job'
            httpStatus = $create.status
            requestId = $create.requestId
            automaticRetry = $false
            responseBodyLogged = $false
        }
        if ($create.status -notin 200, 201, 202) { throw 'RECOVERY_JOB_CREATE_FAILED_NO_RETRY' }
    }

    $jobReady = $false
    for ($i = 0; $i -lt 24; $i++) {
        $observed = Invoke-Arm 'GET' $jobUri
        if ($observed.status -eq 200) {
            $resource = $observed.content | ConvertFrom-Json
            $container = $resource.properties.template.containers[0]
            $trigger = $resource.properties.configuration
            $envByName = @{}
            foreach ($item in @($container.env)) { $envByName[$item.name] = $item }
            $secretNames = @($trigger.secrets | ForEach-Object { $_.name })
            $registry = $trigger.registries | Select-Object -First 1
            $exactMatch = (
                $resource.properties.environmentId -eq $containerAppsEnvId -and
                $resource.location -eq 'Central India' -and
                (@($resource.identity.userAssignedIdentities.PSObject.Properties.Name) -contains $acrIdentity) -and
                $registry.server -eq 'replayforgefullstack2026.azurecr.io' -and
                $registry.identity -eq $acrIdentity -and
                $container.image -eq $image -and
                @($container.command).Count -eq 1 -and $container.command[0] -eq 'python' -and
                @($container.args).Count -eq 2 -and $container.args[0] -eq '-c' -and
                $container.args[1] -eq $containerCommand -and
                [double]$container.resources.cpu -eq 1 -and
                $container.resources.memory -eq '2Gi' -and
                $trigger.triggerType -eq 'Manual' -and
                [int]$trigger.replicaRetryLimit -eq 0 -and
                [int]$trigger.replicaTimeout -eq 1500 -and
                [int]$trigger.manualTriggerConfig.parallelism -eq 1 -and
                [int]$trigger.manualTriggerConfig.replicaCompletionCount -eq 1 -and
                $envByName['PG_HOST'].value -eq $serverHost -and
                $envByName['PG_ADMIN_PASSWORD'].secretRef -eq 'pg-admin-password' -and
                $envByName['RF_MIGRATOR_PASSWORD'].secretRef -eq 'rf-migrator-password' -and
                $envByName['RF_API_PASSWORD'].secretRef -eq 'rf-api-password' -and
                $envByName['RF_AUTH_PASSWORD'].secretRef -eq 'rf-auth-password' -and
                ((@($secretNames | Sort-Object)) -join ',') -eq 'pg-admin-password,rf-api-password,rf-auth-password,rf-migrator-password'
            )
            if (-not $exactMatch) { throw 'RECOVERY_JOB_CONFIG_MISMATCH_NO_START' }
            $jobReady = $resource.properties.provisioningState -eq 'Succeeded'
            if ($resource.properties.provisioningState -eq 'Failed') { throw 'RECOVERY_JOB_PROVISIONING_FAILED' }
            if ($jobReady) { break }
        }
        Start-Sleep -Seconds 5
    }
    if (-not $jobReady) { throw 'RECOVERY_JOB_PROVISIONING_UNCERTAIN_NO_START' }

    $executionList = Invoke-Arm 'GET' (Get-ExecutionUri)
    if ($executionList.status -ne 200) { throw 'RECOVERY_EXECUTION_LIST_UNAVAILABLE_NO_START' }
    $executions = ($executionList.content | ConvertFrom-Json).value
    if (@($executions).Count -ne 0) { throw 'RECOVERY_EXECUTION_ALREADY_EXISTS_NO_START' }

    $startOperationId = [guid]::NewGuid().ToString('N')
    Add-JournalEvent @{
        event = 'submission_intent'
        operationId = $startOperationId
        action = 'start_private_postgres_recovery_job'
        jobName = $jobName
        createOperationId = $operationId
        requestDigest = $requestDigest
        expectedStarts = 1
        executionCountBeforeStart = 0
    }
    $startUri = "https://management.azure.com/subscriptions/$subscription/resourceGroups/$resourceGroup/providers/Microsoft.App/jobs/$jobName/start`?api-version=$apiVersion"
    $start = $null
    try {
        $start = Invoke-Arm 'POST' $startUri '{}'
    }
    catch {
        Add-JournalEvent @{
            event = 'provider_result'
            operationId = $startOperationId
            action = 'start_private_postgres_recovery_job'
            sendExceptionClass = $_.Exception.GetType().Name
            httpStatus = $null
            automaticRetry = $false
            reconciliation = 'GET_ONLY'
        }
    }
    if ($start) {
        Add-JournalEvent @{
            event = 'provider_result'
            operationId = $startOperationId
            action = 'start_private_postgres_recovery_job'
            httpStatus = $start.status
            requestId = $start.requestId
            automaticRetry = $false
            responseBodyLogged = $false
        }
        if ($start.status -notin 200, 202) { throw 'RECOVERY_START_FAILED_NO_RETRY' }
    }

    $executionName = $null
    $executionState = $null
    for ($i = 0; $i -lt 300; $i++) {
        $list = Invoke-Arm 'GET' (Get-ExecutionUri)
        if ($list.status -eq 200) {
            $rows = @((($list.content | ConvertFrom-Json).value))
            if ($rows.Count -gt 0) {
                $row = $rows | Sort-Object { $_.properties.startTime } -Descending | Select-Object -First 1
                $executionName = [string]$row.name
                $executionState = [string]$row.properties.status
                if ($executionState -in 'Succeeded', 'Failed', 'Stopped', 'Cancelled') {
                    Add-JournalEvent @{
                        event = 'execution_reconciled'
                        operationId = $startOperationId
                        action = 'start_private_postgres_recovery_job'
                        jobName = $jobName
                        executionName = $executionName
                        state = $executionState
                        retriesConfigured = 0
                        automaticRetry = $false
                    }
                    break
                }
            }
        }
        Start-Sleep -Seconds 5
    }
    if (-not $executionName) { throw 'RECOVERY_EXECUTION_UNCERTAIN_NO_RETRY' }

    $queryToken = (az account get-access-token --resource https://api.loganalytics.io/ --query accessToken -o tsv 2>$null | Select-Object -First 1).Trim()
    $query = "ContainerAppConsoleLogs_CL | where TimeGenerated > ago(2h) | where ContainerJobName_s == '$jobName' | where Log_s startswith 'PREFLIGHT_' or Log_s startswith 'MIGRATION_' or Log_s startswith 'RECOVERY_' or Log_s startswith 'ROLE_' | project TimeGenerated, Log_s | order by TimeGenerated asc"
    $queryJson = @{ query = $query } | ConvertTo-Json -Compress
    $queryUri = "https://api.loganalytics.io/v1/workspaces/$workspaceId/query?timespan=PT2H"
    $logLines = @()
    if ($queryToken) {
        $queryHeaders = @{ Authorization = "Bearer $queryToken"; 'Content-Type' = 'application/json' }
        for ($attempt = 0; $attempt -lt 10 -and $logLines.Count -eq 0; $attempt++) {
            try {
                $queryResult = Invoke-RestMethod -Method Post -Uri $queryUri -Headers $queryHeaders -Body $queryJson -TimeoutSec 60
                if ($queryResult.tables -and $queryResult.tables[0].rows) {
                    $logLines = @($queryResult.tables[0].rows | ForEach-Object { [string]$_[1] })
                }
            }
            catch {
                Add-JournalEvent @{
                    event = 'diagnostic_read_failed'
                    operationId = $startOperationId
                    action = 'read_recovery_job_markers'
                    errorClass = $_.Exception.GetType().Name
                    automaticRetry = $false
                }
            }
            if ($logLines.Count -eq 0) { Start-Sleep -Seconds 12 }
        }
    }
    Add-JournalEvent @{
        event = 'execution_markers_observed'
        operationId = $startOperationId
        action = 'read_recovery_job_markers'
        jobName = $jobName
        executionName = $executionName
        markerCount = $logLines.Count
        markers = $logLines
        queryTable = 'ContainerAppConsoleLogs'
        queryColumn = 'Log'
        secretValuesLogged = $false
    }

    [pscustomobject]@{
        jobName = $jobName
        createOperationId = $operationId
        startOperationId = $startOperationId
        executionName = $executionName
        executionState = $executionState
        scriptSha256 = $scriptHash
        requestDigest = $requestDigest
        markerCount = $logLines.Count
        markers = $logLines
    } | ConvertTo-Json -Depth 10
}
finally {
    $adminPassword = $null
    $migratorPassword = $null
    $apiPassword = $null
    $authPassword = $null
    $requestBody = $null
    $containerCommand = $null
    $sourceB64 = $null
    $source = $null
    $sourceBytes = $null
    $job = $null
    $queryToken = $null
    $queryHeaders = $null
    $queryJson = $null
    $logsState = $null
    $script:armToken = $null
}
