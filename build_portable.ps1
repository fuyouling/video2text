# Video2Text Portable Build Script (PowerShell)
# Requires: Python 3.8+, PyInstaller, requests

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Video2Text Green Version Build Tool" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Check Python
Write-Host "[1/6] Checking Python environment..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Python not found" }
    Write-Host "  Python found: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Python not found. Please install Python 3.8+" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Step 2: Clean old builds (optional, use cache to speed up)
Write-Host "[2/6] Cleaning old builds..." -ForegroundColor Yellow
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }

# Step 3: Install dependencies
Write-Host "[3/6] Checking dependencies..." -ForegroundColor Yellow
$packages = @("pyinstaller", "requests")
foreach ($pkg in $packages) {
    $check = pip show $pkg 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Installing $pkg..." -ForegroundColor Yellow
        pip install $pkg
    } else {
        Write-Host "  $pkg already installed" -ForegroundColor Green
    }
}

# Step 4: Build with PyInstaller (use cache)
Write-Host "[4/6] Building executable..." -ForegroundColor Yellow

# Check if rebuild needed (spec file changed or no previous build)
$needsRebuild = $false
$cacheFile = "build\.build_cache"
if (Test-Path "video2text_portable.spec") {
    $currentHash = (Get-FileHash "video2text_portable.spec").Hash
    if (Test-Path $cacheFile) {
        $cachedHash = Get-Content $cacheFile -Raw
        if ($currentHash -ne $cachedHash) { $needsRebuild = $true }
    } else {
        $needsRebuild = $true
    }
} else {
    $needsRebuild = $true
}

$portableDir = "dist\video2text_portable"
if ($needsRebuild -or -not (Test-Path "$portableDir\video2text.exe")) {
    Write-Host "  Rebuilding..." -ForegroundColor Yellow
    pyinstaller video2text_portable.spec  # No --clean, use cache
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Build failed!" -ForegroundColor Red
        Read-Host "`nPress Enter to exit"
        exit 1
    }
    if (Test-Path "video2text_portable.spec") {
        $currentHash = (Get-FileHash "video2text_portable.spec").Hash
        $currentHash | Out-File $cacheFile -Force
    }
    Write-Host "  Build complete (cached for next run)" -ForegroundColor Green
} else {
    Write-Host "  Skipped (using previous build)" -ForegroundColor Green
}

# Step 5: Create directory structure and copy files
Write-Host "[5/6] Creating portable directory structure..." -ForegroundColor Yellow

# Create directories
$dirs = @("logs", "output", "video", "models")
foreach ($dir in $dirs) {
    $path = Join-Path $portableDir $dir
    if (-not (Test-Path $path)) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
        Write-Host "  Created: $dir" -ForegroundColor Green
    }
    # Copy readme.md from source subdirectories
    $srcReadme = Join-Path $dir "readme.md"
    if (Test-Path $srcReadme) {
        Copy-Item -Force $srcReadme "$path\"
        Write-Host "  Copied: $dir\readme.md" -ForegroundColor Green
    }
}

# Copy assets folder (for runtime icon loading)
if (Test-Path "assets") {
    Copy-Item -Recurse -Force "assets" "$portableDir\assets"
    Write-Host "  Copied: assets/ (for icons)" -ForegroundColor Green
}

# Copy config.ini if exists
if (Test-Path "config.ini") {
    Copy-Item -Force "config.ini" "$portableDir\"
    Write-Host "  Copied: config.ini" -ForegroundColor Green
}

# Create README
$readme = @"
Video2Text Portable Version - User Manual
========================================

1. Extract and run video2text.exe directly
2. Config file config.ini can be edited directly
3. Model will auto-download on first run (about 3GB, requires internet)
4. Ensure FFmpeg is installed and added to PATH
5. Ensure Ollama service is running (for summarization)

For detailed documentation, see README.md
"@
$readme | Out-File -FilePath "$portableDir\README_PORTABLE.txt" -Encoding utf8
Write-Host "  Created: README_PORTABLE.txt" -ForegroundColor Green

# Copy README if exists
if (Test-Path "README.md") {
    Copy-Item -Force "README.md" "$portableDir\"
    Write-Host "  Copied: README.md" -ForegroundColor Green
}

# Create launcher script
$batContent = @"
@echo off
cd /d "%~dp0"
start "" "%~dp0video2text.exe" %*
"@
$batContent | Out-File -FilePath "$portableDir\video2text.bat" -Encoding ascii
Write-Host "  Created: video2text.bat" -ForegroundColor Green

# Step 6: Create ZIP package (excluding models)
Write-Host "[6/6] Creating ZIP package (excluding models)..." -ForegroundColor Yellow
$zipName = "video2text_portable_windows_$(Get-Date -Format 'yyyyMMdd').zip"
$zipPath = Join-Path "dist" $zipName

# Stop any running video2text.exe to release file locks
Get-Process "video2text" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

# Create temp dir without models
$tempDir = "dist\video2text_portable_temp"
if (Test-Path $tempDir) { Remove-Item -Recurse -Force $tempDir -ErrorAction SilentlyContinue }
Copy-Item -Recurse -Force "$portableDir" $tempDir

# Create ZIP with retry logic
$retryCount = 3
for ($i = 1; $i -le $retryCount; $i++) {
    try {
        Compress-Archive -Path "$tempDir\*" -DestinationPath $zipPath -Force -ErrorAction Stop
        Write-Host "  Created: $zipPath (models excluded)" -ForegroundColor Green
        break
    } catch {
        if ($i -eq $retryCount) {
            Write-Host "  Warning: ZIP creation failed after $retryCount attempts" -ForegroundColor Yellow
            Write-Host "  You can manually zip: $tempDir" -ForegroundColor Yellow
        } else {
            Write-Host "  Retry $i/$retryCount..." -ForegroundColor Yellow
            Start-Sleep -Seconds 2
        }
    }
}

# Cleanup temp dir
if (Test-Path $tempDir) { Remove-Item -Recurse -Force $tempDir -ErrorAction SilentlyContinue }

# Summary
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Build Complete!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Output files:" -ForegroundColor Yellow
Write-Host "  - Directory: $portableDir" -ForegroundColor White
Write-Host "  - ZIP package: $zipPath" -ForegroundColor White
Write-Host ""
Write-Host "Green version features:" -ForegroundColor Yellow
Write-Host "  [√] No installation required, extract and use" -ForegroundColor Green
Write-Host "  [√] Can directly edit config.ini" -ForegroundColor Green
Write-Host "  [√] No registry writes, pure green software" -ForegroundColor Green
Write-Host "  [√] Auto-downloads model on first run (excluded from ZIP)" -ForegroundColor Green
Write-Host "  [√] Uses cache to speed up rebuilds" -ForegroundColor Green
Write-Host ""

Read-Host "Press Enter to exit"
