<#
.SYNOPSIS
Simulates people using the PMS: every browser test plays its part against a fresh synthetic
company in Docker, in each browser and time zone picked, and the run ends with what failed.

.DESCRIPTION
The browser tests (frontend/e2e) are the PMS's own: staff, an owner, a tenant and an applicant
sign in and use each feature (ci/features.toml says which test uses which). CI runs them in
Chromium, in UTC, on Linux. This runs them where people are: in Edge and Firefox, on Windows,
and in time zones whose day starts before or after UTC's.

Each run starts from empty books (compose.e2e.yaml: its own Postgres, every migration, the API
serving the portal on 127.0.0.1:8123 with the test partners, so no email, screening or payment
reaches anyone), seeded with synthetic PMCs (frontend/e2e/seed.py). Runs never see each other,
and nothing touches a company's books. At the end the stack and its books are removed
(-KeepRunning leaves the last run's up).

Needs Docker Desktop, running, and Node 20 or later (22, as the portal is built with). Edge and
Chrome are used as installed on this computer; Chromium, Firefox and WebKit are downloaded by
Playwright into its own folder the first time.

A failure is a lead, not yet a bug: its report (the folder in the table) shows each step, the
page and a trace (npx playwright show-report <folder>, in frontend).

.PARAMETER Browsers
msedge, chrome, chromium, firefox or webkit. Default: msedge, firefox, chromium.

.PARAMETER TimeZones
IANA names (America/New_York), or "local" for this computer's own. Default: UTC,
America/Los_Angeles (a day starting 7 or 8 hours after UTC's) and Pacific/Kiritimati (14 hours
before).

.PARAMETER Quick
One run: Edge in this computer's own time zone, unless -Browsers or -TimeZones say otherwise.

.PARAMETER Grep
Only the tests whose title matches (a regular expression), as playwright test --grep.

.PARAMETER KeepRunning
Leaves the last run's PMS up at http://127.0.0.1:8123 to look at by hand.

.PARAMETER Plan
Lists the runs and does nothing else.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File scripts\simulate.ps1
Edge, Firefox and Chromium, each in UTC, Los Angeles and Kiritimati: nine runs.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File scripts\simulate.ps1 -Quick
Edge in this computer's time zone: one run.

.EXAMPLE
scripts\simulate.ps1 -Browsers firefox -TimeZones America/New_York -Grep "owner"
#>
[CmdletBinding()]
param(
    [ValidateSet('msedge', 'chrome', 'chromium', 'firefox', 'webkit')]
    [string[]] $Browsers = @('msedge', 'firefox', 'chromium'),
    [string[]] $TimeZones = @('UTC', 'America/Los_Angeles', 'Pacific/Kiritimati'),
    [switch] $Quick,
    [string] $Grep,
    [switch] $KeepRunning,
    [switch] $Plan
)

Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Compose = Join-Path $Root 'compose.e2e.yaml'
$Frontend = Join-Path $Root 'frontend'
$BaseUrl = 'http://127.0.0.1:8123'
# Edge and Chrome are branded builds of Chromium: Playwright drives them by channel.
$Channels = @('msedge', 'chrome')

if ($Quick) {
    if (-not $PSBoundParameters.ContainsKey('Browsers')) { $Browsers = @('msedge') }
    if (-not $PSBoundParameters.ContainsKey('TimeZones')) { $TimeZones = @('local') }
}

function Get-RunName([string] $Browser, [string] $TimeZone) {
    # A folder name: letters, digits and dashes only.
    return ($Browser + '-' + $TimeZone) -replace '[^A-Za-z0-9]+', '-'
}

$Runs = @(
    foreach ($browser in $Browsers) {
        foreach ($zone in $TimeZones) {
            [pscustomobject]@{ Browser = $browser; TimeZone = $zone; Name = (Get-RunName $browser $zone) }
        }
    }
)

if ($Plan) {
    $Runs | Format-Table Browser, TimeZone, Name -AutoSize | Out-String | Write-Host
    return
}

# A program's exit code says whether it worked. Windows PowerShell 5.1 turns what a program writes
# to stderr into errors when it is redirected (and the ISE always), which 'Stop' would make fatal
# before the exit code is read, so programs run under 'Continue'.
function Invoke-Native([string] $What, [scriptblock] $Command) {
    # Runs one program and stops the whole simulation if it fails.
    Write-Host ''
    Write-Host "== $What"
    $ErrorActionPreference = 'Continue'
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
}

function Invoke-Quiet([scriptblock] $Command) {
    # Runs one program, shows nothing of it and returns its exit code.
    $ErrorActionPreference = 'Continue'
    & $Command *> $null
    return $LASTEXITCODE
}

function Test-Prerequisite {
    $problems = @()
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        $problems += 'Docker is not installed: install Docker Desktop (docker.com).'
    } elseif ((Invoke-Quiet { docker info --format '{{.ServerVersion}}' }) -ne 0) {
        $problems += 'Docker is not running: start Docker Desktop, then run this again.'
    }
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        $problems += 'Node is not installed: install Node 22 (nodejs.org).'
    } else {
        $major = [int](((node --version) -replace '^v', '') -split '\.')[0]
        if ($major -lt 20) { $problems += "Node $major is too old: install Node 22 (nodejs.org)." }
    }
    return $problems
}

