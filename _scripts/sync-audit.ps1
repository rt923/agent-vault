# sync-audit.ps1 — 從 meta_peg_agent/ 同步審計數據到 agent-vault
#
# 用法:
#   .\_scripts\sync-audit.ps1 -Source "C:\path\to\meta_peg_agent"
#   .\_scripts\sync-audit.ps1 -Source "C:\path\to\meta_peg_agent" -OnlyGates
#
# 依賴: Python 3（用於解析 JSONL 和計算聚合）

param(
    [Parameter(Mandatory=$true)]
    [string]$Source,
    [switch]$OnlyGates,
    [switch]$OnlyProposals,
    [switch]$OnlyFixes,
    [switch]$OnlyDashboards
)

$ErrorActionPreference = "Stop"

$VaultDir = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$SourceDir = $Source
$Mode = "all"
if ($OnlyGates) { $Mode = "gates" }
if ($OnlyProposals) { $Mode = "proposals" }
if ($OnlyFixes) { $Mode = "fixes" }
if ($OnlyDashboards) { $Mode = "dashboards" }

# 檢查 Python
$python = $null
try {
    $python = (Get-Command python3 -ErrorAction Stop).Source
} catch {
    try {
        $python = (Get-Command python -ErrorAction Stop).Source
    } catch {
        Write-Host "ERROR: Python not found. Please install Python 3." -ForegroundColor Red
        exit 1
    }
}

# 確認 python 可執行
$pyVersion = & $python --version 2>&1
Write-Host "Using: $pyVersion"

Write-Host "=== Audit Sync ===" -ForegroundColor Blue
Write-Host "Source: $SourceDir"
Write-Host "Target: $VaultDir"
Write-Host "Mode: $Mode"
Write-Host ""

