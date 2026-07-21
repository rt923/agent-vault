#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vault_bridge.py — Outbox → Bridge 流水线 (信任边界版)
====================================================

设计依据
--------
- PEG-A §10「产出落库(Outbox Persistence)」：智能体只把产出写进自己工作区的
  `_outbox/`，形式为 `{name}.md` + 同名 `{name}.meta.md`；**绝不**直接写 Obsidian 库。
- 本脚本运行在【用户本机】，是「Vault Bridge」：从 `_outbox/` 读取、校验、跑安全闸门、
  分类归档进 vault 的 `_agent-output/` 与 `_agent-sessions/`。全程只需文件系统，无需任何凭据。

处理流水线 (每个 {name}.md + {name}.meta.md 对)
--------------------------------------------
  1. 配对发现   ：扫描 _outbox/ 下所有 *.md，要求存在同名 *.meta.md，否则跳过并告警。
  2. 元数据校验 ：解析 .meta.md 前置 YAML，检查必填字段
                  (agent_name / created / file_type / description) 与 file_type 枚举。
                  失败 → 移入 _outbox/_rejected/<name>__<reason>/ 并记录。
  3. §13 红线    ：phase0 主文档 (phase0_meta_peg_agent_prompt[.full].md) 永不进 vault
                  → 视为红线违规，移入 _rejected/。
  4. 安全闸门    ：把 .md 正文写入临时文件，调用 explainability_check.py 扫描
                  (exit != 0 即 REJECT) → 移入 _outbox/_quarantine/<name>/。
  5. 分类路由    ：file_type → _agent-output/archive/<type>/(report|code|image|data|config)
                  note → _agent-output/inbox/；session_trace 或文件名含 trace → _agent-sessions/。
  6. 元数据嵌入  ：.md 目标把 .meta.md 合并进前置 frontmatter；同时写同名 sidecar .meta.md；
                  非 .md 文件则仅复制原文件 + sidecar。
  7. 索引更新    ：向 _agent-output/_index.md 追加一行清单。
  8. 收尾归档    ：成功后将源对移入 _outbox/_done/；全程不删除原始产出(仅移动)。

用法
----
  python vault_bridge.py [--source SRC] [--vault VAULT] [--explainability EXPL]
                         [--dry-run] [--verbose]

  --source          智能体 _outbox 目录 (默认: 下方 DEFAULT_SOURCE)
  --vault           Obsidian vault 根目录 (默认: 下方 DEFAULT_VAULT)
  --explainability  explainability_check.py 路径 (默认: 下方 DEFAULT_EXPLAIN)
  --dry-run         只打印计划，不写任何文件、不移动
  --verbose         打印细节

注：本脚本【只】把文件交付进 vault 工作树；git add / commit / push 是用户本机的 SSH 动作，
    不在本脚本职责内 (遵循信任边界：bridge 负责搬运，push 由用户/WorkBuddy 负责)。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 默认值 (用户本机实际路径；均可用参数/环境变量覆盖)
# ---------------------------------------------------------------------------
DEFAULT_SOURCE = r"C:\Users\1\WorkBuddy\2026-07-13-11-57-54\meta_peg_agent\_outbox"
DEFAULT_VAULT  = r"C:\Users\1\Documents\agent-vault"
DEFAULT_EXPLAIN = r"C:\Users\1\WorkBuddy\2026-07-13-11-57-54\meta_peg_agent\explainability_check.py"

FILE_TYPES = {"report", "code", "image", "data", "note", "config"}
REQUIRED_META = ("agent_name", "created", "file_type", "description")

# §13 红线：这些文件名 (不含扩展名) 永不进 vault
RED_LINE_NAMES = {"phase0_meta_peg_agent_prompt", "phase0_meta_peg_agent_prompt_full"}


# ---------------------------------------------------------------------------
# 极简 YAML 前置解析 (仅覆盖本项目受控 schema：标量 + 块列表)
# ---------------------------------------------------------------------------
def parse_frontmatter(text: str):
    """返回 (meta_dict, body)。无前置则返回 ({}, text)。"""
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    fm = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1:])
    return parse_yaml_simple(fm), body


