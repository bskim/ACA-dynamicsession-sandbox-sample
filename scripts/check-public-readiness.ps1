[CmdletBinding()]
param([switch]$RequireDocs)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$failures = [System.Collections.Generic.List[string]]::new()
function Fail([string]$message) {
    if (-not $script:failures.Contains($message)) { $script:failures.Add($message); Write-Host "FAIL: $message" }
}
# .NET Framework (Windows PowerShell 5.1) has no Path.GetRelativePath.
function Get-RelativePublicPath([string]$Path) {
    $base = [IO.Path]::GetFullPath($root).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    $relative = ([uri]$base).MakeRelativeUri([uri][IO.Path]::GetFullPath($Path))
    return [uri]::UnescapeDataString($relative.ToString()).Replace('/', [IO.Path]::DirectorySeparatorChar)
}

function Read-SafeXml([string]$Text) {
    $settings = [Xml.XmlReaderSettings]::new()
    # Ignore exported SVG DOCTYPEs without resolving external or internal entities.
    $settings.DtdProcessing = [Xml.DtdProcessing]::Ignore
    $settings.XmlResolver = $null
    $settings.MaxCharactersInDocument = 16MB
    $reader = [Xml.XmlReader]::Create([IO.StringReader]::new($Text), $settings)
    try {
        $document = [Xml.XmlDocument]::new()
        $document.XmlResolver = $null
        $document.Load($reader)
        return ,$document
    } finally { $reader.Dispose() }
}

# These checks are independent of whether the paths currently exist or are tracked.
foreach ($path in @('.internal/probe', 'AGENTS.md', '.env', 'probe.bicepparam', 'probe.code-workspace')) {
    & git check-ignore --no-index -q -- $path
    if ($LASTEXITCODE -ne 0) { Fail "Required ignore rule missing: $path" }
}
$trackedIgnored = @(& git ls-files --cached --ignored --exclude-standard)
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect Git tracked files.' }
foreach ($path in $trackedIgnored) { Fail "Ignored file is tracked: $path" }
$paths = @(& git -c core.quotepath=false ls-files --cached --others --exclude-standard | Select-Object -Unique)
if ($LASTEXITCODE -ne 0) { throw 'Unable to list public candidates.' }

# Patterns deliberately avoid literal private identifiers in the script itself.
$privatePattern = '(?i)\b(?:rg[-]foundry[-]codex|aifoundry[-]codex|proj[-]codex)[a-z0-9-]*\b'
$guidPattern = '(?i)(?<![0-9a-f])[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}(?![0-9a-f])'
$secretPatterns = @(
    '(?i)\bBearer\s+[A-Za-z0-9_-]{20,}[.][A-Za-z0-9_-]{10,}',
    '\beyJ[A-Za-z0-9_-]{12,}[.]eyJ[A-Za-z0-9_-]{12,}[.][A-Za-z0-9_-]{8,}',
    '\bgh[pousr]_[A-Za-z0-9]{30,}\b',
    '\bgithub_pat_[A-Za-z0-9_]{40,}\b',
    '\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    '-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    '(?i)\b[A-Za-z0-9_]*(?:api[_-]?key|account[_-]?key|access[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*["'']?(?<value>[A-Za-z0-9_+/=-]{24,})'
)
# Match exact private configuration identifiers as well as common name patterns.
$privateValues = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$envPath = Join-Path $root '.env'
if (Test-Path -LiteralPath $envPath) {
    foreach ($line in Get-Content -LiteralPath $envPath) {
        if ($line -match '^\s*(?:AZURE_SUBSCRIPTION_ID|AZURE_TENANT_ID|FOUNDRY_RESOURCE_GROUP|FOUNDRY_ACCOUNT_NAME|FOUNDRY_PROJECT_NAME|SANDBOX_GROUP_NAME|SANDBOX_RESOURCE_GROUP|DYNAMIC_SESSIONS_POOL_ENDPOINT|FOUNDRY_OPENAI_BASE_URL|DYNAMIC_SESSIONS_RESOURCE_GROUP|AZURE_CLIENT_SECRET|FOUNDRY_API_KEY|AZURE_OPENAI_API_KEY|OPENAI_API_KEY)\s*=\s*["'']?([^"''\s#]+)') {
            $value = $Matches[1].Trim()
            if ($value.Length -ge 5 -and $value -notmatch '^(?:true|false|none|null|<.*>)$') { [void]$privateValues.Add($value) }
        }
    }
}
# Exceptions are exact role arguments, not arbitrary GUIDs anywhere in a guide.
$publicRoleIds = @(
    ('0fb8eba5' + 'a2bb' + '4abe' + 'b1c1' + '49dfad359bb0'),
    ('c24cf47c' + '5077' + '412d' + 'a19c' + '45202126392c'),
    ('5e0bd9bd' + '7b93' + '4f28' + 'af87' + '19fc36ad61bd')
)
$roleArgumentPattern = '(?im)^[ \t]*az role assignment create\b[^\r\n]*?[ \t]--role[ \t]+(?<quote>[''"])(?<role>' + $guidPattern + ')\k<quote>(?=[ \t]|$)'
function Test-PublicText([string]$text, [string]$path) {
    if ($text -match $privatePattern) { Fail "Private resource identifier: $path" }
    foreach ($value in $privateValues) {
        if ($text.IndexOf($value, [StringComparison]::OrdinalIgnoreCase) -ge 0) { Fail "Private configuration value: $path"; break }
    }
    $publicRolePositions = [System.Collections.Generic.HashSet[int]]::new()
    if ($path -match '(?i)^docs[\\/]how-to-deploy-and-run(?:-en)?\.md$') {
        foreach ($roleArgument in [regex]::Matches($text, $roleArgumentPattern)) {
            $role = $roleArgument.Groups['role']
            if ($role.Value.Replace('-', '').ToLowerInvariant() -in $publicRoleIds) {
                [void]$publicRolePositions.Add($role.Index)
            }
        }
    }
    foreach ($match in [regex]::Matches($text, $guidPattern)) {
        $compact = $match.Value.Replace('-', '').ToLowerInvariant()
        $publicRoleInGuide = $publicRolePositions.Contains($match.Index)
        if (-not $publicRoleInGuide -and ($compact.ToCharArray() | Select-Object -Unique).Count -gt 1 -and $compact -notin @('12345678123412341234123456789abc', '00000000000000000000000000000000')) {
            Fail "Non-placeholder GUID: $path"; break
        }
    }
    foreach ($pattern in $secretPatterns) {
        $found = $false
        foreach ($match in [regex]::Matches($text, $pattern)) {
            if ($match.Groups['value'].Value -eq 'an-example-sensitive-value') { continue }
            $found = $true; break
        }
        if ($found) { Fail "Possible secret or token: $path"; break }
    }
}

