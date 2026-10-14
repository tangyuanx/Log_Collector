$ErrorActionPreference = 'Stop'
# Embed a complete onedir runtime. The native launcher never uses _MEI/TEMP.
Compress-Archive -Path dist/LogCollector-Portable/* -DestinationPath dist/runtime.zip -Force
$csc = Join-Path $env:SystemRoot 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
& $csc /nologo /target:winexe /platform:x64 /optimize+ /out:dist/LogCollector.exe /win32icon:assets/icon.ico /resource:dist/runtime.zip,runtime.zip /reference:System.Windows.Forms.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll scripts/Launcher.cs
if ($LASTEXITCODE -ne 0) { throw 'Native launcher build failed' }
Remove-Item dist/runtime.zip