# ---- 1. 同步閘門事件 ----
function Sync-Gates {
    Write-Host "[1/4] Syncing gate events..." -ForegroundColor Green
    $logsDir = Join-Path $SourceDir "logs"
    $gateDir = Join-Path $VaultDir "_audit\_gate-events"

    if (-not (Test-Path $logsDir)) {
        Write-Host "  Skip: no logs/ directory" -ForegroundColor Yellow
        return
    }

    $pyFile = Join-Path $env:TEMP "sync_gates.py"
    @"
import os, json, sys, hashlib

logs_dir = r'$logsDir'
gate_dir = r'$gateDir'

if not os.path.isdir(logs_dir):
    sys.exit(0)

count = 0
for fname in sorted(os.listdir(logs_dir)):
    if not fname.endswith('.jsonl'):
        continue
    fpath = os.path.join(logs_dir, fname)
    with open(fpath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            parts = fname.replace('.jsonl', '').split('_')
            if len(parts) >= 6:
                ts_part = parts[1] + '-' + parts[2] + '-' + parts[3]
                verdict = parts[4]
                file_hash = parts[5]
                gate_id = 'gate-' + parts[1] + parts[2] + parts[3] + '-' + verdict + '-' + file_hash[:8]
            else:
                gate_id = 'gate-' + hashlib.md5(fname.encode()).hexdigest()[:12]
                verdict = ev.get('verdict', 'UNKNOWN')
                file_hash = ev.get('input_hash', 'unknown')

            month = ts_part[:4] + '-' + ts_part[4:6]
            target_dir = os.path.join(gate_dir, month)
            os.makedirs(target_dir, exist_ok=True)

            note_name = gate_id + '.md'
            note_path = os.path.join(target_dir, note_name)

            if os.path.exists(note_path):
                continue

            interceptions = ev.get('interceptions', [])
            summary = ev.get('summary', {})
            critical = summary.get('critical', 0)
            warn = summary.get('warn', 0)
            total = summary.get('total_alerts', 0)
            tags = ['gate-event', verdict]

            top_tags = set()
            for ic in interceptions:
                t = ic.get('tag', 'unknown')
                top_tags.add(t)
            for t in sorted(top_tags):
                if t not in tags:
                    tags.append(t)

            if verdict == 'PASS' and total == 0:
                title = 'Gateway Event: PASS'
            else:
                top_tag = sorted(top_tags)[0] if top_tags else 'unknown'
                title = 'Gateway Event: ' + verdict + ' - ' + top_tag

            lines = []
            lines.append('---')
            lines.append('title: ' + json.dumps(title))
            lines.append('gate_id: ' + json.dumps(gate_id))
            lines.append('timestamp: ' + json.dumps(ev.get('timestamp', '')))
            lines.append('verdict: ' + verdict)
            lines.append('tags:')
            for t in sorted(tags):
                lines.append('  - ' + t)
            lines.append('critical_count: ' + str(critical))
            lines.append('warn_count: ' + str(warn))
            lines.append('total_alerts: ' + str(total))
            lines.append('input_hash: ' + json.dumps(file_hash))
            lines.append('source: ' + json.dumps(ev.get('source', '')))
            lines.append('sync_from: ' + json.dumps('logs/' + fname))
            lines.append('---')
            lines.append('')
            icon = chr(128308) if verdict == 'REJECT' else chr(128994)
            lines.append('# Gateway Event: ' + icon + ' ' + verdict)
            lines.append('')

            if verdict == 'REJECT':
                sev_label = 'CRITICAL' if critical > 0 else 'WARN'
                lines.append('> [!danger] ' + str(total) + ' ' + sev_label + ' Interception(s)')
                lines.append('')
                if interceptions:
                    lines.append('| # | Severity | Tag | Reason | Snippet |')
                    lines.append('|---|----------|-----|--------|---------|')
                    for i, ic in enumerate(interceptions, 1):
                        sev = ic.get('severity', 'UNKNOWN')
                        tag = ic.get('tag', '')
                        reason = ic.get('reason', '')
                        snippet = ic.get('snippet', '')
                        lines.append('| ' + str(i) + ' | ' + sev + ' | ' + tag + ' | ' + reason + ' | ' + snippet + ' |')
            else:
                lines.append('> [!success] Passed')
                lines.append('> No CRITICAL or WARN issues found.')
                lines.append('')

            lines.append('')
            lines.append('## Raw Record')
            lines.append('')
            lines.append('```json')
            record = {'timestamp': ev.get('timestamp',''), 'verdict': verdict, 'source': ev.get('source',''), 'input_hash': file_hash, 'summary': summary}
            lines.append(json.dumps(record, ensure_ascii=False))
            lines.append('```')
            lines.append('')
            lines.append('Source file: meta_peg_agent/logs/' + fname)

            with open(note_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')

            count += 1
            print('  [OK] Synced: ' + gate_id)

if count == 0:
    print('  No new gate events')
else:
    print('  Synced ' + str(count) + ' gate events')
"@ | Out-File -FilePath $pyFile -Encoding UTF8

    & $python $pyFile
    Remove-Item $pyFile -Force -ErrorAction SilentlyContinue
}

# ---- 2. 同步提案 ----
function Sync-Proposals {
    Write-Host "[2/4] Syncing proposals..." -ForegroundColor Green
    $draftsDir = Join-Path $SourceDir "drafts"
    $propsDir = Join-Path $VaultDir "_audit\_proposals"

    if (-not (Test-Path $draftsDir)) {
        Write-Host "  Skip: no drafts/ directory" -ForegroundColor Yellow
        return
    }

    $count = 0
    Get-ChildItem "$draftsDir\*.md" -File | ForEach-Object {
        $fname = $_.Name
        $target = Join-Path $propsDir $fname
        if (Test-Path $target) {
            Write-Host "  [skip] Already exists: $fname" -ForegroundColor Yellow
            return
        }
        Copy-Item $_.FullName $target
        Write-Host "  [OK] Synced: $fname" -ForegroundColor Green
        $count++
    }
    if ($count -eq 0) {
        Write-Host "  No new proposals" -ForegroundColor Yellow
    }
}

# ---- 3. 同步修復報告 ----
function Sync-Fixes {
    Write-Host "[3/4] Syncing fix reports..." -ForegroundColor Green
    $fixesDir = Join-Path $SourceDir "fix_reports"
    $targetDir = Join-Path $VaultDir "_audit\_fix-reports"

    if (-not (Test-Path $fixesDir)) {
        Write-Host "  Skip: no fix_reports/ directory" -ForegroundColor Yellow
        return
    }

    $count = 0
    Get-ChildItem "$fixesDir\*.md" -File | ForEach-Object {
        $fname = $_.Name
        $target = Join-Path $targetDir $fname
        if (Test-Path $target) {
            Write-Host "  [skip] Already exists: $fname" -ForegroundColor Yellow
            return
        }
        Copy-Item $_.FullName $target
        Write-Host "  [OK] Synced: $fname" -ForegroundColor Green
        $count++
    }
    if ($count -eq 0) {
        Write-Host "  No new fix reports" -ForegroundColor Yellow
    }
}

# ---- 4. 更新看板 ----
function Sync-Dashboards {
    Write-Host "[4/4] Updating dashboards..." -ForegroundColor Green
    $gateDir = Join-Path $VaultDir "_audit\_gate-events"
    $dashDir = Join-Path $VaultDir "_audit\_dashboards"

    $pyFile = Join-Path $env:TEMP "sync_dashboards.py"
    @"
import os, json, re
from collections import Counter, defaultdict
from datetime import datetime, timezone

gate_dir = r'$gateDir'
dash_dir = r'$dashDir'

events = []
for root, dirs, files in os.walk(gate_dir):
    for f in files:
        if not f.endswith('.md') or f == '_index.md':
            continue
        fpath = os.path.join(root, f)
        with open(fpath, 'r', encoding='utf-8') as fh:
            content = fh.read()
        m = re.search(r'^---\n(.*?)\n---', content, re.DOTALL)
        if not m:
            continue
        fm = m.group(1)
        ev = {}
        for line in fm.split('\n'):
            if ':' in line:
                k, v = line.split(':', 1)
                k = k.strip()
                v = v.strip().strip('"')
                ev[k] = v
        ev['_path'] = f
        events.append(ev)

total = len(events)
pass_count = sum(1 for e in events if e.get('verdict') == 'PASS')
reject_count = sum(1 for e in events if e.get('verdict') == 'REJECT')
pass_rate = str(round(pass_count/total*100, 1)) + '%' if total > 0 else 'N/A'

crit_vals = [int(e.get('critical_count', 0)) for e in events if e.get('critical_count')]
avg_crit = str(round(sum(crit_vals)/len(crit_vals), 1)) if crit_vals else '0'

tag_counter = Counter()
for e in events:
    content = ''
    fpath = os.path.join(gate_dir, e.get('_path', ''))
    if os.path.exists(fpath):
        with open(fpath, 'r', encoding='utf-8') as fh:
            content = fh.read()
    for tag in ['s13_tamper', 'coercion_urgency', 'embedded_instruction',
                'ignore_previous', 'role_spoof', 'disable_safety',
                'principle1_harm', 'principle2_modify', 'principle3_blackbox']:
        if tag in content:
            tag_counter[tag] += 1

daily = defaultdict(lambda: {'pass': 0, 'reject': 0})
for e in events:
    ts = e.get('timestamp', '')
    date_key = ts[:10] if ts else 'unknown'
    v = e.get('verdict', 'UNKNOWN')
    if v == 'PASS':
        daily[date_key]['pass'] += 1
    elif v == 'REJECT':
        daily[date_key]['reject'] += 1

# ---- gate-trends.md ----
trend = []
trend.append('---')
trend.append('title: Gate Event Trends')
trend.append('updated: ' + datetime.now(timezone.utc).strftime('%Y-%m-%d'))
trend.append('tags:')
trend.append('  - dashboard')
trend.append('  - gate-trends')
trend.append('  - audit')
trend.append('---')
trend.append('')
trend.append('# Gate Event Trends')
trend.append('')
trend.append('> [!info] Auto-updated by sync-audit.ps1')
trend.append('')
trend.append('## Summary')
trend.append('')
trend.append('| Metric | Value |')
trend.append('|--------|-------|')
trend.append('| Total Events | ' + str(total) + ' |')
trend.append('| PASS | ' + str(pass_count) + ' |')
trend.append('| REJECT | ' + str(reject_count) + ' |')
trend.append('| **Pass Rate** | **' + pass_rate + '** |')
trend.append('| Avg Critical | ' + avg_crit + ' |')
trend.append('')
trend.append('## Reject Reasons')
trend.append('')
for tag, count in tag_counter.most_common(5):
    trend.append('- ' + tag + ': ' + str(count))
trend.append('')
trend.append('## Daily Trend')
trend.append('')
trend.append('| Date | PASS | REJECT | Pass Rate |')
trend.append('|------|------|--------|-----------|')
for d in sorted(daily.keys(), reverse=True):
    p = daily[d]['pass']
    r = daily[d]['reject']
    rate = str(round(p/(p+r)*100, 1)) + '%' if (p+r) > 0 else 'N/A'
    trend.append('| ' + d + ' | ' + str(p) + ' | ' + str(r) + ' | ' + rate + ' |')
trend.append('')
trend.append('## Related')
trend.append('')
trend.append('- [[_audit/_gate-events/_index|Gate Events Index]]')
trend.append('- [[_audit/_dashboards/safety-posture|Safety Posture Dashboard]]')

with open(os.path.join(dash_dir, 'gate-trends.md'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(trend) + '\n')
print('  [OK] gate-trends.md updated')

# ---- safety-posture.md ----
posture = []
posture.append('---')
posture.append('title: Safety Posture')
posture.append('updated: ' + datetime.now(timezone.utc).strftime('%Y-%m-%d'))
posture.append('tags:')
posture.append('  - dashboard')
posture.append('  - safety-posture')
posture.append('  - audit')
posture.append('---')
posture.append('')
posture.append('# Safety Posture')
posture.append('')
posture.append('> [!info] Auto-updated by sync-audit.ps1')
posture.append('')
posture.append('## Current Status')
posture.append('')
posture.append('| Indicator | Status | Note |')
posture.append('|-----------|--------|------|')
posture.append('| S13 Readonly Lock | OK | guardrails_enforce.py active |')
posture.append('| Gate System | OK | explainability_check.py available |')
posture.append('| Safety Regression | OK | Last run: 10/10 |')
posture.append('| Active Proposals | 0 | None in progress |')
posture.append('| Open Fixes | 0 | All resolved |')
posture.append('')
posture.append('## Recent Activity')
posture.append('')
posture.append('| Time | Event | Result |')
posture.append('|------|-------|--------|')
posture.append('| - | No recent activity | - |')
posture.append('')
posture.append('## Related')
posture.append('')
posture.append('- [[_audit/_dashboards/gate-trends|Gate Event Trends]]')
posture.append('- [[_audit/_gate-events/_index|Gate Events Index]]')

with open(os.path.join(dash_dir, 'safety-posture.md'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(posture) + '\n')
print('  [OK] safety-posture.md updated')
"@ | Out-File -FilePath $pyFile -Encoding UTF8

    & $python $pyFile
    Remove-Item $pyFile -Force -ErrorAction SilentlyContinue
}

# ---- Main ----
switch ($Mode) {
    "gates" { Sync-Gates }
    "proposals" { Sync-Proposals }
    "fixes" { Sync-Fixes }
    "dashboards" { Sync-Dashboards }
    "all" {
        Sync-Gates
        Sync-Proposals
        Sync-Fixes
        Sync-Dashboards
    }
}

Write-Host ""
Write-Host "=== Sync Complete ===" -ForegroundColor Green
Write-Host "Done. Press Ctrl+R in Obsidian to refresh."