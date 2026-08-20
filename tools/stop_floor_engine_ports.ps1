param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot
)

$ErrorActionPreference = 'Stop'

function Get-NormalizedPath([string]$Path) {
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

$root = Get-NormalizedPath $ProjectRoot
$rootPrefix = $root + [System.IO.Path]::DirectorySeparatorChar
$knownPorts = [System.Collections.Generic.HashSet[int]]::new()
foreach ($port in @(7870, 3000, 3001)) {
    [void]$knownPorts.Add($port)
}
if ($env:FLOOR_API_PORT -and $env:FLOOR_API_PORT -match '^\d+$') {
    [void]$knownPorts.Add([int]$env:FLOOR_API_PORT)
}

$managedNames = @(
    'python.exe', 'pythonw.exe', 'node.exe', 'uvicorn.exe',
    'floorengine.exe', 'floor_engine.exe', 'floor-ai.exe', 'floorai.exe'
)

function Test-ProjectProcess($Process) {
    if (-not $Process -or [int]$Process.ProcessId -le 4) {
        return $false
    }
    $name = [string]$Process.Name
    if ($managedNames -notcontains $name.ToLowerInvariant()) {
        return $false
    }
    $executable = [string]$Process.ExecutablePath
    $commandLine = [string]$Process.CommandLine
    if ($executable.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    if ($commandLine.IndexOf($root, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        return $true
    }
    # A relative `python serve.py` invocation has no repository path in its
    # command line.  Only accept it when it owns Floor Engine's primary port.
    if ($name -in @('python.exe', 'pythonw.exe') -and $commandLine -match '(^|[\\/\s"])serve\.py([\s"]|$)') {
        $ownsPrimary = Get-NetTCPConnection -State Listen -OwningProcess ([int]$Process.ProcessId) -ErrorAction SilentlyContinue |
            Where-Object { $knownPorts.Contains([int]$_.LocalPort) } |
            Select-Object -First 1
        return [bool]$ownsPrimary
    }
    return $false
}

function Get-EndpointRows {
    $rows = @()
    try {
        $rows += Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
            ForEach-Object {
                [pscustomobject]@{
                    Protocol = 'TCP'
                    Address = [string]$_.LocalAddress
                    Port = [int]$_.LocalPort
                    ProcessId = [int]$_.OwningProcess
                }
            }
    } catch {}
    try {
        $rows += Get-NetUDPEndpoint -ErrorAction SilentlyContinue |
            ForEach-Object {
                [pscustomobject]@{
                    Protocol = 'UDP'
                    Address = [string]$_.LocalAddress
                    Port = [int]$_.LocalPort
                    ProcessId = [int]$_.OwningProcess
                }
            }
    } catch {}
    return @($rows)
}

$processes = @(Get-CimInstance Win32_Process)
$byId = @{}
foreach ($process in $processes) {
    $byId[[int]$process.ProcessId] = $process
}

$targets = [System.Collections.Generic.HashSet[int]]::new()
foreach ($process in $processes) {
    if (Test-ProjectProcess $process) {
        [void]$targets.Add([int]$process.ProcessId)
    }
}

# Include only managed runtime descendants.  Browser processes opened by the
# service are deliberately excluded even if they happen to be children.
$changed = $true
while ($changed) {
    $changed = $false
    foreach ($process in $processes) {
        $pidValue = [int]$process.ProcessId
        $parentValue = [int]$process.ParentProcessId
        if (-not $targets.Contains($pidValue) -and $targets.Contains($parentValue) -and
                $managedNames -contains ([string]$process.Name).ToLowerInvariant()) {
            [void]$targets.Add($pidValue)
            $changed = $true
        }
    }
}

$endpointsBefore = @(Get-EndpointRows)
$ownedEndpoints = @($endpointsBefore | Where-Object { $targets.Contains([int]$_.ProcessId) })

if ($targets.Count -eq 0) {
    Write-Host '[信息] 没有发现属于 Floor Engine 项目的后台进程。' -ForegroundColor Green
} else {
    Write-Host ('[发现] 项目后台进程：{0} 个' -f $targets.Count) -ForegroundColor Yellow
    foreach ($targetId in @($targets)) {
        $process = $byId[$targetId]
        $ports = @($ownedEndpoints | Where-Object { $_.ProcessId -eq $targetId } |
            Sort-Object Protocol, Port -Unique |
            ForEach-Object { '{0}/{1}' -f $_.Protocol, $_.Port })
        $portText = if ($ports.Count) { $ports -join ', ' } else { '无监听端口（子进程/残留进程）' }
        Write-Host ('  PID {0,-7} {1,-18} {2}' -f $targetId, $process.Name, $portText)
        if ($process.CommandLine) {
            Write-Host ('      {0}' -f $process.CommandLine) -ForegroundColor DarkGray
        }
    }

    function Get-TargetDepth([int]$ProcessId) {
        $depth = 0
        $cursor = $ProcessId
        $visited = [System.Collections.Generic.HashSet[int]]::new()
        while ($byId.ContainsKey($cursor) -and $visited.Add($cursor)) {
            $parent = [int]$byId[$cursor].ParentProcessId
            if (-not $targets.Contains($parent)) { break }
            $depth += 1
            $cursor = $parent
        }
        return $depth
    }

    $orderedTargets = @($targets | ForEach-Object {
        [pscustomobject]@{ ProcessId = [int]$_; Depth = Get-TargetDepth ([int]$_) }
    } | Sort-Object Depth -Descending)

    foreach ($row in $orderedTargets) {
        try {
            Stop-Process -Id $row.ProcessId -Force -ErrorAction Stop
            Write-Host ('[关闭] PID {0}' -f $row.ProcessId) -ForegroundColor Cyan
        } catch {
            Write-Host ('[失败] PID {0}: {1}' -f $row.ProcessId, $_.Exception.Message) -ForegroundColor Red
        }
    }
}

$deadline = (Get-Date).AddSeconds(8)
do {
    Start-Sleep -Milliseconds 250
    $remainingPids = @($targets | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
} while ($remainingPids.Count -gt 0 -and (Get-Date) -lt $deadline)

$remainingProcesses = @(Get-CimInstance Win32_Process | Where-Object { Test-ProjectProcess $_ })
$endpointsAfter = @(Get-EndpointRows)
$knownConflicts = @($endpointsAfter | Where-Object { $knownPorts.Contains([int]$_.Port) })

if ($ownedEndpoints.Count -gt 0) {
    $freed = @($ownedEndpoints | Sort-Object Protocol, Port -Unique |
        ForEach-Object { '{0}/{1}' -f $_.Protocol, $_.Port })
    Write-Host ('[端口] 已处理：{0}' -f ($freed -join ', ')) -ForegroundColor Green
}

if ($knownConflicts.Count -gt 0) {
    Write-Host '[跳过] 以下常用端口仍被其他程序占用；为避免误杀，脚本没有结束它们：' -ForegroundColor Yellow
    foreach ($endpoint in $knownConflicts | Sort-Object Protocol, Port, ProcessId -Unique) {
        $owner = Get-CimInstance Win32_Process -Filter ('ProcessId={0}' -f $endpoint.ProcessId) -ErrorAction SilentlyContinue
        Write-Host ('  {0}/{1} -> PID {2} {3}' -f $endpoint.Protocol, $endpoint.Port,
            $endpoint.ProcessId, ([string]$owner.Name))
        if ($owner.CommandLine) {
            Write-Host ('      {0}' -f $owner.CommandLine) -ForegroundColor DarkGray
        }
    }
}

if ($remainingProcesses.Count -gt 0 -or $remainingPids.Count -gt 0) {
    Write-Host '[未完成] 仍有 Floor Engine 后台进程存活。请以管理员身份重新运行。' -ForegroundColor Red
    exit 2
}

Write-Host '[验证] 没有发现仍在监听端口的 Floor Engine 后台进程。' -ForegroundColor Green
exit 0
