# Talks to an app's accessibility tree: the Windows version of a11y_helper.py, with the same answers.
# Usage: a11y_helper.ps1 PID list
#        a11y_helper.ps1 PID press NAME [NAME...]   the names in order of preference
param([int]$AppPid, [string]$Mode, [Parameter(ValueFromRemainingArguments)][string[]]$Names)

[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$Auto = [System.Windows.Automation.AutomationElement]
$MaxNodes = 4000

function Get-Pressable($Root) {
    $condition = [System.Windows.Automation.Condition]::TrueCondition
    $seen = 0
    foreach ($node in $Root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition)) {
        if ($seen++ -ge $MaxNodes) { break }
        try {
            $name = $node.Current.Name
            if ($name -and -not $node.Current.IsOffscreen -and (
                    $node.GetCurrentPropertyValue($Auto::IsInvokePatternAvailableProperty) -or
                    $node.GetCurrentPropertyValue($Auto::IsTogglePatternAvailableProperty))) {
                $node
            }
        } catch {}  # nodes vanish while the tree is being walked
    }
}

function Invoke-Node($Node) {
    if ($Node.GetCurrentPropertyValue($Auto::IsInvokePatternAvailableProperty)) {
        $Node.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    } else {
        $Node.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Toggle()
    }
}

$match = New-Object System.Windows.Automation.PropertyCondition($Auto::ProcessIdProperty, $AppPid)
$apps = $Auto::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, $match)
if ($apps.Count -eq 0) {
    @{error = 'not on the accessibility tree'} | ConvertTo-Json -Compress
    exit
}
$items = @(foreach ($app in $apps) { Get-Pressable $app })
if ($Mode -eq 'list') {
    @{items = @($items | ForEach-Object { @{name = $_.Current.Name; role = $_.Current.LocalizedControlType} })} | ConvertTo-Json -Compress -Depth 3
    exit
}
foreach ($wanted in $Names) {
    foreach ($node in $items) {
        if ($node.Current.Name -ieq $wanted) {
            $pressed = $node.Current.Name
            try { Invoke-Node $node } catch { $pressed = $null }
            @{pressed = $pressed} | ConvertTo-Json -Compress
            exit
        }
    }
}
@{pressed = $null} | ConvertTo-Json -Compress
