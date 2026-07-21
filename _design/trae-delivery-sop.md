---
title: TRAE 交付標準作業程序
date: 2026-07-21
tags:
  - workflow
  - delivery
  - sop
aliases:
  - Delivery SOP
  - 交付模板
cssclasses:
  - document
status: stable
version: 1.0
related:
  - "[[審計追蹤層架構設計]]"
---

# TRAE 交付標準作業程序

> [!abstract] 用途
> 當 TRAE 告知「已推送到 `trae-delivery` 分支」時，WorkBuddy 按此 SOP 執行取檔、驗證、推送。

---

## 標準流程

```powershell
cd C:\Users\1\Documents\agent-vault

# ═══════════════════════════════════════════════
# ① fetch（唯一硬編碼步驟）
# ═══════════════════════════════════════════════
git fetch origin trae-delivery

# ═══════════════════════════════════════════════
# ② 審查閘門：diff + blob 哈希核對
# ═══════════════════════════════════════════════
# ②a 看真實差異（兩步 diff，非三步 ...）
git -c core.quotepath=false diff --stat master origin/trae-delivery

# ②b 對每個出現在 diff 中的文件，核對 blob 哈希
#    只在 master 與 trae-delivery 哈希不同時才取
#    語法：
#       git rev-parse HEAD:"<文件>"
#       git rev-parse origin/trae-delivery:"<文件>"

# ═══════════════════════════════════════════════
# ③ 只取真正更新的文件
# ═══════════════════════════════════════════════
#    根據②的結果，逐文件執行：
#       git checkout origin/trae-delivery -- "<文件>"

# ═══════════════════════════════════════════════
# ④ 提交與推送
# ═══════════════════════════════════════════════
#    git add "<文件>"
#    git commit -m "docs(vault): ... (TRAE)"
#    git push origin master

# ═══════════════════════════════════════════════
# ⑤ 清理 trae-delivery 分支（遠端）
# ═══════════════════════════════════════════════
#    git push origin --delete trae-delivery
#    注意：本地無此分支，不執行 git branch -d
```

---

## 執行範例

### 情境一：正常交付（1 份文件更新）

```powershell
# ① fetch
git fetch origin trae-delivery

# ② 審查閘門
git -c core.quotepath=false diff --stat master origin/trae-delivery
# 輸出範例：審計追蹤層架構設計.md | 25 +++++

# 核對 blob
git rev-parse HEAD:"審計追蹤層架構設計.md"
git rev-parse origin/trae-delivery:"審計追蹤層架構設計.md"
# 哈希不同 → 確認有更新

# ③ 取文件
git checkout origin/trae-delivery -- "審計追蹤層架構設計.md"

# ④ 提交
git add "審計追蹤層架構設計.md"
git commit -m "docs(vault): 審計追蹤層架構設計 v2.1 同步觸發方式更正 (TRAE)"
git push origin master

# ⑤ 清理
git push origin --delete trae-delivery
```

### 情境二：零更新（兩端一致）

```powershell
git fetch origin trae-delivery
git -c core.quotepath=false diff --stat master origin/trae-delivery
# 輸出為空，或 blob 哈希完全一致

# → 無動作，直接清理
git push origin --delete trae-delivery
```

### 情境三：多份文件更新

```powershell
git fetch origin trae-delivery
git -c core.quotepath=false diff --stat master origin/trae-delivery

# 對每個文件核對 blob，逐文件 checkout
git checkout origin/trae-delivery -- "文件A.md"
git checkout origin/trae-delivery -- "文件B.md"

git add "文件A.md" "文件B.md"
git commit -m "docs(vault): 批量交付 (TRAE)"
git push origin master
git push origin --delete trae-delivery
```

---

## 鐵律

1. **永遠不跳過審查閘門（②）** — 不驗 blob 就直接 checkout 等同於盲目信任，禁止
2. **永遠不 `git add -A`** — 只 `git add` 本次交付的具體文件，避免混入 `.obsidian/`、`_audit/` 等本地遺留
3. **中文文件名必須雙引號包裹** — `git add "審計追蹤層架構設計.md"` 而非 `git add 審計追蹤層架構設計.md`
4. **本地無 `trae-delivery` 分支** — 只做 `git push origin --delete trae-delivery`，不做 `git branch -d`
5. **commit message 尾綴 `(TRAE)`** — 標明來源，便於日後追溯

---

%% 變更記錄 %%

**變更記錄**
- 2026-07-21：v1.0 初始版本，定義 TRAE 交付標準作業程序