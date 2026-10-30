$ErrorActionPreference='Stop'
$helper=[IO.File]::ReadAllText((Join-Path (Get-Location) 'deploy/run-postgres-recovery.ps1'))
Invoke-Expression $helper.Substring(0,$helper.IndexOf('$adminPassword = $null'))
$origin='https://replayforge-web.blacksky-3aa3a460.centralindia.azurecontainerapps.io'
$values=@{}
foreach($line in [IO.File]::ReadAllLines('C:\Users\dhanu\Downloads\ReplayForge\.env')) {
    if($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {$values[$matches[1]]=$matches[2].Trim().Trim('"').Trim("'")}
}
foreach($key in @('GITHUB_CLIENT_ID','GITHUB_CLIENT_SECRET')) {if(-not $values[$key]){throw 'OAUTH_CONFIGURATION_MISSING'}}
$apiPass=Get-PlaintextSecret 'rf-api-password.dpapi'
$authPass=Get-PlaintextSecret 'rf-auth-password.dpapi'
$apiDsn="host=$serverHost port=5432 dbname=replayforge_demo user=rf_runtime_api password=$apiPass sslmode=verify-full sslrootcert=/etc/ssl/certs/ca-certificates.crt"
$authDsn="host=$serverHost port=5432 dbname=replayforge_demo user=rf_runtime_auth password=$authPass sslmode=verify-full sslrootcert=/etc/ssl/certs/ca-certificates.crt"
$common=@(
 @{name='REPLAYFORGE_DEPLOYMENT_MODE';value='PUBLIC_DEPLOYMENT'},
 @{name='REPLAYFORGE_WEB_AUTH_MODE';value='github'},
 @{name='REPLAYFORGE_PUBLIC_ORIGIN';value=$origin},
 @{name='REPLAYFORGE_AUTH_API_ORIGIN';value=$origin},
 @{name='REPLAYFORGE_AUTH_WEB_ORIGIN';value=$origin}
)
$apiEnv=$common+@(
 @{name='REPLAYFORGE_PRIVATE_DATABASE_HOST';value=$serverHost},
 @{name='REPLAYFORGE_DATABASE_URL';secretRef='api-dsn'},
 @{name='REPLAYFORGE_AUTH_DATABASE_URL';secretRef='auth-dsn'},
 @{name='REPLAYFORGE_AUTH_ENABLE_GITHUB';value='true'},
 @{name='REPLAYFORGE_AUTH_ENCRYPTION_KEY_HEX';secretRef='auth-encryption'},
 @{name='REPLAYFORGE_AUTH_HMAC_KEY_HEX';secretRef='auth-hmac'},
 @{name='GITHUB_CLIENT_ID';value=$values['GITHUB_CLIENT_ID']},
 @{name='GITHUB_CLIENT_SECRET';secretRef='github-secret'},
 @{name='REPLAYFORGE_ENABLE_COLLECTION_BROKER';value='false'},
 @{name='REPLAYFORGE_ENABLE_GITHUB_PUBLICATION';value='false'}
)
$webEnv=$common+@(@{name='REPLAYFORGE_API_URL';value='http://127.0.0.1:8081'})
if($values['GITHUB_APP_SLUG']){$webEnv+=@{name='GITHUB_APP_SLUG';value=$values['GITHUB_APP_SLUG']}}
$proxyTemplate=[IO.File]::ReadAllText((Join-Path (Get-Location) 'deploy/nginx.conf.template'))
if($proxyTemplate -notmatch 'server_names_hash_bucket_size 128;' -or
   $proxyTemplate -notmatch 'listen 127\.0\.0\.1:8081;' -or
   $proxyTemplate -notmatch 'proxy_set_header Host __PUBLIC_HOST__;' -or
   $proxyTemplate -notmatch 'proxy_set_header X-Forwarded-Proto https;') {
 throw 'PUBLIC_PROXY_TEMPLATE_CONTRACT_INVALID'
}
$proxyTemplateBase64=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($proxyTemplate))
$proxyCommand=@'
set -eu
printf '%s' '__TEMPLATE_B64__' | base64 -d > /tmp/replayforge-nginx.template
host=${REPLAYFORGE_PUBLIC_ORIGIN#https://}
sed "s/__PUBLIC_HOST__/$host/g" /tmp/replayforge-nginx.template > /tmp/replayforge-nginx.conf
nginx -t -c /tmp/replayforge-nginx.conf
exec nginx -c /tmp/replayforge-nginx.conf -g 'daemon off;'
'@
$proxyCommand=$proxyCommand.Replace('__TEMPLATE_B64__',$proxyTemplateBase64).Trim()
$app=@{location='Central India';identity=@{type='UserAssigned';userAssignedIdentities=@{$acrIdentity=@{}}};properties=@{
 environmentId=$containerAppsEnvId
 configuration=@{activeRevisionsMode='Single';ingress=@{external=$true;targetPort=8080;allowInsecure=$false;transport='auto'};registries=@(@{server='replayforgefullstack2026.azurecr.io';identity=$acrIdentity});secrets=@(
 @{name='api-dsn';value=$apiDsn},@{name='auth-dsn';value=$authDsn},
 @{name='auth-encryption';value=(Get-PlaintextSecret 'auth-encryption-key.dpapi')},
 @{name='auth-hmac';value=(Get-PlaintextSecret 'auth-hmac-key.dpapi')},
 @{name='github-secret';value=$values['GITHUB_CLIENT_SECRET']})}
 template=@{scale=@{minReplicas=0;maxReplicas=1};containers=@(
 @{name='api';image=$image;env=$apiEnv;resources=@{cpu=0.5;memory='1Gi'}},
 @{name='web';image='replayforgefullstack2026.azurecr.io/replayforge-web@sha256:8c4ad32785ea432345152c9e955324020c3e385c1e81fc817b605286580df610';env=$webEnv;resources=@{cpu=0.5;memory='1Gi'}},
 @{name='proxy';image='replayforgefullstack2026.azurecr.io/replayforge-proxy@sha256:d02e88bb254a617cf429b4ce852701d693baa52096ac2fb29c9335136d9f4f29';command=@('/bin/sh','-c');args=@($proxyCommand);env=$common;resources=@{cpu=0.25;memory='0.5Gi'}})}
}}
$uri="https://management.azure.com/subscriptions/$subscription/resourceGroups/$resourceGroup/providers/Microsoft.App/containerApps/replayforge-web?api-version=$apiVersion"
$before=Invoke-Arm 'GET' $uri
if($before.status -ne 404){throw 'APP_ALREADY_EXISTS_NO_RESUBMISSION'}
$op=[guid]::NewGuid().ToString('N')
Add-JournalEvent @{event='submission_intent';operationId=$op;action='create_public_fullstack_app';origin=$origin;minReplicas=0;maxReplicas=1;secretValuesLogged=$false}
try {
 $result=Invoke-Arm 'PUT' $uri ($app|ConvertTo-Json -Depth 30 -Compress)
 Add-JournalEvent @{event='provider_result';operationId=$op;action='create_public_fullstack_app';httpStatus=$result.status;requestId=$result.requestId;automaticRetry=$false}
 [pscustomobject]@{operationId=$op;httpStatus=$result.status;origin=$origin}|ConvertTo-Json -Compress
} finally {$app=$null;$values=$null;$apiPass=$null;$authPass=$null;$apiDsn=$null;$authDsn=$null;$script:armToken=$null}

