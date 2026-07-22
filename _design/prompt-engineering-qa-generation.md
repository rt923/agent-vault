---
title: Q&A 生成提示詞工程語言包
date: 2026-07-22
tags:
  - prompt-engineering
  - sft-training
  - qa-generation
aliases:
  - Prompt Pack
  - 提示詞語言包
cssclasses:
  - document
status: stable
version: 1.0
related:
  - "[[審計追蹤層架構設計]]"
  - "[[_design/trae-delivery-sop]]"
---

# Q&A 生成提示詞工程語言包

> [!abstract] 用途
> 從數位化編程書中自動生成高品質 SFT 問答對。六組提示詞覆蓋生成、校準、多樣性、代碼驗證、品質檢查、審查引導六個環節。

---

## 一、系統提示詞

*所有提示詞共用此系統提示。放在 API 調用的 `system` 欄位。*

```text
你是一位資深編程導師，正在為大語言模型訓練數據集製作高品質問答對。

你的核心原則：
1. 你只能基於【提供的段落內容】生成問答，不得添加段落中沒有的資訊
2. 你的答案必須準確、具體、可驗證
3. 你偏好精確的術語而非模糊的描述
4. 你生成的代碼必須語法正確、可直接運行
5. 你不做過度推廣——如果段落只講了概念 A，你的問題和答案就只圍繞概念 A

你永遠不應該：
- 編造段落中沒有的 API 名稱、函數簽名或版本號
- 用「這段在講什麼」這種泛泛的問題
- 生成沒有原文支撐的「建議」或「最佳實踐」
- 在答案中說「根據我的理解」或「一般來說」（因為你的唯一知識來源是段落）
```

---

## 二、核心生成提示詞

*參數：`{book_title}`, `{chapter}`, `{page}`, `{paragraph_text}`*

```text
請根據以下段落，生成 3-5 個高品質問答對。

【來源】
書籍：{book_title}
章節：{chapter}
頁碼：{page}

【段落內容】
{paragraph_text}

【問題類型要求】
每個問答對必須屬於以下類型之一，且類型不得重複：
- concept：概念解釋（「什麼是 X？」、「X 的作用是什麼？」）
- code：代碼相關（「如何用 X 實現 Y？」、「這段代碼的輸出是什麼？」）
- comparison：對比分析（「X 和 Y 有什麼區別？」）
- howto：實操指導（「如何正確使用 X？」、「X 的常見錯誤有哪些？」）
- why：原理探究（「為什麼要使用 X？」、「X 的設計動機是什麼？」）

【難度要求】
- beginner：剛接觸此概念的初學者能理解的問題
- intermediate：有一定基礎、需要結合多個概念理解的問題
- advanced：需要深入理解原理或涉及邊界情況的問題

【輸出格式】
嚴格輸出 JSON 陣列，不要添加任何其他文字：

```json
[
  {
    "question": "具體問題（以問號結尾）",
    "answer": "基於段落的準確回答，引用原文關鍵術語",
    "type": "concept|code|comparison|howto|why",
    "difficulty": "beginner|intermediate|advanced",
    "source_quote": "答案對應的原文關鍵句（直接引用段落中的一句話）"
  }
]
```

【品質自查】
生成後，請自行確認：
1. 每個答案是否都能在段落中找到原文支撐？
2. 代碼示例是否語法正確？
3. 問題是否具體而非泛泛？
4. 難度標記是否合理？
```

---

## 三、代碼專用生成提示詞

*用於段落中包含代碼塊時。參數：`{book_title}`, `{chapter}`, `{code_block}`, `{surrounding_text}`*

```text
請根據以下代碼塊及其上下文，生成 3 個代碼相關的問答對。

【來源】
書籍：{book_title}
章節：{chapter}

【上下文說明】
{surrounding_text}

【代碼塊】
{code_block}

【問題類型要求】
必須覆蓋以下三種代碼問題：
1. 語法/語義：「這段代碼中的 `{關鍵語法}` 起了什麼作用？」
2. 行為預測：「如果輸入是 X，這段代碼的輸出是什麼？為什麼？」
3. 修改/擴展：「如何修改這段代碼以實現 Y？」

【輸出格式】
```json
[
  {
    "question": "具體的代碼相關問題",
    "answer": "包含代碼解釋的準確回答",
    "type": "code",
    "difficulty": "beginner|intermediate|advanced",
    "code_block": "相關的代碼片段（如有）",
    "source_quote": "答案對應的原文關鍵句"
  }
]
```

【代碼品質要求】
- 答案中的代碼必須可直接運行（或明確標註偽代碼）
- 代碼縮排必須正確
- 變數命名必須有意義
```

---

## 四、多樣性校正提示詞

*用於批量生成後檢查多樣性。參數：`{batch_questions_json}`*

```text
以下是同一本書中已生成的問題列表。請檢查是否存在重複或同質化問題。

【已生成問題】
{batch_questions_json}

【檢查維度】
1. 問題句式多樣性：是否過多使用「什麼是」開頭？
2. 概念覆蓋面：是否遺漏了段落中的重要概念？
3. 難度分佈：beginner/intermediate/advanced 的比例是否合理（建議 3:4:3）？
4. 類型分佈：concept/code/comparison/howto/why 是否均有覆蓋？

【輸出】
```json
{
  "duplicate_pairs": [{"q1": "問題1", "q2": "問題2", "reason": "重複原因"}],
  "missing_concepts": ["段落中存在但未被提問的概念"],
  "difficulty_balance": {"beginner": N, "intermediate": N, "advanced": N},
  "type_balance": {"concept": N, "code": N, "comparison": N, "howto": N, "why": N},
  "suggestions": ["補充建議1", "補充建議2"]
}
```
```