def _strip_quotes(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("\"", "'"):
        return v[1:-1]
    return v


def parse_yaml_simple(text: str) -> dict:
    data = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s or s.startswith("#"):
            i += 1
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, val = m.group(1), m.group(2).strip()
        if val == "" or val in ("|", ">"):
            # 块列表 或 块标量
            if i + 1 < len(lines) and lines[i + 1].lstrip().startswith("- "):
                items = []
                j = i + 1
                while j < len(lines) and lines[j].lstrip().startswith("- "):
                    items.append(_strip_quotes(lines[j].lstrip()[2:].strip()))
                    j += 1
                data[key] = items
                i = j
                continue
            # 块标量：收集到下一个 key 之前（简化处理：留空）
            data[key] = ""
            i += 1
            continue
        data[key] = _strip_quotes(val)
        i += 1
    return data


def emit_frontmatter(d: dict) -> str:
    out = ["---"]
    for k, v in d.items():
        if isinstance(v, (list, tuple)):
            out.append(f"{k}:")
            for item in v:
                out.append(f"  - {item}")
        else:
            out.append(f"{k}: {v}")
    out.append("---")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# 闸门调用
# ---------------------------------------------------------------------------
def run_gate(explain_path: Path, content: str, tmp_dir: Path, verbose: bool) -> tuple[bool, str]:
    """调用 explainability_check.py 扫描 content。返回 (passed, detail)。"""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / "_gate_tmp.txt"
    tmp.write_text(content, encoding="utf-8")
    try:
        env = dict(os.environ)
        env["LLM_ENABLED"] = "0"  # 强制离线确定性
        proc = subprocess.run(
            [sys.executable, str(explain_path), str(tmp)],
            capture_output=True, text=True, env=env,
        )
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    try:
        rep = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        rep = {}
    passed = proc.returncode == 0 and bool(rep.get("passed", False))
    detail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else f"exit={proc.returncode}"
    if verbose and proc.stdout.strip():
        print("    [gate] " + proc.stdout.strip().replace("\n", "\n    [gate] "))
    return passed, detail


# ---------------------------------------------------------------------------
# 元数据校验
# ---------------------------------------------------------------------------
def validate_meta(meta: dict) -> list[str]:
    """返回缺失/非法字段列表 (空=通过)。"""
    problems = []
    for f in REQUIRED_META:
        if not str(meta.get(f, "")).strip():
            problems.append(f"missing:{f}")
    ft = str(meta.get("file_type", "")).strip()
    if ft and ft not in FILE_TYPES:
        problems.append(f"bad_file_type:{ft}")
    return problems


# ---------------------------------------------------------------------------
# 路由决策
# ---------------------------------------------------------------------------
def route_target(vault: Path, name: str, meta: dict) -> tuple[Path, str]:
    """返回 (目标文件目录, 类型标签)。"""
    is_trace = bool(meta.get("session_trace")) or "trace" in name.lower()
    if is_trace:
        return vault / "_agent-sessions", "session"
    ft = str(meta.get("file_type", "")).strip()
    if ft == "note":
        return vault / "_agent-output" / "inbox", "note"
    if ft in FILE_TYPES:
        return vault / "_agent-output" / "archive" / ft, ft
    # 未知类型兜底到 inbox
    return vault / "_agent-output" / "inbox", ft or "unknown"


# ---------------------------------------------------------------------------
# 索引更新
# ---------------------------------------------------------------------------
def update_index(vault: Path, name: str, meta: dict, dest_file: Path, label: str):
    idx = vault / "_agent-output" / "_index.md"
    rel = dest_file.relative_to(vault).as_posix()
    rel_no_ext = re.sub(r"\.md$", "", rel)
    created = str(meta.get("created", "")).strip()
    date = created[:10] if re.match(r"\d{4}-\d{2}-\d{2}", created) else datetime.now().strftime("%Y-%m-%d")
    agent = str(meta.get("agent_name", "unknown"))
    row = f"| {date} | {agent} | {label} | [[{rel_no_ext}|{name}]] | ✅ |\n"
    if not idx.exists():
        header = (
            "---\ntitle: 智能体产出通道索引\n---\n\n"
            "# 智能体产出索引\n\n"
            "| 日期 | 智能体 | 类型 | 文件 | 状态 |\n"
            "|------|--------|------|------|------|\n"
        )
        idx.write_text(header, encoding="utf-8")
    with idx.open("a", encoding="utf-8") as f:
        f.write(row)


# ---------------------------------------------------------------------------
# 单对处理
# ---------------------------------------------------------------------------
def process_pair(md_path: Path, meta_path: Path, args, cfg) -> str:
    """返回状态: done | rejected | quarantined | error"""
    name = md_path.stem
    src = md_path.parent
    verbose = args.verbose

    # 1) 读元数据
    try:
        meta_text = meta_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"error:read_meta:{e}"
    meta, _ = parse_frontmatter(meta_text)

    # 2) 元数据校验
    problems = validate_meta(meta)
    if problems:
        reason = "_".join(problems)
        if not args.dry_run:
            _move_to(src / "_rejected" / f"{name}__{reason}", md_path, meta_path)
        if verbose:
            print(f"    [reject] {name}: {reason}")
        return "rejected"

    # 3) §13 红线
    if name in RED_LINE_NAMES:
        if not args.dry_run:
            _move_to(src / "_rejected" / f"{name}__redline_phase0", md_path, meta_path)
        if verbose:
            print(f"    [redline] {name}: §13 主文档禁止进入 vault")
        return "rejected"

    # 4) 安全闸门
    content = md_path.read_text(encoding="utf-8")
    is_md = md_path.suffix.lower() == ".md"
    gate_text = content if is_md else str(meta.get("description", ""))
    if cfg["explain"] and gate_text.strip():
        passed, detail = run_gate(cfg["explain"], gate_text, src / "_tmp", verbose)
        if not passed:
            if not args.dry_run:
                _move_to(src / "_quarantine" / name, md_path, meta_path)
            if verbose:
                print(f"    [quarantine] {name}: 闸门 REJECT ({detail})")
            return "quarantined"

    # 5) 路由
    dest_dir, label = route_target(cfg["vault"], name, meta)
    if not args.dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / md_path.name

    # 6) 元数据嵌入 + 写出
    if is_md:
        _, body = parse_frontmatter(content)
        existing_fm, _ = parse_frontmatter(content)
        merged = dict(meta)
        merged.update(existing_fm)  # 输出自身 frontmatter 优先
        merged["bridged_at"] = datetime.now().isoformat(timespec="seconds")
        merged["source_outbox"] = str(md_path)
        new_content = emit_frontmatter(merged) + (body if body.startswith("\n") else "\n" + body)
        if not args.dry_run:
            dest_file.write_text(new_content, encoding="utf-8")
            # sidecar
            shutil.copy2(meta_path, dest_dir / meta_path.name)
    else:
        if not args.dry_run:
            shutil.copy2(md_path, dest_file)
            shutil.copy2(meta_path, dest_dir / meta_path.name)

    # 7) 索引
    if not args.dry_run:
        update_index(cfg["vault"], name, meta, dest_file, label)

    # 8) 收尾：移入 _done
    if not args.dry_run:
        _move_to(src / "_done" / name, md_path, meta_path)
    if verbose:
        print(f"    [done] {name} -> {dest_file.relative_to(cfg['vault']).as_posix()} ({label})")
    return "done"


def _move_to(target_dir: Path, *files):
    target_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        if f.exists():
            shutil.move(str(f), str(target_dir / f.name))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Outbox → Vault 信任边界桥接流水线")
    ap.add_argument("--source", default=os.environ.get("MPA_OUTBOX", DEFAULT_SOURCE),
                    help="智能体 _outbox 目录")
    ap.add_argument("--vault", default=os.environ.get("VAULT_ROOT", DEFAULT_VAULT),
                    help="Obsidian vault 根目录")
    ap.add_argument("--explainability", default=os.environ.get("MPA_EXPLAIN", DEFAULT_EXPLAIN),
                    help="explainability_check.py 路径 (设空可跳过闸门)")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件/不移动")
    ap.add_argument("--verbose", "-v", action="store_true", help="打印细节")
    args = ap.parse_args()

    source = Path(args.source)
    vault = Path(args.vault)
    explain = Path(args.explainability) if args.explainability else None

    print(f"[vault_bridge] source = {source}")
    print(f"[vault_bridge] vault  = {vault}")
    print(f"[vault_bridge] gate   = {explain if explain else '(disabled)'}")
    print(f"[vault_bridge] mode   = {'DRY-RUN' if args.dry_run else 'LIVE'}")

    if not source.exists():
        print(f"[vault_bridge] _outbox 不存在，尝试创建: {source}")
        if not args.dry_run:
            source.mkdir(parents=True, exist_ok=True)
        # 仍继续（可能为空）

    cfg = {"vault": vault, "explain": explain}
    stats = {"done": 0, "rejected": 0, "quarantined": 0, "error": 0, "skipped": 0}

    md_files = sorted(m for m in source.glob("*.md") if not m.name.endswith(".meta.md"))
    paired = 0
    for md in md_files:
        # 跳过桥接自身的辅助目录产物 (理论上不在顶层，但保险)
        if md.name == "_index.md":
            continue
        meta = md.with_name(md.stem + ".meta.md")
        if not meta.exists():
            print(f"[skip] {md.name}: 缺同名 .meta.md")
            stats["skipped"] += 1
            continue
        paired += 1
        if args.verbose or args.dry_run:
            print(f"\n>>> 处理 {md.name} + {meta.name}")
        st = process_pair(md, meta, args, cfg)
        stats[st] = stats.get(st, 0) + 1

    print("\n========== 汇总 ==========")
    print(f"配对文件对 : {paired}")
    print(f"  归档(done)      : {stats['done']}")
    print(f"  拒收(rejected)  : {stats['rejected']}")
    print(f"  隔离(quarantine): {stats['quarantined']}")
    print(f"  错误(error)     : {stats['error']}")
    print(f"  跳过(skipped)   : {stats['skipped']}")
    print("[vault_bridge] 完成。vault 工作树已更新；git push 由用户本机 SSH 动作执行。")


if __name__ == "__main__":
    main()
