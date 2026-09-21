#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Root,

    [Parameter(Mandatory = $true)]
    [string]$RulesFile,

    [Parameter(Mandatory = $true)]
    [string]$ReportPath
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
    throw "Root must be an existing directory: $Root"
}

if (-not (Test-Path -LiteralPath $RulesFile -PathType Leaf)) {
    throw "RulesFile must be an existing file: $RulesFile"
}

$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$resolvedRules = (Resolve-Path -LiteralPath $RulesFile).Path
$reportFullPath = [IO.Path]::GetFullPath($ReportPath)
$reportDirectory = [IO.Path]::GetDirectoryName($reportFullPath)

if (-not (Test-Path -LiteralPath $reportDirectory -PathType Container)) {
    throw "Report directory must already exist: $reportDirectory"
}

$config = Get-Content -LiteralPath $resolvedRules -Raw -Encoding utf8 | ConvertFrom-Json
if (@($config.textExtensions).Count -eq 0) {
    throw "RulesFile must contain a non-empty textExtensions array."
}
if ([int64]$config.maxFileBytes -le 0) {
    throw "RulesFile maxFileBytes must be greater than zero."
}
if (@($config.rules).Count -eq 0) {
    throw "RulesFile must contain at least one rule. Empty rules must never produce PASS."
}

$extensions = @($config.textExtensions | ForEach-Object { $_.ToLowerInvariant() })
$maxBytes = [int64]$config.maxFileBytes
$regexTimeout = [TimeSpan]::FromSeconds(2)
$validatedRules = foreach ($rule in @($config.rules)) {
    if ([string]::IsNullOrWhiteSpace([string]$rule.id)) {
        throw "Every rule must have a non-empty id."
    }
    if ([string]::IsNullOrWhiteSpace([string]$rule.pattern)) {
        throw "Rule '$($rule.id)' must have a non-empty pattern."
    }
    if ($rule.kind -notin @('literal', 'regex')) {
        throw "Unsupported rule kind in rule '$($rule.id)': $($rule.kind)."
    }

    $compiledRegex = $null
    if ($rule.kind -eq 'regex') {
        $compiledRegex = [regex]::new(
            [string]$rule.pattern,
            [Text.RegularExpressions.RegexOptions]::IgnoreCase,
            $regexTimeout)
    }

    [pscustomobject]@{
        id = [string]$rule.id
        kind = [string]$rule.kind
        pattern = [string]$rule.pattern
        regex = $compiledRegex
    }
}

$findings = [System.Collections.Generic.List[object]]::new()
$skipped = [System.Collections.Generic.List[object]]::new()
$checkedFiles = 0

$files = Get-ChildItem -LiteralPath $resolvedRoot -File -Recurse
foreach ($file in $files) {
    $relativePath = [IO.Path]::GetRelativePath($resolvedRoot, $file.FullName)
    $extension = $file.Extension.ToLowerInvariant()

    if ($extensions -notcontains $extension) {
        $skipped.Add([ordered]@{ path = $relativePath; reason = 'extension-not-configured' })
        continue
    }

    if ($file.Length -gt $maxBytes) {
        $skipped.Add([ordered]@{ path = $relativePath; reason = 'file-too-large' })
        continue
    }

    $checkedFiles++
    $lineNumber = 0
    foreach ($line in Get-Content -LiteralPath $file.FullName -Encoding utf8) {
        $lineNumber++
        foreach ($rule in $validatedRules) {
            $matched = $false
            if ($rule.kind -eq 'literal') {
                $matched = $line.IndexOf($rule.pattern, [StringComparison]::OrdinalIgnoreCase) -ge 0
            }
            elseif ($rule.kind -eq 'regex') {
                try {
                    $matched = $rule.regex.IsMatch($line)
                }
                catch [Text.RegularExpressions.RegexMatchTimeoutException] {
                    throw "Regex rule '$($rule.id)' exceeded the two-second timeout."
                }
            }

            if ($matched) {
                $findings.Add([ordered]@{
                    ruleId = [string]$rule.id
                    path = $relativePath
                    line = $lineNumber
                })
            }
        }
    }
}

$rulesHash = (Get-FileHash -LiteralPath $resolvedRules -Algorithm SHA256).Hash
$blockingSkipped = @($skipped | Where-Object { $_.reason -eq 'file-too-large' })
$status = if ($findings.Count -gt 0) { 'FAIL' } elseif ($blockingSkipped.Count -gt 0) { 'BLOCKED' } else { 'PASS' }
$report = [ordered]@{
    schemaVersion = 1
    scannedRoot = $resolvedRoot
    rulesSha256 = $rulesHash
    checkedFiles = $checkedFiles
    skippedFiles = @($skipped)
    blockingSkippedCount = $blockingSkipped.Count
    findingCount = $findings.Count
    findings = @($findings)
    status = $status
}

$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportFullPath -Encoding utf8

if ($findings.Count -gt 0) {
    Write-Error "Privacy artifact scan failed. See the report for rule IDs and locations." -ErrorAction Continue
    exit 2
}

if ($blockingSkipped.Count -gt 0) {
    Write-Error "Privacy artifact scan is blocked: one or more configured text files exceed maxFileBytes. Review skippedFiles." -ErrorAction Continue
    exit 3
}

Write-Output "Privacy artifact scan passed: $checkedFiles text files checked."
exit 0
