param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

function Invoke-DemoStep {
    param([string]$Program, [string[]]$Arguments, [string]$Failure)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw $Failure }
}

Push-Location -LiteralPath $PSScriptRoot
try {
    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $node -or -not $npm) { throw 'Install Node.js with npm and reopen PowerShell.' }
    $nodeVersion = & $node.Source --version
    if ($LASTEXITCODE -ne 0 -or $nodeVersion -notmatch '^v(\d+\.\d+\.\d+)$') {
        throw 'Could not read the installed Node.js version.'
    }
    $version = [version]$Matches[1]
    if (($version.Major -ne 20 -or $version -lt [version]'20.19.0') -and $version -lt [version]'22.12.0') {
        throw 'Vite requires Node.js 20.19 or newer within 20.x, or Node.js 22.12 or newer.'
    }

    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if (-not $git) { throw 'Install Git for Windows and reopen PowerShell.' }
    Invoke-DemoStep $git.Source @('rev-parse', '--show-toplevel') 'Run this script from a Git checkout.' | Out-Null

    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $versionCheck = 'import sys; raise SystemExit(sys.version_info < (3, 10))'
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        $python = Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $python) { throw 'Install Python 3.10 or newer with the PATH option enabled.' }
        Invoke-DemoStep $python.Source @('-c', $versionCheck) 'Python 3.10 or newer is required.'
        Invoke-DemoStep $python.Source @('-m', 'venv', '.venv') 'Could not create the local Python environment.'
    }
    Invoke-DemoStep $venvPython @('-c', $versionCheck) 'The local .venv requires Python 3.10 or newer. Recreate it with a supported interpreter.'
    Write-Host 'Checking backend dependencies...'
    Invoke-DemoStep $venvPython @('-m', 'pip', '--disable-pip-version-check', 'install', '--quiet', '-r', 'requirements.txt') 'Could not install the backend dependencies.'

    Push-Location -LiteralPath 'apps/web'
    try {
        $lockHash = (Get-FileHash -LiteralPath 'package-lock.json').Hash
        $marker = 'node_modules/.sentinel-lock-sha256'
        $install = -not (Test-Path -LiteralPath $marker -PathType Leaf) -or
            (Get-Content -LiteralPath $marker -Raw).Trim() -ne $lockHash -or
            -not (Test-Path -LiteralPath 'node_modules/.bin/vite.cmd' -PathType Leaf) -or
            -not (Test-Path -LiteralPath 'node_modules/.bin/tsc.cmd' -PathType Leaf)
        if ($install) {
            Write-Host 'Installing frontend dependencies...'
            Invoke-DemoStep $npm.Source @('ci', '--ignore-scripts', '--no-audit', '--no-fund') 'Could not install the locked frontend dependencies.'
            Set-Content -LiteralPath $marker -Value $lockHash -Encoding Ascii
        }

        $build = Get-Item -LiteralPath 'dist/index.html' -ErrorAction SilentlyContinue
        $changed = $build -and (Get-ChildItem -File -Recurse -Path 'src', 'index.html', 'package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.ts' |
            Where-Object { $_.LastWriteTimeUtc -gt $build.LastWriteTimeUtc } |
            Select-Object -First 1)
        if ($install -or -not $build -or $changed) {
            Write-Host 'Building the Evidence Workspace...'
            Invoke-DemoStep $npm.Source @('run', 'build') 'The frontend build failed.'
        }
    }
    finally {
        Pop-Location
    }

    Write-Host "Open http://127.0.0.1:$Port in your browser. Press Ctrl+C to stop."
    Invoke-DemoStep $venvPython @('-m', 'scripts.serve_evidence', '--port', "$Port", '--output-dir', '.cache/portfolio') 'The local evidence server stopped with an error.'
}
catch {
    Write-Error -Message "SENTINEL-CG: $($_.Exception.Message)" -ErrorAction Continue
    exit 1
}
finally {
    Pop-Location
}
