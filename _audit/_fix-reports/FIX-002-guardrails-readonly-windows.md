---
title: "FIX-002: guardrails_enforce.py Windows 只讀屬性檢測修復"
fix_id: "FIX-002"
date: 2026-07-15
severity: P1
status: ✅ 已修復並驗證通過
tags:
  - fix-report
  - guardrails
  - windows
  - readonly
sync_from: "fix_reports/FIX-002-guardrails-readonly-windows.md"
---

# FIX-002: Windows 只讀屬性檢測修復

> [!danger] P1 嚴重級別
> R9 防護鏈缺口：`guardrails_enforce.py` 在 Windows 上無法正確設置/檢測檔案只讀屬性。

## 問題摘要

| 欄位 | 值 |
|------|-----|
| 報告編號 | FIX-002 |
| 日期 | 2026-07-15 |
| 修復人 | PEG-A (self_optimize) |
| 嚴重級別 | P1 |
| 狀態 | ✅ 已修復並驗證通過 |

## 根因

Python 的 `os.stat()` / `os.chmod()` 在 Windows 上不反映/不設置 NTFS 只讀屬性。`os.chmod` 只修改 POSIX 權限位（Python 內部模擬），不調用 Windows API `SetFileAttributesW`。

## 修復方案

新增 `set_readonly()` 和 `set_readable()` 函數，使用 `ctypes` 調用 Windows API：

- `is_readonly()`：改為 `GetFileAttributesW` 檢測
- `set_readonly()`：使用 `SetFileAttributesW` 設置/取消只讀
- 跨平台兼容：Windows 走 ctypes，Linux/macOS 仍走 `os.chmod`

## 驗證結果

| 測試 | 結果 |
|------|------|
| 場景 1：惡意自改提案（削弱 §13） | ✅ REJECT, 2 CRITICAL, 0.004s |
| 場景 2：合法自改提案（不觸及 §13） | ✅ PASS, 0 CRITICAL |
| 場景 3：guardrails verify 只讀鎖完整性 | ✅ hash_match=True, readonly=True |
| 安全回歸測試 | ✅ 10/10 green |

## 完整內容

原始檔案：`meta_peg_agent/fix_reports/FIX-002-guardrails-readonly-windows.md`