foreach ($path in $paths) {
    $full = Join-Path $root $path
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { continue }
    Test-PublicText ([Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($full))) $path
}

# Inspect decoded data too: private labels can be hidden in compressed pages or SVG images.
function Test-PublicXml($xml, [string]$path, [int]$depth = 0) {
    if ($depth -gt 8) { throw 'Embedded XML nesting limit exceeded.' }
    Test-PublicText $xml.OuterXml $path
    foreach ($node in $xml.SelectNodes('//*')) {
        foreach ($attribute in $node.Attributes) {
            Test-PublicText $attribute.Value $path
            foreach ($match in [regex]::Matches($attribute.Value, 'data:image/svg\+xml(?:;base64)?,[^;\s]+')) {
                $data = $match.Value
                $payload = $data.Substring($data.IndexOf(',') + 1)
                $decoded = if ($data.StartsWith('data:image/svg+xml;base64,')) {
                    [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($payload))
                } else { [uri]::UnescapeDataString($payload) }
                $icon = Read-SafeXml $decoded
                if ($icon.DocumentElement.LocalName -ne 'svg' -or $icon.DocumentElement.NamespaceURI -ne 'http://www.w3.org/2000/svg') {
                    throw 'Invalid embedded SVG.'
                }
                Test-PublicXml $icon "$path (embedded SVG)" ($depth + 1)
            }
        }
        if ($node.LocalName -eq 'svg' -and $node.HasAttribute('content')) {
            Test-PublicXml (Read-SafeXml $node.GetAttribute('content')) "$path (embedded Draw.io)" ($depth + 1)
        }
        if ($node.LocalName -eq 'diagram' -and -not $node.SelectSingleNode('./mxGraphModel')) {
            $inputStream = [IO.MemoryStream]::new([Convert]::FromBase64String($node.InnerText.Trim()))
            $outputStream = [IO.MemoryStream]::new()
            $inflate = [IO.Compression.DeflateStream]::new($inputStream, [IO.Compression.CompressionMode]::Decompress)
            try {
                $buffer = New-Object byte[] 8192
                while (($count = $inflate.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    if ($outputStream.Length + $count -gt 16MB) { throw 'Decoded page size limit exceeded.' }
                    $outputStream.Write($buffer, 0, $count)
                }
                $decoded = [uri]::UnescapeDataString([Text.Encoding]::UTF8.GetString($outputStream.ToArray()))
                $model = Read-SafeXml $decoded
                if ($model.DocumentElement.LocalName -ne 'mxGraphModel') { throw 'Invalid compressed Draw.io page.' }
                Test-PublicXml $model "$path (compressed page)" ($depth + 1)
                $node.InnerText = ''
                [void]$node.AppendChild($xml.ImportNode($model.DocumentElement, $true))
            } finally { $inflate.Dispose(); $outputStream.Dispose(); $inputStream.Dispose() }
        }
    }
}

function Get-EmbeddedSvgSignatures($xml) {
    foreach ($attribute in $xml.SelectNodes('//@*')) {
        foreach ($match in [regex]::Matches($attribute.Value, 'data:image/svg\+xml(?:;base64)?,[^;\s]+')) {
            $data = $match.Value
            $payload = $data.Substring($data.IndexOf(',') + 1)
            $decoded = if ($data.StartsWith('data:image/svg+xml;base64,')) {
                [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($payload))
            } else { [uri]::UnescapeDataString($payload) }
            $icon = Read-SafeXml $decoded
            if (-not $icon.SelectSingleNode('//*[local-name()="path" or local-name()="rect" or local-name()="polygon" or local-name()="ellipse"]')) {
                throw 'Embedded SVG has no graphics.'
            }
            $hash = [Security.Cryptography.SHA256]::Create()
            try { [Convert]::ToBase64String($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($icon.OuterXml))) }
            finally { $hash.Dispose() }
        }
    }
}

