#requires -version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('Init', 'Issue')][string]$Action,
    [Parameter(Mandatory = $true)][string]$PrivateKeyPath,
    [string]$PublicKeyPath,
    [string]$RequestCode
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$utf8 = New-Object System.Text.UTF8Encoding $false
$entropy = $utf8.GetBytes('T7-Rekindle/preview-authority/v1')

function ConvertTo-Base64Url([byte[]]$Bytes) {
    return [Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Get-KeyId([byte[]]$PublicBlob) {
    $hash = [System.Security.Cryptography.SHA256]::Create()
    try { return ConvertTo-Base64Url ($hash.ComputeHash($PublicBlob)) }
    finally { $hash.Dispose() }
}

function Write-NewFile([string]$Path, [string]$Content) {
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try {
        $bytes = $utf8.GetBytes($Content + [Environment]::NewLine)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    finally { $stream.Dispose() }
}

$signer = $null
$key = $null
$privateBlob = $null
try {
    $privatePath = [System.IO.Path]::GetFullPath($PrivateKeyPath)
    $repository = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot)).TrimEnd('\') + '\'
    if ($privatePath.StartsWith($repository, [StringComparison]::OrdinalIgnoreCase) -and
        -not $privatePath.StartsWith($repository + '.local\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Keep the private key outside the repository or under its ignored .local directory.'
    }

    if ($Action -eq 'Init') {
        if ([string]::IsNullOrWhiteSpace($PublicKeyPath)) { throw 'Init requires -PublicKeyPath.' }
        $publicPath = [System.IO.Path]::GetFullPath($PublicKeyPath)
        if ($privatePath -eq $publicPath -or (Test-Path -LiteralPath $privatePath) -or (Test-Path -LiteralPath $publicPath)) {
            throw 'Key output paths must be different and must not already exist.'
        }
        if (-not [System.IO.Directory]::Exists([System.IO.Path]::GetDirectoryName($privatePath)) -or
            -not [System.IO.Directory]::Exists([System.IO.Path]::GetDirectoryName($publicPath))) {
            throw 'Create the key output directories before initialization.'
        }
        $signer = New-Object System.Security.Cryptography.ECDsaCng 256
        $publicBlob = $signer.Key.Export([System.Security.Cryptography.CngKeyBlobFormat]::EccPublicBlob)
        $privateBlob = $signer.Key.Export([System.Security.Cryptography.CngKeyBlobFormat]::EccPrivateBlob)
        $protected = [System.Security.Cryptography.ProtectedData]::Protect($privateBlob, $entropy,
            [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
        $publicText = [Convert]::ToBase64String($publicBlob)
        $envelope = [ordered]@{ schemaVersion = 1; publicKey = $publicText; protectedPrivateKey = [Convert]::ToBase64String($protected) }
        Write-NewFile $privatePath ($envelope | ConvertTo-Json -Compress)
        Write-NewFile $publicPath $publicText
        [ordered]@{ keyId = (Get-KeyId $publicBlob); publicKey = $publicText } | ConvertTo-Json -Compress
    }
    else {
        $request = ($RequestCode + '').Trim()
        if ($request -cnotmatch '\AT7PR1\.([A-Za-z0-9_-]{43})\z') { throw 'Issue requires a complete T7PR1 request code.' }
        $subject = $Matches[1]
        $subjectBytes = [Convert]::FromBase64String($subject.Replace('-', '+').Replace('_', '/') + '=')
        if ($subjectBytes.Length -ne 32 -or (ConvertTo-Base64Url $subjectBytes) -cne $subject) { throw 'Invalid request encoding.' }
        if ((Get-Item -LiteralPath $privatePath).Length -gt 16384) { throw 'Invalid private key file size.' }
        $envelope = [System.IO.File]::ReadAllText($privatePath, $utf8) | ConvertFrom-Json
        if ($envelope.schemaVersion -ne 1) { throw 'Unsupported private key format.' }
        $privateBlob = [System.Security.Cryptography.ProtectedData]::Unprotect(
            [Convert]::FromBase64String($envelope.protectedPrivateKey), $entropy,
            [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
        $key = [System.Security.Cryptography.CngKey]::Import($privateBlob, [System.Security.Cryptography.CngKeyBlobFormat]::EccPrivateBlob)
        $signer = New-Object System.Security.Cryptography.ECDsaCng $key
        if ($signer.KeySize -ne 256) { throw 'Only ECDSA P-256 is supported.' }
        $publicBlob = $signer.Key.Export([System.Security.Cryptography.CngKeyBlobFormat]::EccPublicBlob)
        if ([Convert]::ToBase64String($publicBlob) -cne $envelope.publicKey) { throw 'Key pair does not match.' }
        $issuedAt = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        $payload = [ordered]@{
            policyVersion = 1; keyId = (Get-KeyId $publicBlob); subjectHash = $subject
            issuedAt = $issuedAt; expiresAt = $issuedAt + 604800
        } | ConvertTo-Json -Compress
        $message = 'T7PA1.' + (ConvertTo-Base64Url ($utf8.GetBytes($payload)))
        $signer.HashAlgorithm = [System.Security.Cryptography.CngAlgorithm]::Sha256
        $message + '.' + (ConvertTo-Base64Url ($signer.SignData($utf8.GetBytes($message))))
    }
}
catch {
    # Never echo key material, grant text or request identifiers in diagnostics.
    [Console]::Error.WriteLine('Preview authorization operation failed. Check action arguments, output paths, request format and Windows key ownership.')
    exit 1
}
finally {
    if ($null -ne $privateBlob) { [Array]::Clear($privateBlob, 0, $privateBlob.Length) }
    if ($null -ne $signer) { $signer.Dispose() }
    if ($null -ne $key) { $key.Dispose() }
}
