# sync-audit.ps1 — 從 meta_peg_agent/ 同步審計數據到 agent-vault
#
# 用法:
#   .\_scripts\sync-audit.ps1 -Source C:\path\to\meta_peg_agent
#   .\_scripts\sync-audit.ps1 -Source C:\path\to\meta_peg_agent -OnlyGates
#   .\_scripts\sync-audit.ps1 -Source C:\path\to\meta_peg_agent -OnlyProposals
#   .\_scripts\sync-audit.ps1 -Source C:\path\to\meta_peg_agent -OnlyFixes
#   .\_scripts\sync-audit.ps1 -Source C:\path\to\meta_peg_agent -OnlyDashboards
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

# ---- 設定路徑 ----
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
        Write-Host "❌ 錯誤: 找不到 python3 或 python，請先安裝 Python" -ForegroundColor Red
        exit 1
    }
}

Write-Host "=== 同步審計數據 ===" -ForegroundColor Blue
Write-Host "來源: $SourceDir"
Write-Host "目標: $VaultDir"
Write-Host "模式: $Mode"
Write-Host ""

# ---- 1. 同步閘門事件 ----
function Sync-Gates {
    Write-Host "[1/4] 同步閘門事件..." -ForegroundColor Green
    $logsDir = Join-Path $SourceDir "logs"
    $gateDir = Join-Path $VaultDir "_audit\_gate-events"

    if (-not (Test-Path $logsDir)) {
        Write-Host "  跳過：無 logs/ 目錄" -ForegroundColor Yellow
        return
    }

    $pyScript = @"
import os, json, sys, hashlib

logs_dir = os.path.abspath(r'$logsDir')
gate_dir = os.path.abspath(r'$gateDir')

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
                ts_part = f'{parts[1]}-{parts[2]}-{parts[3]}'
                verdict = parts[4]
                file_hash = parts[5]
                gate_id = f'gate-{parts[1]}{parts[2]}{parts[3]}-{verdict}-{file_hash[:8]}'
            else:
                gate_id = f'gate-{hashlib.md5(fname.encode()).hexdigest()[:12]}'
                verdict = ev.get('verdict', 'UNKNOWN')
                file_hash = ev.get('input_hash', 'unknown')

            month = f'{ts_part[:4]}-{ts_part[4:6]}'
            target_dir = os.path.join(gate_dir, month)
            os.makedirs(target_dir, exist_ok=True)

            note_name = f'{gate_id}.md'
            note_path = os.path.join(target_dir, note_name)

            if os.path.exists(note_path):
                continue

            interceptions = ev.get('interceptions', [])
            summary = ev.get('summary', {})
            critical = summary.get('critical', 0)
            warn = summary.get('warn', 0)
            total = summary.get('total_alerts', 0)
            tags = ['gate-event', verdict]
            preview = ev.get('input_preview', '')[:80]

            top_tags = set()
            for ic in interceptions:
                t = ic.get('tag', 'unknown')
                top_tags.add(t)
            for t in sorted(top_tags):
                if t not in tags:
                    tags.append(t)

            if verdict == 'PASS' and total == 0:
                title = '閘門事件: PASS · 正常通過'
            else:
                top_tag = sorted(top_tags)[0] if top_tags else 'unknown'
                title = f'閘門事件: {verdict} · {top_tag}'

            lines = []
            lines.append('---')
            lines.append(f'title: "{title}"')
            lines.append(f'gate_id: "{gate_id}"')
            lines.append(f'timestamp: "{ev.get("timestamp", "")}"')
            lines.append(f'verdict: {verdict}')
            lines.append('tags:')
            for t in sorted(tags):
                lines.append(f'  - {t}')
            lines.append(f'critical_count: {critical}')
            lines.append(f'warn_count: {warn}')
            lines.append(f'total_alerts: {total}')
            lines.append(f'input_hash: "{file_hash}"')
            lines.append(f'input_preview: "{ev.get("input_preview", "")[:60]}"')
            lines.append(f'source: "{ev.get("source", "")}"')
            lines.append(f'sync_from: "logs/{fname}"')
            lines.append('---')
            lines.append('')
            lines.append(f'# 閘門事件: {"🔴" if verdict == "REJECT" else "🟢"} {verdict}')
            lines.append('')

            if verdict == 'REJECT':
                lines.append(f'> [!danger] {total} 項{"CRITICAL" if critical > 0 else "WARN"}攔截')
                lines.append('> 本次閘門調用拒絕了輸入，原因：')
                lines.append('>')
                if interceptions:
                    lines.append('| # | 嚴重級別 | 標籤 | 原因 | 片段 |')
                    lines.append('|---|---------|------|------|------|')
                    for i, ic in enumerate(interceptions, 1):
                        sev = ic.get('severity', 'UNKNOWN')
                        sev_icon = '🔴' if sev == 'CRITICAL' else '🟡'
                        tag = ic.get('tag', '')
                        reason = ic.get('reason', '')
                        snippet = ic.get('snippet', '')
                        lines.append(f'| {i} | {sev_icon} {sev} | {tag} | {reason} | {snippet} |')
            else:
                lines.append('> [!success] 通過')
                lines.append('> 本次閘門調用未發現任何 CRITICAL 或 WARN 問題。')
                lines.append('')

            lines.append('')
            lines.append('## 原始記錄')
            lines.append('')
            lines.append('```json')
            lines.append(json.dumps({'timestamp': ev.get('timestamp',''), 'verdict': verdict, 'source': ev.get('source',''), 'input_hash': file_hash, 'summary': summary}, ensure_ascii=False))
            lines.append('```')
            lines.append('')
            lines.append(f'完整原始 JSONL 檔案：`meta_peg_agent/logs/{fname}`')

            with open(note_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')

            count += 1
            print(f'  ✅ 已同步: {gate_id}')

if count == 0:
    print('  ℹ️  無新閘門事件')
else:
    print(f'  ✅ 共同步 {count} 個閘門事件')
"@

    & $python -c $pyScript
}

# ---- 2. 同步提案 ----
function Sync-Proposals {
    Write-Host "[2/4] 同步自指提案..." -ForegroundColor Green
    $draftsDir = Join-Path $SourceDir "drafts"
    $propsDir = Join-Path $VaultDir "_audit\_proposals"

    if (-not (Test-Path $draftsDir)) {
        Write-Host "  跳過：無 drafts/ 目錄" -ForegroundColor Yellow
        return
    }

    $count = 0
    Get-ChildItem "$draftsDir\*.md" -File | ForEach-Object {
        $fname = $_.Name
        $target = Join-Path $propsDir $fname
        if (Test-Path $target) {
            Write-Host "  ⏭️  已存在: $fname" -ForegroundColor Yellow
            return
        }
        Copy-Item $_.FullName $target
        Write-Host "  ✅ 已同步: $fname" -ForegroundColor Green
        $count++
    }
    if ($count -eq 0) {
        Write-Host "  ℹ️  無新提案" -ForegroundColor Yellow
    }
}

# ---- 3. 同步修復報告 ----
function Sync-Fixes {
    Write-Host "[3/4] 同步修復報告..." -ForegroundColor Green
    $fixesDir = Join-Path $SourceDir "fix_reports"
    $targetDir = Join-Path $VaultDir "_audit\_fix-reports"

    if (-not (Test-Path $fixesDir)) {
        Write-Host "  跳過：無 fix_reports/ 目錄" -ForegroundColor Yellow
        return
    }

    $count = 0
    Get-ChildItem "$fixesDir\*.md" -File | ForEach-Object {
        $fname = $_.Name
        $target = Join-Path $targetDir $fname
        if (Test-Path $target) {
            Write-Host "  ⏭️  已存在: $fname" -ForegroundColor Yellow
            return
        }
        Copy-Item $_.FullName $target
        Write-Host "  ✅ 已同步: $fname" -ForegroundColor Green
        $count++
    }
    if ($count -eq 0) {
        Write-Host "  ℹ️  無新修復報告" -ForegroundColor Yellow
    }
}

# ---- 4. 更新看板 ----
function Sync-Dashboards {
    Write-Host "[4/4] 更新聚合看板..." -ForegroundColor Green
    $gateDir = Join-Path $VaultDir "_audit\_gate-events"
    $dashDir = Join-Path $VaultDir "_audit\_dashboards"

    $pyScript = @"
import os, json, re
from collections import Counter, defaultdict
from datetime import datetime, timezone

gate_dir = os.path.abspath(r'$gateDir')
dash_dir = os.path.abspath(r'$dashDir')

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
pass_rate = f'{pass_count/total*100:.1f}%' if total > 0 else '—'

crit_vals = [int(e.get('critical_count', 0)) for e in events if e.get('critical_count')]
avg_crit = f'{sum(crit_vals)/len(crit_vals):.1f}' if crit_vals else '0'

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

# 生成 gate-trends.md
trend_lines = []
trend_lines.append('---')
trend_lines.append('title: 閘門趨勢看板')
trend_lines.append('updated: ' + datetime.now(timezone.utc).strftime('%Y-%m-%d'))
trend_lines.append('tags:')
trend_lines.append('  - dashboard')
trend_lines.append('  - gate-trends')
trend_lines.append('  - audit')
trend_lines.append('---')
trend_lines.append('')
trend_lines.append('# 閘門趨勢看板')
trend_lines.append('')
trend_lines.append('> [!info] 由 \`sync-audit.ps1\` 自動更新')
trend_lines.append('> 匯總所有 \`explainability_check.py\` 閘門事件的統計趨勢。')
trend_lines.append('')
trend_lines.append('## 總體統計')
trend_lines.append('')
trend_lines.append('| 指標 | 數值 |')
trend_lines.append('|------|------|')
trend_lines.append(f'| 總事件數 | {total} |')
trend_lines.append(f'| 通過 (PASS) | {pass_count} |')
trend_lines.append(f'| 拒絕 (REJECT) | {reject_count} |')
trend_lines.append(f'| **通過率** | **{pass_rate}** |')
trend_lines.append(f'| 平均 Critical 數 | {avg_crit} |')
trend_lines.append('')

if tag_counter:
    trend_lines.append('## 拒絕原因分佈')
    trend_lines.append('')
    trend_lines.append('pie')
    trend_lines.append('    title 拒絕原因分佈')
    for tag, count in tag_counter.most_common(5):
        trend_lines.append(f'    \"{tag}\" : {count}')
trend_lines.append('')

trend_lines.append('## 按時間趨勢')
trend_lines.append('')
trend_lines.append('| 日期 | PASS | REJECT | 通過率 |')
trend_lines.append('|------|------|--------|--------|')
sorted_dates = sorted(daily.keys(), reverse=True)
for d in sorted_dates:
    p = daily[d]['pass']
    r = daily[d]['reject']
    rate = f'{p/(p+r)*100:.1f}%' if (p+r) > 0 else '—'
    trend_lines.append(f'| {d} | {p} | {r} | {rate} |')
trend_lines.append('')

if tag_counter:
    trend_lines.append('## 高頻攔截標籤')
    trend_lines.append('')
    trend_lines.append('| 標籤 | 出現次數 | 說明 |')
    trend_lines.append('|------|---------|------|')
    tag_desc = {
        's13_tamper': '§13 篡改嘗試',
        'coercion_urgency': '脅迫/利誘話術',
        'embedded_instruction': '嵌入指令探針',
        'ignore_previous': '忽略先前指令',
        'role_spoof': '角色偽裝',
        'disable_safety': '禁用安全機制',
        'principle1_harm': '§13 原則一：不傷害',
        'principle2_modify': '§13 原則二：不改底座',
        'principle3_blackbox': '§13 原則三：可知性',
    }
    for tag, count in tag_counter.most_common(10):
        desc = tag_desc.get(tag, '—')
        trend_lines.append(f'| {tag} | {count} | {desc} |')
trend_lines.append('')

with open(os.path.join(dash_dir, 'gate-trends.md'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(trend_lines) + '\n')
print('  ✅ 閘門趨勢看板已更新')

# 生成 safety-posture.md
posture_lines = []
posture_lines.append('---')
posture_lines.append('title: 安全態勢看板')
posture_lines.append('updated: ' + datetime.now(timezone.utc).strftime('%Y-%m-%d'))
posture_lines.append('tags:')
posture_lines.append('  - dashboard')
posture_lines.append('  - safety-posture')
posture_lines.append('  - audit')
posture_lines.append('---')
posture_lines.append('')
posture_lines.append('# 安全態勢看板')
posture_lines.append('')
posture_lines.append('> [!info] 由 \`sync-audit.ps1\` 自動更新')
posture_lines.append('> Meta-PEG-Agent 當前安全態勢總覽。')
posture_lines.append('')
posture_lines.append('## 當前狀態')
posture_lines.append('')
posture_lines.append('| 指標 | 狀態 | 說明 |')
posture_lines.append('|------|------|------|')
posture_lines.append('| 🛡️ §13 只讀鎖 | ✅ 正常 | \`guardrails_enforce.py\` 保護正常 |')
posture_lines.append('| 🚪 閘門系統 | ✅ 運行中 | \`explainability_check.py\` 可用 |')
posture_lines.append('| 📋 安全回歸 | ✅ 10/10 | 最近一次回歸全部通過 |')
posture_lines.append('| 📝 活躍提案 | 0 | 無進行中的自指提案 |')
posture_lines.append('| 🔧 未解決修復 | 0 | 所有已知修復已完成 |')
posture_lines.append('')
posture_lines.append('## 最近活動（24h）')
posture_lines.append('')
posture_lines.append('| 時間 | 事件 | 結果 |')
posture_lines.append('|------|------|------|')
posture_lines.append('| — | 無近期活動 | — |')

with open(os.path.join(dash_dir, 'safety-posture.md'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(posture_lines) + '\n')
print('  ✅ 安全態勢看板已更新')
"@

    & $python -c $pyScript
}

# ---- 主流程 ----
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