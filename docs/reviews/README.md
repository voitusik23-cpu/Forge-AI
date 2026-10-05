# External reviews

This directory holds reviews produced by external or independent reviewers.

These documents are **input to Forge decisions, not decisions themselves**.
A review may contain incorrect claims about the implementation; only the code
and the passing test suite establish current state. Accepted conclusions belong
in [`../DECISIONS.md`](../DECISIONS.md); verified current state belongs in
[`../SOURCE_OF_TRUTH.md`](../SOURCE_OF_TRUTH.md) and
[`../ARCHITECTURE.md`](../ARCHITECTURE.md).

## Contents

| Document | Reviewer | Target checkpoint | Notes |
| --- | --- | --- | --- |
| [`FOR_DEEPSEEK_REVIEW.md`](FOR_DEEPSEEK_REVIEW.md) | DeepSeek | `e335e14878d9877e61bb34acec9a179c472ce28f` (`feat: add knowledge governance v0.1`) | Architecture and engineering review package. Predates the execution authorization v0.2 work, so its execution-plane claims describe the v0.1 state. |

## Rules for adding a review

1. Keep the reviewer's text intact. Do not rewrite an external review to match
   the current code; if the code moved on, say so in the table above.
2. Record the exact commit the review targeted. A review without a checkpoint
   cannot be audited later.
3. Do not copy a review into core architecture documents. Summarize the accepted
   conclusions in `DECISIONS.md` and link back here.

---

# Внешние ревью — русская версия

Этот каталог содержит ревью, подготовленные внешними или независимыми
рецензентами.

Эти документы — **входные данные для решений Forge, а не сами решения**. Ревью
может содержать неверные утверждения о реализации; текущее состояние
устанавливают только код и проходящий набор тестов. Принятые выводы принадлежат
[`../DECISIONS.md`](../DECISIONS.md); проверенное текущее состояние — в
[`../SOURCE_OF_TRUTH.md`](../SOURCE_OF_TRUTH.md) и
[`../ARCHITECTURE.md`](../ARCHITECTURE.md).

## Содержимое

| Документ | Рецензент | Целевой checkpoint | Примечания |
| --- | --- | --- | --- |
| [`FOR_DEEPSEEK_REVIEW.md`](FOR_DEEPSEEK_REVIEW.md) | DeepSeek | `e335e14878d9877e61bb34acec9a179c472ce28f` (`feat: add knowledge governance v0.1`) | Пакет ревью архитектуры и инженерии. Предшествует работе по execution authorization v0.2, поэтому его утверждения об execution plane описывают состояние v0.1. |

## Правила добавления ревью

1. Сохраняйте текст рецензента неизменным. Не переписывайте внешнее ревью под
   текущий код; если код ушёл вперёд, укажите это в таблице выше.
2. Фиксируйте точный коммит, на который было нацелено ревью. Ревью без checkpoint
   невозможно проверить позже.
3. Не копируйте ревью в основные архитектурные документы. Резюмируйте принятые
   выводы в `DECISIONS.md` и ссылайтесь сюда.
