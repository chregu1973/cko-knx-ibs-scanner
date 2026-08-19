param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath
)

$ErrorActionPreference = "Stop"

if (-not $env:CKO_SIGNING_CERTIFICATE_BASE64) {
    Write-Host "Kein Code-Signing-Zertifikat konfiguriert; Signierung wird übersprungen."
    exit 0
}

$certificatePath = Join-Path $env:RUNNER_TEMP "cko-code-signing.pfx"
[IO.File]::WriteAllBytes(
    $certificatePath,
    [Convert]::FromBase64String($env:CKO_SIGNING_CERTIFICATE_BASE64)
)

try {
    & signtool.exe sign `
        /fd SHA256 `
        /td SHA256 `
        /tr "http://timestamp.digicert.com" `
        /f $certificatePath `
        /p $env:CKO_SIGNING_CERTIFICATE_PASSWORD `
        $FilePath
    if ($LASTEXITCODE -ne 0) {
        throw "SignTool ist mit Exitcode $LASTEXITCODE fehlgeschlagen."
    }
    & signtool.exe verify /pa /v $FilePath
    if ($LASTEXITCODE -ne 0) {
        throw "Die Signaturprüfung ist mit Exitcode $LASTEXITCODE fehlgeschlagen."
    }
}
finally {
    Remove-Item $certificatePath -Force -ErrorAction SilentlyContinue
}