---

## 五、品質過濾提示詞

*用於自動過濾。參數：`{qa_pair_json}`*

```text
請評估以下問答對的品質，判斷是否應保留用於訓練。

【問答對】
{qa_pair_json}

【評分標準（每項 0-2 分）】
1. 準確性（2=完全準確，0=存在事實錯誤）
2. 具體性（2=問題非常具體，0=泛泛而談）
3. 原文支撐（2=答案完全可追溯到原文，0=無法追溯）
4. 代碼正確性（如有代碼，2=可運行，0=有語法錯誤；無代碼則給 2）
5. 教學價值（2=對學習者很有幫助，0=無意義）

【輸出】
```json
{
  "scores": {"accuracy": N, "specificity": N, "grounding": N, "code_correctness": N, "teaching_value": N},
  "total": N,
  "verdict": "keep|revise|reject",
  "reason": "判斷理由（一句話）",
  "revision_note": "如需修改，說明修改建議"
}
```
## 六、人工審查引導提示詞

*此提示詞嵌入 Obsidian 筆記模板中，作為審查者的檢查清單。*

```text
## 審查檢查清單

在將此 Q&A 標記為「已審查」之前，請逐項確認：

### 準確性
- [ ] 答案中的每一個事實陳述都能在原文中找到對應
- [ ] 沒有添加原文中不存在的 API 名稱、版本號或函數簽名
- [ ] 代碼塊的語法正確（已實際運行或目視檢查）

### 具體性
- [ ] 問題不是「這段在講什麼」式的泛泛問題
- [ ] 問題包含足夠的上下文，脫離原文也能理解
- [ ] 答案直接回應問題，沒有繞圈子

### 教學價值
- [ ] 這個問題對學習者來說是有意義的（不是無聊的細節）
- [ ] 答案的深度與難度標記一致
- [ ] 如果學習者只讀這個 Q&A，能獲得正確的理解

### 格式
- [ ] frontmatter 中的 type、difficulty、source_book 欄位正確
- [ ] 代碼塊使用了正確的語言標記（```python、```javascript 等）
- [ ] source_quote 確實是原文中的一句話

### 審查操作
- 通過：將 `review_status: pending` 改為 `review_status: approved`
- 需修改：直接編輯答案，然後改為 `review_status: approved`
- 拒絕：將 `review_status: pending` 改為 `review_status: rejected`，並在下方註明拒絕原因
```

---

## 七、完整調用序列

```python
# pipeline_qa_generation.py — 按順序調用各提示詞

PROMPTS = {
    "system": "...",           # 一、系統提示詞
    "generate": "...",         # 二、核心生成
    "generate_code": "...",    # 三、代碼專用
    "diversity_check": "...",  # 四、多樣性校正
    "quality_filter": "...",   # 五、品質過濾
    "review_guide": "...",     # 六、審查引導（嵌入筆記模板）
}

def generate_qa_pipeline(chunks, book_meta):
    """完整 Q&A 生成管線"""
    all_qa_pairs = []

    for chunk in chunks:
        # 1. 選擇提示詞
        if chunk["has_code"]:
            prompt = PROMPTS["generate_code"]
        else:
            prompt = PROMPTS["generate"]

        # 2. 生成 Q&A
        raw_qa = llm.generate(
            system=PROMPTS["system"],
            prompt=prompt.format(
                book_title=book_meta["title"],
                chapter=chunk["chapter"],
                page=chunk["page"],
                paragraph_text=chunk["text"],
                code_block=chunk.get("code", ""),
                surrounding_text=chunk.get("context", "")
            )
        )

        # 3. 品質過濾
        for qa in parse_json(raw_qa):
            quality = llm.generate(
                system=PROMPTS["system"],
                prompt=PROMPTS["quality_filter"].format(qa_pair_json=json.dumps(qa))
            )
            result = parse_json(quality)
            if result["verdict"] == "keep":
                qa["review_status"] = "pending"
                all_qa_pairs.append(qa)
            elif result["verdict"] == "revise":
                qa["review_status"] = "needs_revision"
                qa["revision_note"] = result["revision_note"]
                all_qa_pairs.append(qa)

    # 4. 多樣性檢查
    diversity = llm.generate(
        system=PROMPTS["system"],
        prompt=PROMPTS["diversity_check"].format(
            batch_questions_json=json.dumps([q["question"] for q in all_qa_pairs])
        )
    )

    return all_qa_pairs, parse_json(diversity)
```

---

## 八、使用鐵律

1. **永遠先跑系統提示詞中的「你永遠不應該」清單作為自檢**——在正式生成前，讓 LLM 自己複述一遍禁止事項
2. **每批 50 條後跑一次多樣性檢查**——不要等全書生成完才發現問題句式集中
3. **source_quote 欄位不可省略**——這是人工審查時對照原文的唯一快速入口
4. **代碼塊必須標註語言**——否則匯出 JSONL 時無法正確格式化
5. **審查引導提示詞嵌入筆記本體**——不依賴審查者記憶，每一條 Q&A 自帶檢查清單

---

%% 變更記錄 %%

**變更記錄**
- 2026-07-22：v1.0 初始版本，六組提示詞 + 調用序列 + 使用鐵律