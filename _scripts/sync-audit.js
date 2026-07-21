#!/usr/bin/env node
'use strict';
/*
 * sync-audit.js — 審計追蹤層冪等同步腳本 (Node.js, UTF-8)
 *
 * 架構治理（ArchQ）：單向同步（源 -> _audit 唯讀鏡像），隨同步原子重算 _dashboards。
 *
 * 數據契約（frontmatter schema）：
 *   閘門事件: outcome / ts_event / ts_sync / rule_id / actor / proposal_id / source_hash
 *   提案:     proposal_id / gate_event_id / status / ts_event
 *   修復報告: fix_id / gate_event_id / status / ts_event
 *
 * 分片:      按 ts_event 月份（非同步時間），P0-3
 * 唯讀鏡像:  _audit/ 為源真相源的唯讀拷貝，修正回源再重同步，P0-2
 * 雙向ID:    proposal_id <-> gate_event_id，P1-1
 * 看板:      隨同步原子重算 + generated_at 時間戳，P1-2
 * Tombstone: 追加只讀，不自動刪除，P1-3
 *
 * 用法:
 *   node sync-audit.js -Source <meta-peg-agent根> [-Dest <vault根>] [--dry-run] [--retain-months <N>]
 *
 *   -Source          源根目錄，其下須有 logs/ drafts/ fix_reports/（必要）
 *   -Dest            vault 根目錄（默認: 本腳本所在 ../../vault/）
 *   --dry-run / -n   只打印將執行的操作，不落盤
 *   --retain-months  保留月數（默認 13，超過的 ts_event 月份標記為「已歸檔」）
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

// ───── 常數 ─────

const SCHEMA_VERSION = '1.0';
const DEFAULT_RETAIN_MONTHS = 13;

// ───── 參數解析 ─────

function parseArgs(argv) {
  const a = { Source: null, Dest: null, dryRun: false, retainMonths: DEFAULT_RETAIN_MONTHS };
  for (let i = 2; i < argv.length; i++) {
    const t = argv[i];
    if (t === '--dry-run' || t === '-n') a.dryRun = true;
    else if (t === '-Source') a.Source = argv[++i];
    else if (t === '-Dest') a.Dest = argv[++i];
    else if (t === '--retain-months') a.retainMonths = parseInt(argv[++i], 10) || DEFAULT_RETAIN_MONTHS;
    else if (t.startsWith('-Source=')) a.Source = t.slice(8);
    else if (t.startsWith('-Dest=')) a.Dest = t.slice(6);
    else if (t.startsWith('--retain-months=')) a.retainMonths = parseInt(t.slice(16), 10) || DEFAULT_RETAIN_MONTHS;
  }
  return a;
}

// ───── 工具函數 ─────

function ensureDir(d) { fs.mkdirSync(d, { recursive: true }); }

function sha8(s) { return crypto.createHash('sha256').update(s, 'utf8').digest('hex').slice(0, 8); }

function nowISO() { return new Date().toISOString(); }

function monthKey(isoStr) {
  if (!isoStr || typeof isoStr !== 'string') return 'unknown';
  const m = isoStr.match(/^(\d{4}-\d{2})/);
  return m ? m[1] : 'unknown';
}

function isOlderThanMonths(isoStr, n) {
  if (!isoStr || typeof isoStr !== 'string') return false;
  const d = new Date(isoStr);
  if (isNaN(d.getTime())) return false;
  const cutoff = new Date();
  cutoff.setMonth(cutoff.getMonth() - n);
  return d < cutoff;
}

function makeFrontmatter(fields) {
  let y = '---\n';
  for (const [k, v] of Object.entries(fields)) {
    if (v === undefined || v === null) continue;
    if (Array.isArray(v)) {
      y += `${k}:\n`;
      for (const item of v) y += `  - ${item}\n`;
    } else if (typeof v === 'string' && /[:\n#]/.test(v)) {
      y += `${k}: "${v.replace(/"/g, '\\"')}"\n`;
    } else {
      y += `${k}: ${v}\n`;
    }
  }
  y += '---\n';
  return y;
}

function parseFrontmatter(text) {
  const m = text.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/);
  if (!m) return { data: {}, body: text };
  const data = {};
  let currentListKey = null;
  m[1].split(/\r?\n/).forEach(line => {
    const listMatch = line.match(/^\s{2,}-\s+(.*)$/);
    if (listMatch && currentListKey) {
      data[currentListKey].push(listMatch[1].trim());
      return;
    }
    const kvMatch = line.match(/^([A-Za-z0-9_]+):\s*(.*)$/);
    if (kvMatch) {
      let v = kvMatch[2].trim();
      if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
      if (v === '') {
        data[kvMatch[1]] = [];
        currentListKey = kvMatch[1];
      } else {
        data[kvMatch[1]] = v;
        currentListKey = null;
      }
    } else {
      currentListKey = null;
    }
  });
  return { data, body: m[2] };
}

function makeCtx(destRoot, dryRun, retainMonths) {
  return {
    destRoot, dryRun, retainMonths, now: nowISO(),
    stats: { copied: 0, skipped: 0, archived: 0, wouldCopy: 0, errors: 0 },
    report: [],
  };
}

// ───── 閘門事件：解析 JSONL → 生成 .md 筆記 ─────

function syncGateEvents(logsDir, gateDir, ctx) {
  if (!fs.existsSync(logsDir)) {
    ctx.report.push('SKIP gate-events: 源目錄不存在 -> logs/');
    return;
  }
  ensureDir(gateDir);

  let jsonlFiles;
  try { jsonlFiles = fs.readdirSync(logsDir).filter(f => f.endsWith('.jsonl')).sort(); }
  catch (e) { ctx.report.push(`ERROR 讀 logs/: ${e.message}`); ctx.stats.errors++; return; }

  if (jsonlFiles.length === 0) {
    ctx.report.push('SKIP gate-events: logs/ 無 .jsonl 檔案');
    return;
  }

  for (const jf of jsonlFiles) {
    const jp = path.join(logsDir, jf);
    let lines;
    try { lines = fs.readFileSync(jp, 'utf8').split(/\r?\n/).filter(Boolean); }
    catch (e) { ctx.report.push(`ERROR 讀 ${jf}: ${e.message}`); ctx.stats.errors++; continue; }

    for (const line of lines) {
      let ev;
      try { ev = JSON.parse(line); }
      catch (e) { ctx.report.push(`SKIP JSONL 解析失敗 ${jf}: ${e.message}`); continue; }

      const fnameParts = jf.replace(/\.jsonl$/, '').split('_');
      const tsEvent = ev.timestamp || '';
      const verdict = (ev.verdict || 'UNKNOWN').toUpperCase();
      const sourceHash = ev.input_hash || (fnameParts.length >= 6 ? fnameParts[5] : 'unknown');
      const sourceHashShort = sourceHash.slice(0, 8);
      const ruleId = (ev.interceptions && ev.interceptions.length > 0)
        ? ev.interceptions.map(i => i.tag).filter(Boolean).join(',')
        : 'no_alert';
      const actor = 'agent';

      const preview = ev.input_preview || '';
      let proposalId = '';
      const pegMatch = preview.match(/PEG-\d{4}-\d{2}-\d{2}-\d{3}/);
      if (pegMatch) proposalId = pegMatch[0];
      else if (/自指|self.?modify|diff/i.test(preview)) proposalId = 'self-modify';

      const contentKey = `${verdict}|${tsEvent}|${sourceHash}|${ruleId}`;
      const gateId = `gate-${sha8(contentKey)}`;
      const fileName = `gate-${verdict}-${sourceHashShort}.md`;
      const month = monthKey(tsEvent);
      const targetDir = path.join(gateDir, month);
      const outPath = path.join(targetDir, fileName);

      if (fs.existsSync(outPath)) {
        ctx.stats.skipped++;
        continue;
      }

      if (isOlderThanMonths(tsEvent, ctx.retainMonths)) {
        ctx.stats.archived++;
        ctx.report.push(`ARCHIVE ${fileName} (${tsEvent}) 超過保留期 ${ctx.retainMonths} 個月，略過`);
        continue;
      }

      const interceptions = ev.interceptions || [];
      const summary = ev.summary || {};
      const criticalCount = summary.critical || 0;
      const warnCount = summary.warn || 0;
      const totalAlerts = summary.total_alerts || 0;

      const tags = ['gate-event', verdict.toLowerCase()];
      for (const ic of interceptions) {
        const t = ic.tag;
        if (t && !tags.includes(t)) tags.push(t);
      }

      const fm = makeFrontmatter({
        title: `閘門事件: ${verdict}${interceptions.length ? ' · ' + interceptions[0].tag : ''}`,
        gate_id: gateId,
        outcome: verdict,
        ts_event: tsEvent,
        ts_sync: ctx.now,
        rule_id: ruleId,
        actor: actor,
        proposal_id: proposalId || undefined,
        source_hash: sourceHashShort,
        schema_version: SCHEMA_VERSION,
        tags: tags,
        critical_count: criticalCount,
        warn_count: warnCount,
        total_alerts: totalAlerts,
        source: ev.source || '',
        source_file: `logs/${jf}`,
      });

      let body = `# 閘門事件: ${verdict}\n\n`;

      if (verdict === 'REJECT') {
        const sevLabel = criticalCount > 0 ? 'CRITICAL' : 'WARN';
        body += `> [!danger] ${totalAlerts} ${sevLabel} Interception(s)\n\n`;
        if (interceptions.length) {
          body += '| # | Severity | Tag | Reason | Snippet |\n';
          body += '|---|----------|-----|--------|---------|\n';
          interceptions.forEach((ic, i) => {
            body += `| ${i + 1} | ${ic.severity || 'UNKNOWN'} | ${ic.tag || ''} | ${(ic.reason || '').replace(/\|/g, '\\|')} | ${(ic.snippet || '').replace(/\|/g, '\\|')} |\n`;
          });
        }
      } else {
        body += '> [!success] Passed\n> No CRITICAL or WARN issues found.\n';
      }

      body += '\n## 原始記錄\n\n```json\n';
      body += JSON.stringify({ timestamp: tsEvent, verdict, source: ev.source, input_hash: sourceHash, summary, interceptions }, null, 2);
      body += '\n```\n\n---\n\n';
      body += `> [!warning] 唯讀鏡像\n> 本檔案為 \`logs/${jf}\` 的同步拷貝。\n> **修正方式**：回源 \`meta_peg_agent/logs/\` 修改，再重跑同步。\n> 直接編輯本檔案將被同步腳本跳過（不覆蓋），但違反審計一致性。\n`;

      const content = fm + body;
      if (ctx.dryRun) {
        ctx.stats.wouldCopy++;
        ctx.report.push(`WOULD COPY ${fileName} -> _audit/_gate-events/${month}/`);
      } else {
        ensureDir(targetDir);
        try {
          fs.writeFileSync(outPath, content, 'utf8');
          ctx.stats.copied++;
          ctx.report.push(`COPY ${fileName} -> _audit/_gate-events/${month}/`);
        } catch (e) {
          ctx.report.push(`ERROR 寫 ${fileName}: ${e.message}`);
          ctx.stats.errors++;
        }
      }
    }
  }
}

// ───── 提案同步（drafts/ → _proposals/）─────

function syncProposals(draftsDir, proposalsDir, ctx) {
  if (!fs.existsSync(draftsDir)) {
    ctx.report.push('SKIP proposals: 源目錄不存在 -> drafts/');
    return;
  }
  ensureDir(proposalsDir);

  let files;
  try { files = fs.readdirSync(draftsDir).filter(f => f.endsWith('.md')); }
  catch (e) { ctx.report.push(`ERROR 讀 drafts/: ${e.message}`); ctx.stats.errors++; return; }

  for (const f of files) {
    const srcPath = path.join(draftsDir, f);
    let content;
    try { content = fs.readFileSync(srcPath, 'utf8'); }
    catch (e) { ctx.report.push(`ERROR 讀 ${f}: ${e.message}`); ctx.stats.errors++; continue; }

    const { data } = parseFrontmatter(content);
    const proposalId = data.proposal_id || f.replace(/\.md$/, '');
    const tsEvent = data.ts_event || '';
    const status = data.status || 'proposed';

    const outPath = path.join(proposalsDir, f);

    if (fs.existsSync(outPath)) {
      ctx.stats.skipped++;
      ctx.report.push(`SKIP ${f} (已存在)`);
      continue;
    }

    let outContent = content;
    if (!data.proposal_id || !data.ts_event) {
      const fm = makeFrontmatter({
        title: data.title || `提案: ${proposalId}`,
        proposal_id: proposalId,
        gate_event_id: data.gate_event_id || '',
        status: status,
        ts_event: tsEvent,
        ts_sync: ctx.now,
        schema_version: SCHEMA_VERSION,
        tags: data.tags ? (Array.isArray(data.tags) ? data.tags : [data.tags]) : ['proposal'],
      });
      if (data.proposal_id) {
        outContent = content.replace(/^---[\s\S]*?---\r?\n?/, fm);
      } else {
        outContent = fm + content;
      }
    }

    if (ctx.dryRun) {
      ctx.stats.wouldCopy++;
      ctx.report.push(`WOULD COPY ${f} -> _audit/_proposals/`);
    } else {
      try {
        fs.writeFileSync(outPath, outContent, 'utf8');
        ctx.stats.copied++;
        ctx.report.push(`COPY ${f} -> _audit/_proposals/`);
      } catch (e) {
        ctx.report.push(`ERROR 寫 ${f}: ${e.message}`);
        ctx.stats.errors++;
      }
    }
  }
}

// ───── 修復報告同步（fix_reports/ → _fix-reports/）─────

function syncFixes(fixesDir, fixTargetDir, ctx) {
  if (!fs.existsSync(fixesDir)) {
    ctx.report.push('SKIP fix-reports: 源目錄不存在 -> fix_reports/');
    return;
  }
  ensureDir(fixTargetDir);

  let files;
  try { files = fs.readdirSync(fixesDir).filter(f => f.endsWith('.md')); }
  catch (e) { ctx.report.push(`ERROR 讀 fix_reports/: ${e.message}`); ctx.stats.errors++; return; }

  for (const f of files) {
    const srcPath = path.join(fixesDir, f);
    let content;
    try { content = fs.readFileSync(srcPath, 'utf8'); }
    catch (e) { ctx.report.push(`ERROR 讀 ${f}: ${e.message}`); ctx.stats.errors++; continue; }

    const { data } = parseFrontmatter(content);
    const fixId = data.fix_id || f.replace(/\.md$/, '');
    const tsEvent = data.ts_event || '';
    const status = data.status || 'open';

    const outPath = path.join(fixTargetDir, f);

    if (fs.existsSync(outPath)) {
      ctx.stats.skipped++;
      ctx.report.push(`SKIP ${f} (已存在)`);
      continue;
    }

    let outContent = content;
    if (!data.fix_id || !data.ts_event) {
      const fm = makeFrontmatter({
        title: data.title || `修復報告: ${fixId}`,
        fix_id: fixId,
        gate_event_id: data.gate_event_id || '',
        status: status,
        ts_event: tsEvent,
        ts_sync: ctx.now,
        schema_version: SCHEMA_VERSION,
        tags: data.tags ? (Array.isArray(data.tags) ? data.tags : [data.tags]) : ['fix-report'],
      });
      if (data.fix_id) {
        outContent = content.replace(/^---[\s\S]*?---\r?\n?/, fm);
      } else {
        outContent = fm + content;
      }
    }

    if (ctx.dryRun) {
      ctx.stats.wouldCopy++;
      ctx.report.push(`WOULD COPY ${f} -> _audit/_fix-reports/`);
    } else {
      try {
        fs.writeFileSync(outPath, outContent, 'utf8');
        ctx.stats.copied++;
        ctx.report.push(`COPY ${f} -> _audit/_fix-reports/`);
      } catch (e) {
        ctx.report.push(`ERROR 寫 ${f}: ${e.message}`);
        ctx.stats.errors++;
      }
    }
  }
}

// ───── 重建 _index.md（掃描實際文件，冪等）─────

function rebuildIndex(dir, ctx, label) {
  if (!fs.existsSync(dir)) return;
  const entries = [];
  const walk = (d, prefix) => {
    let items;
    try { items = fs.readdirSync(d); }
    catch (e) { return; }
    for (const f of items) {
      const fp = path.join(d, f);
      let st;
      try { st = fs.statSync(fp); } catch (e) { continue; }
      if (st.isDirectory()) {
        if (f !== '_index.md') walk(fp, prefix ? prefix + '/' + f : f);
      } else if (f.endsWith('.md') && f !== '_index.md') {
        let content;
        try { content = fs.readFileSync(fp, 'utf8'); } catch (e) { content = ''; }
        const { data } = parseFrontmatter(content);
        entries.push({ rel: prefix ? prefix + '/' + f : f, data });
      }
    }
  };
  walk(dir, '');
  entries.sort((a, b) => a.rel.localeCompare(b.rel));

  let md = `# ${label} 索引\n\n> 自動生成 · generated_at: ${ctx.now}\n\n`;
  md += '> [!info] 唯讀鏡像\n> 本目錄為源真相源的唯讀拷貝。\n';
  md += '> 修正請回源目錄修改，再重跑同步。\n';
  md += '> 審計記錄只增不刪；如需標記已廢棄，請在源目錄添加 `tombstone: true` 並重同步。\n\n';

  if (!entries.length) {
    md += '_（暫無條目，等待同步）_\n';
  } else {
    md += '| 檔案 | 關鍵元數據 |\n|------|-----------|\n';
    for (const e of entries) {
      const meta = Object.entries(e.data)
        .filter(([k]) => !['title', 'tags', 'schema_version'].includes(k))
        .map(([k, v]) => `${k}=${v}`)
        .join(' | ');
      md += `| [${e.rel}](./${e.rel}) | ${meta || '—'} |\n`;
    }
  }

  const idxPath = path.join(dir, '_index.md');
  if (ctx.dryRun) {
    ctx.report.push(`WOULD WRITE _index.md (${entries.length} 條) <- ${path.relative(ctx.destRoot, dir)}`);
  } else {
    try { fs.writeFileSync(idxPath, md, 'utf8'); }
    catch (e) { ctx.report.push(`ERROR 寫 _index.md: ${e.message}`); ctx.stats.errors++; }
  }
}

// ───── 收集閘門事件（供看板聚合）─────

function collectEvents(dir, out) {
  if (!fs.existsSync(dir)) return;
  for (const f of fs.readdirSync(dir)) {
    const fp = path.join(dir, f);
    let st;
    try { st = fs.statSync(fp); } catch (e) { continue; }
    if (st.isDirectory()) {
      collectEvents(fp, out);
    } else if (f.endsWith('.md') && f !== '_index.md') {
      let content;
      try { content = fs.readFileSync(fp, 'utf8'); } catch (e) { continue; }
      const { data } = parseFrontmatter(content);
      if (data.outcome) {
        out.push({
          outcome: data.outcome.toUpperCase(),
          ts_event: data.ts_event || '',
          rule_id: data.rule_id || 'unknown',
          proposal_id: data.proposal_id || '',
          gate_id: data.gate_id || f,
          source_file: data.source_file || '',
        });
      }
    }
  }
}

// ───── 看板原子重算（P1-2）─────

function buildDashboards(ctx) {
  const auditDir = path.join(ctx.destRoot, '_audit');
  const geDir = path.join(auditDir, '_gate-events');
  const events = [];
  collectEvents(geDir, events);

  const total = events.length;
  const pass = events.filter(e => e.outcome === 'PASS').length;
  const reject = total - pass;
  const passRate = total ? (pass / total * 100).toFixed(1) + '%' : 'N/A';

  const byMonth = {};
  const byRule = {};
  for (const e of events) {
    const m = monthKey(e.ts_event) || 'unknown';
    if (!byMonth[m]) byMonth[m] = { pass: 0, reject: 0 };
    byMonth[m][e.outcome === 'PASS' ? 'pass' : 'reject']++;

    const rules = e.rule_id.split(',').map(r => r.trim()).filter(Boolean);
    for (const r of rules) {
      if (!byRule[r]) byRule[r] = { pass: 0, reject: 0 };
      byRule[r][e.outcome === 'PASS' ? 'pass' : 'reject']++;
    }
  }

  let trends = '---\n';
  trends += `title: 閘門趨勢\n`;
  trends += `generated_at: ${ctx.now}\n`;
  trends += `total_events: ${total}\n`;
  trends += `pass_rate: ${passRate}\n`;
  trends += `tags:\n  - dashboard\n  - gate-trends\n`;
  trends += `schema_version: ${SCHEMA_VERSION}\n`;
  trends += '---\n\n';
  trends += `# 閘門趨勢\n\n`;
  trends += `> 自動生成 · generated_at: ${ctx.now}\n\n`;

  trends += '## 總覽\n\n';
  trends += `| 指標 | 數值 |\n|------|------|\n`;
  trends += `| 事件總數 | ${total} |\n`;
  trends += `| PASS | ${pass} |\n`;
  trends += `| REJECT | ${reject} |\n`;
  trends += `| 通過率 | ${passRate} |\n`;

  trends += '\n## 按月分佈\n\n';
  const months = Object.keys(byMonth).sort();
  if (months.length) {
    trends += '| 月份 | PASS | REJECT | 通過率 |\n|------|------|--------|--------|\n';
    for (const m of months) {
      const r = byMonth[m];
      const rate = (r.pass + r.reject) ? (r.pass / (r.pass + r.reject) * 100).toFixed(1) + '%' : 'N/A';
      trends += `| ${m} | ${r.pass} | ${r.reject} | ${rate} |\n`;
    }
  } else {
    trends += '_（暫無數據）_\n';
  }

  trends += '\n## 攔截分佈（按規則）\n\n';
  const rules = Object.keys(byRule).sort();
  if (rules.length) {
    trends += '| 規則 | PASS | REJECT |\n|------|------|--------|\n';
    for (const r of rules) {
      trends += `| ${r} | ${byRule[r].pass} | ${byRule[r].reject} |\n`;
    }
  } else {
    trends += '_（暫無攔截）_\n';
  }

  trends += '\n---\n\n';
  trends += '> [!info] 本看板隨同步原子重算，非手動編輯。\n';
  trends += '> 數據來源：`_audit/_gate-events/` 中各閘門事件筆記的 frontmatter。\n';

  const proposalsDir = path.join(auditDir, '_proposals');
  const fixDir = path.join(auditDir, '_fix-reports');
  const countMd = d => {
    if (!fs.existsSync(d)) return 0;
    return fs.readdirSync(d).filter(f => f.endsWith('.md') && f !== '_index.md').length;
  };
  const propCount = countMd(proposalsDir);
  const fixCount = countMd(fixDir);

  const recentRejects = events
    .filter(e => e.outcome === 'REJECT')
    .slice(-5)
    .map(e => `- ${e.ts_event || '?'} / ${e.rule_id} / ${e.gate_id}`)
    .join('\n') || '_（無）_';

  let posture = '---\n';
  posture += `title: 安全態勢\n`;
  posture += `generated_at: ${ctx.now}\n`;
  posture += `total_events: ${total}\n`;
  posture += `tags:\n  - dashboard\n  - safety-posture\n`;
  posture += `schema_version: ${SCHEMA_VERSION}\n`;
  posture += '---\n\n';
  posture += `# 安全態勢\n\n`;
  posture += `> 自動生成 · generated_at: ${ctx.now}\n\n`;

  posture += '## 總覽\n\n';
  posture += `| 指標 | 數值 |\n|------|------|\n`;
  posture += `| 閘口事件總數 | ${total} |\n`;
  posture += `| 通過率 | ${passRate} |\n`;
  posture += `| 待審/在冊提案 | ${propCount} |\n`;
  posture += `| 修復報告 | ${fixCount} |\n`;
  posture += `| 最近同步 | ${ctx.now} |\n`;

  posture += '\n## 近期 REJECT（最近 5 筆）\n\n';
  posture += recentRejects + '\n\n';

  posture += '## 態勢判讀\n\n';
  if (total === 0) {
    posture += '尚無閘口數據，等待首次同步。\n';
  } else if (reject === 0) {
    posture += '✅ 暫無攔截，閘門處於放行狀態。\n';
  } else {
    posture += `⚠️ 攔截率 ${((reject / total) * 100).toFixed(1)}%（${reject}/${total}），請關注高頻 REJECT 規則。\n`;
  }

  posture += '\n---\n\n';
  posture += '> [!info] 本看板隨同步原子重算，非手動編輯。\n';

  const dashDir = path.join(auditDir, '_dashboards');
  ensureDir(dashDir);

  if (ctx.dryRun) {
    ctx.report.push('WOULD WRITE _dashboards/gate-trends.md + safety-posture.md');
  } else {
    try {
      fs.writeFileSync(path.join(dashDir, 'gate-trends.md'), trends, 'utf8');
      fs.writeFileSync(path.join(dashDir, 'safety-posture.md'), posture, 'utf8');
      ctx.report.push('UPDATED _dashboards/gate-trends.md + safety-posture.md');
    } catch (e) {
      ctx.report.push(`ERROR 寫看板: ${e.message}`);
      ctx.stats.errors++;
    }
  }
}

// ───── 主流程 ─────

function main() {
  const args = parseArgs(process.argv);
  if (!args.Source) {
    console.error('用法: node sync-audit.js -Source <meta-peg-agent根> [-Dest <vault根>] [--dry-run] [--retain-months <N>]');
    console.error('範例: node sync-audit.js -Source C:\\Users\\1\\Documents\\meta_peg_agent');
    console.error('      node sync-audit.js -Source ../meta-peg-agent --dry-run');
    console.error('      node sync-audit.js -Source ../meta-peg-agent --retain-months 6');
    process.exit(2);
  }

  const destRoot = args.Dest ? path.resolve(args.Dest) : path.resolve(__dirname, '..');
  const ctx = makeCtx(destRoot, args.dryRun, args.retainMonths);
  const srcRoot = path.resolve(args.Source);

  console.log(`源:          ${srcRoot}`);
  console.log(`目標 vault:  ${destRoot}`);
  console.log(`模式:        ${ctx.dryRun ? 'DRY-RUN（不落盤）' : '執行'}`);
  console.log(`保留月數:    ${ctx.retainMonths} 個月`);
  console.log('');

  const auditDir = path.join(destRoot, '_audit');

  syncGateEvents(path.join(srcRoot, 'logs'), path.join(auditDir, '_gate-events'), ctx);
  syncProposals(path.join(srcRoot, 'drafts'), path.join(auditDir, '_proposals'), ctx);
  syncFixes(path.join(srcRoot, 'fix_reports'), path.join(auditDir, '_fix-reports'), ctx);

  rebuildIndex(path.join(auditDir, '_gate-events'), ctx, '閘門事件');
  rebuildIndex(path.join(auditDir, '_proposals'), ctx, '提案');
  rebuildIndex(path.join(auditDir, '_fix-reports'), ctx, '修復報告');

  buildDashboards(ctx);

  const r = ctx.stats;
  const report = ctx.report;
  console.log(`==== 同步報告 ====`);
  if (report.length) console.log(report.join('\n'));
  console.log(`\n統計:`);
  console.log(`  新增:     ${r.copied}`);
  console.log(`  跳過:     ${r.skipped}`);
  console.log(`  已歸檔:   ${r.archived}`);
  console.log(`  將新增:   ${r.wouldCopy} (dry-run)`);
  console.log(`  錯誤:     ${r.errors}`);
  if (ctx.dryRun) console.log('（DRY-RUN 完成，未寫入任何文件）');
  else console.log('（同步 + 看板重算完成）');
  process.exit(r.errors ? 1 : 0);
}

main();
