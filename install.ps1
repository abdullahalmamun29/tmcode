# TmCode installer for Windows (PowerShell).
#
#   powershell -ExecutionPolicy Bypass -c "irm https://abdullahalmamun29.github.io/tmcode/install.ps1 | iex"
#
# Downloads the prebuilt TmCode from GitHub Releases (no Python needed) into
# %LOCALAPPDATA%\tmcode and adds it to your user PATH. If that fails, it
# installs TmCode with uv instead, which also fetches Python by itself.
#
# Uninstall: delete %LOCALAPPDATA%\tmcode and remove it from your user PATH.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # makes Invoke-WebRequest much faster

$Repo = 'abdullahalmamun29/tmcode'
$Releases = if ($env:TMCODE_RELEASES_URL) { $env:TMCODE_RELEASES_URL } else { "https://github.com/$Repo/releases/latest/download" }
$Root = if ($env:TMCODE_INSTALL_DIR) { $env:TMCODE_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA 'tmcode' }
$Asset = 'tmcode-windows-x86_64.zip'   # also runs on Windows on ARM (emulated)
$AppDir = Join-Path $Root 'tmcode'

function Add-UserPath([string]$Dir) {
    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts = @($current -split ';' | Where-Object { $_ })
    if ($parts -notcontains $Dir) {
        [Environment]::SetEnvironmentVariable('Path', (($parts + $Dir) -join ';'), 'User')
        Write-Host "Added $Dir to your PATH. Open a new terminal to use tmcode."
    }
    if (($env:Path -split ';') -notcontains $Dir) { $env:Path = "$env:Path;$Dir" }
}

function Install-Binary {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("tmcode-" + [Guid]::NewGuid())
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        Write-Host "Downloading $Asset ..."
        $zip = Join-Path $tmp $Asset
        Invoke-WebRequest -UseBasicParsing -Uri "$Releases/$Asset" -OutFile $zip

        try {
            $sums = (Invoke-WebRequest -UseBasicParsing -Uri "$Releases/SHA256SUMS").Content
            if ($sums -is [byte[]]) { $sums = [Text.Encoding]::UTF8.GetString($sums) }
            $line = ($sums -split "`n") | Where-Object { $_ -match " $([regex]::Escape($Asset))\s*$" } | Select-Object -First 1
            if ($line) {
                $expected = ($line -split ' ')[0].Trim().ToLower()
                $actual = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
                if ($expected -ne $actual) { throw "checksum mismatch for $Asset" }
            }
        } catch [System.Net.WebException] { }

        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        & (Join-Path $tmp 'tmcode\tmcode.exe') --version | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'prebuilt TmCode does not run on this system' }

        New-Item -ItemType Directory -Force -Path $Root | Out-Null
        if (Test-Path $AppDir) { Remove-Item -Recurse -Force $AppDir }
        Move-Item (Join-Path $tmp 'tmcode') $AppDir
        Add-UserPath $AppDir
        return $true
    } catch {
        Write-Warning "Prebuilt install failed: $($_.Exception.Message)"
        return $false
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

function Install-WithUv {
    Write-Host 'Installing TmCode with uv instead ...'
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        $env:UV_NO_MODIFY_PATH = '1'
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    }
    uv tool install --force --python 3.12 tmcode
    if ($LASTEXITCODE -ne 0) { uv tool install --force --python 3.12 "git+https://github.com/$Repo.git" }
    if ($LASTEXITCODE -ne 0) { throw 'uv could not install TmCode' }
    Add-UserPath (uv tool dir --bin)
}

if (Install-Binary) {
    Write-Host "Installed TmCode to $AppDir"
} else {
    Install-WithUv
    Write-Host 'Installed TmCode with uv'
}
Write-Host ''
Write-Host 'Done! Open a new terminal (Windows Terminal works best) and run:  tmcode'