function Get-LineEndingNote {
    # Git for Windows checks files out with Windows line endings unless told otherwise, and the
    # image is built from the files as they are on disk. Not a failure: worth knowing if one is.
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return $null }
    $ErrorActionPreference = 'Continue'
    $crlf = @(git -C $Root ls-files --eol | Where-Object { $_ -match '\sw/crlf\s' })
    if ($crlf.Count -eq 0) { return $null }
    $setting = git -C $Root config core.autocrlf
    if (-not $setting) { $setting = 'not set' }
    return "$($crlf.Count) files are checked out with Windows line endings (git config core.autocrlf: $setting). If the build or a run fails strangely, this may be why."
}

function Read-Tally([string] $JsonPath) {
    # The test counts from Playwright's JSON report (stats: expected, unexpected, flaky, skipped).
    if (-not (Test-Path $JsonPath)) { return $null }
    $report = Get-Content -Raw -Path $JsonPath | ConvertFrom-Json
    return $report.stats
}

$problems = @(Test-Prerequisite)
if ($problems.Count -gt 0) {
    $problems | ForEach-Object { Write-Host "Cannot start: $_" }
    exit 2
}
$lineEndings = Get-LineEndingNote
if ($lineEndings) { Write-Warning $lineEndings }

$saved = @{}
foreach ($name in @('E2E_BASE_URL', 'PW_CHANNEL', 'PW_TIMEZONE', 'TZ', 'PLAYWRIGHT_HTML_OUTPUT_DIR',
        'PLAYWRIGHT_HTML_OPEN', 'PLAYWRIGHT_JSON_OUTPUT_FILE', 'PMS_DATA_KEY')) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

# A throwaway key, made here for this simulation, seals the synthetic payees' account numbers
# (compose.e2e.yaml passes it to the PMS), as e2e.yml's does. It is never printed or kept.
$keyBytes = New-Object 'byte[]' 32
$random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$random.GetBytes($keyBytes)
$random.Dispose()
$env:PMS_DATA_KEY = [Convert]::ToBase64String($keyBytes)