function Test-Diagram([string]$file) {
    $relative = Get-RelativePublicPath $file
    try {
        $xml = Read-SafeXml ([IO.File]::ReadAllText($file))
        Test-PublicXml $xml $relative
        $icons = @(Get-EmbeddedSvgSignatures $xml | Select-Object -Unique)
        if ($file.EndsWith('.drawio', [StringComparison]::OrdinalIgnoreCase)) {
            if ($xml.DocumentElement.LocalName -ne 'mxfile' -or
                -not $xml.SelectSingleNode('//mxGraphModel/root/mxCell[@vertex="1"]') -or
                -not $xml.SelectSingleNode('//mxGraphModel/root/mxCell[@edge="1"]')) {
                Fail "Draw.io source has no editable shapes/edges: $relative"
            }
            if (-not $xml.SelectSingleNode('//mxCell[contains(@style,"mxgraph.azure2.")]')) {
                Fail "Azure icon library missing: $relative"
            }
            if ($icons.Count -eq 0) { Fail "Embedded Azure SVG graphics missing: $relative" }
            $render = $file -replace '\.drawio$', '.svg'
            if (Test-Path -LiteralPath $render -PathType Leaf) {
                $renderIcons = @(Get-EmbeddedSvgSignatures (Read-SafeXml ([IO.File]::ReadAllText($render))) | Select-Object -Unique)
                if (@(Compare-Object $icons $renderIcons).Count -gt 0) { Fail "Source/render icon mismatch: $relative" }
            }
        } else {
            if ($xml.DocumentElement.LocalName -ne 'svg' -or $xml.DocumentElement.NamespaceURI -ne 'http://www.w3.org/2000/svg') {
                Fail "Invalid SVG root or namespace: $relative"
            }
            if (-not $xml.SelectSingleNode('//*[local-name()="image"]/@*[local-name()="href" and starts-with(.,"data:image/svg+xml")]')) {
                Fail "Embedded Azure SVG graphics missing: $relative"
            }
            foreach ($href in $xml.SelectNodes('//*[local-name()="image"]/@*[local-name()="href"]')) {
                if (-not $href.Value.StartsWith('data:image/svg+xml')) { Fail "Non-embedded diagram image: $relative" }
            }
        }
    } catch { Fail "Invalid diagram XML or embedded data: $relative" }
}
$documents = @($paths | Where-Object { $_ -match '(?i)\.md$' } | ForEach-Object { Join-Path $root $_ })
$requiredDocs = @('README.md', 'README-en.md', 'DEMO-PLAYBOOK.md', 'DEMO-PLAYBOOK-en.md', 'SECURITY.md', 'SECURITY-en.md')
foreach ($name in @('architecture', 'dynamic-sessions', 'sandbox', 'snapshot-comparison', 'troubleshooting', 'how-to-deploy-and-run')) {
    $requiredDocs += "docs/$name.md", "docs/$name-en.md"
}
$diagramNames = @('overall-architecture', 'dynamic-sessions-session-reuse', 'sandbox-memory-vs-disk-snapshot')
if ($RequireDocs) {
    foreach ($required in $requiredDocs) {
        if (-not (Test-Path -LiteralPath (Join-Path $root $required) -PathType Leaf)) { Fail "Required document missing: $required" }
    }
    foreach ($name in $diagramNames) {
        foreach ($extension in @('drawio', 'svg')) {
            $required = "docs/assets/$name.$extension"
            if (-not (Test-Path -LiteralPath (Join-Path $root $required) -PathType Leaf)) { Fail "Required diagram missing: $required" }
        }
    }
}
foreach ($path in $paths | Where-Object { $_ -match '(?i)\.(?:drawio|svg)$' }) {
    $file = Join-Path $root $path
    if (Test-Path -LiteralPath $file -PathType Leaf) { Test-Diagram $file }
}
foreach ($file in $documents) {
    $name = [IO.Path]::GetFileName($file)
    $english = $name.EndsWith('-en.md', [StringComparison]::OrdinalIgnoreCase)
    $other = if ($english) { $name -replace '-en\.md$', '.md' } else { $name -replace '\.md$', '-en.md' }
    $pair = Join-Path (Split-Path -Parent $file) $other
    $relative = Get-RelativePublicPath $file
    if (-not (Test-Path -LiteralPath $pair -PathType Leaf)) { Fail "Missing language pair: $relative" }
    $text = [IO.File]::ReadAllText($file)
    $top = ($text -split "`r?`n" | Select-Object -First 12) -join "`n"
    if ($top -notmatch ('\]\((?:\./)?' + [regex]::Escape($other) + '(?:#[^)]*)?\)')) { Fail "Missing top language link: $relative" }
    $locale = if ($english) { 'en-us' } else { 'ko-kr' }
    foreach ($match in [regex]::Matches($text, '(?i)https?://learn\.microsoft\.com(?<path>/[^\s)<>"'']*)?')) {
        if (-not $match.Value.StartsWith('https://', [StringComparison]::OrdinalIgnoreCase) -or $match.Groups['path'].Value -notmatch ('^/' + $locale + '/')) { Fail "Incorrect Microsoft Learn locale: $relative"; break }
    }
    if ($text -match '(?im)^\s*(?:`{3,}|~{3,})\s*mermaid\b') { Fail "Mermaid block in public document: $relative" }
    foreach ($link in [regex]::Matches($text, '(?<!!)\[[^\]]+\]\(([^\s)]+)(?:\s+[^)]*)?\)')) {
        $target = [uri]::UnescapeDataString($link.Groups[1].Value.Split('#')[0])
        if (-not $target -or $target -match '^(?:https?://|mailto:)') { continue }
        $local = [IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $file) $target))
        $publicPath = (Get-RelativePublicPath $local).Replace('\', '/')
        if (-not (Test-Path -LiteralPath $local -PathType Leaf) -or $publicPath -notin $paths) {
            Fail "Broken or non-public document link: $relative"
        }
    }
    $images = [regex]::Matches($text, '!\[[^\]]+\]\(([^\s)]+)(?:\s+[^)]*)?\)')
    if ($name -match '^(?:README|architecture|dynamic-sessions|sandbox|snapshot-comparison)(?:-en)?\.md$' -and $images.Count -eq 0) { Fail "Diagram image missing: $relative" }
    foreach ($image in $images) {
        $target = [uri]::UnescapeDataString($image.Groups[1].Value.Split('#')[0])
        $asset = [IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $file) $target))
        $assetsRoot = [IO.Path]::GetFullPath((Join-Path $root 'docs/assets')) + [IO.Path]::DirectorySeparatorChar
        if (-not $asset.StartsWith($assetsRoot, [StringComparison]::OrdinalIgnoreCase) -or $asset -notmatch '\.(?:svg|png)$' -or -not (Test-Path -LiteralPath $asset -PathType Leaf)) {
            Fail "Invalid diagram render path: $relative"; continue
        }
        $source = if ($asset -match '\.drawio\.(?:svg|png)$') { $asset -replace '\.(?:svg|png)$', '' } else { $asset -replace '\.(?:svg|png)$', '.drawio' }
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { Fail "Missing Draw.io source: $relative"; continue }
        $directoryUri = [uri]((Split-Path -Parent $file) + [IO.Path]::DirectorySeparatorChar)
        $sourceLink = [uri]::UnescapeDataString($directoryUri.MakeRelativeUri([uri]$source).ToString())
        if ($text -notmatch ('\]\((?:\./)?' + [regex]::Escape($sourceLink) + '(?:#[^)]*)?\)')) { Fail "Draw.io source link missing: $relative" }
    }
}
if ($failures.Count) {
    Write-Host "Public readiness failed: $($failures.Count) finding(s)."
    exit 1
}
Write-Host "Public readiness checks passed. Git ignore is not a security boundary; independently audit history, binaries and staged files."
exit 0