$Results = @()
$started = Get-Date
Push-Location $Root
try {
    Invoke-Native 'Build the image (the first time takes several minutes)' {
        docker compose -f $Compose build
    }
    Push-Location $Frontend
    try {
        Invoke-Native 'Install the browser tests (npm ci)' { npm ci --no-audit --no-fund }
        $downloads = @($Browsers | Where-Object { $Channels -notcontains $_ } | Select-Object -Unique)
        if ($downloads.Count -gt 0) {
            $install = @('playwright', 'install')
            # On Linux the browsers need system libraries too (CI's runners may install them).
            if ((Test-Path variable:IsLinux) -and $IsLinux -and $env:CI) { $install += '--with-deps' }
            Invoke-Native "Download $($downloads -join ', ') for Playwright" { npx @install @downloads }
        }
    } finally {
        Pop-Location
    }

    foreach ($run in $Runs) {
        Write-Host ''
        Write-Host "######## $($run.Browser) in $($run.TimeZone)"
        Invoke-Native 'Empty books' { docker compose -f $Compose down -v --remove-orphans }
        Invoke-Native 'Start the PMS' { docker compose -f $Compose up -d --wait app }
        Invoke-Native 'Seed the synthetic PMCs' { docker compose -f $Compose run --rm seed }

        $report = Join-Path $Frontend "playwright-report/simulate/$($run.Name)"
        $json = Join-Path $Frontend "test-results/simulate/$($run.Name).json"
        $env:E2E_BASE_URL = $BaseUrl
        $env:PLAYWRIGHT_HTML_OUTPUT_DIR = $report
        $env:PLAYWRIGHT_HTML_OPEN = 'never'
        $env:PLAYWRIGHT_JSON_OUTPUT_FILE = $json
        $env:PW_CHANNEL = $null
        $env:PW_TIMEZONE = $null
        $env:TZ = $null
        $arguments = @('playwright', 'test', '--reporter=list,html,json',
            '--output', (Join-Path $Frontend "test-results/simulate/$($run.Name)"))
        if ($Channels -contains $run.Browser) {
            $env:PW_CHANNEL = $run.Browser
        } else {
            $arguments += "--browser=$($run.Browser)"
        }
        if ($run.TimeZone -ne 'local') {
            # The browser's time zone, and the test runner's, so its own dates agree with the page's.
            $env:PW_TIMEZONE = $run.TimeZone
            $env:TZ = $run.TimeZone
        }
        if ($Grep) { $arguments += @('--grep', $Grep) }

        Push-Location $Frontend
        try {
            $ErrorActionPreference = 'Continue'
            & npx @arguments
            $exit = $LASTEXITCODE
            $ErrorActionPreference = 'Stop'
        } finally {
            Pop-Location
        }
        # A run that ended before Playwright wrote its report counts as failed, with no tally.
        $tally = Read-Tally $json
        $passed, $failed, $flaky, $skipped = 0, 'no report', 0, 0
        if ($tally) {
            $passed, $failed, $flaky, $skipped = $tally.expected, $tally.unexpected, $tally.flaky, $tally.skipped
        }
        $Results += [pscustomobject]@{
            Browser  = $run.Browser
            TimeZone = $run.TimeZone
            Passed   = $passed
            Failed   = $failed
            Flaky    = $flaky
            Skipped  = $skipped
            Exit     = $exit
            Report   = "frontend/playwright-report/simulate/$($run.Name)"
        }
    }
} finally {
    foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
    if ($KeepRunning) {
        Write-Host ''
        Write-Host "The last run's PMS is still up at $BaseUrl (docker compose -f compose.e2e.yaml down -v removes it)."
    } else {
        $null = Invoke-Quiet { docker compose -f $Compose down -v --remove-orphans }
    }
    Pop-Location
}

$minutes = [int]((Get-Date) - $started).TotalMinutes
Write-Host ''
Write-Host "== What happened ($($Results.Count) runs, $minutes minutes)"
$Results | Format-Table Browser, TimeZone, Passed, Failed, Flaky, Skipped, Report -AutoSize | Out-String | Write-Host
if ($lineEndings) { Write-Host "Note: $lineEndings" }

$bad = @($Results | Where-Object { $_.Exit -ne 0 })
if ($bad.Count -gt 0) {
    Write-Host "$($bad.Count) of $($Results.Count) runs had a failure. Open a run's report: cd frontend; npx playwright show-report <Report>"
    exit 1
}
Write-Host 'Every test passed in every run.'
exit 0
