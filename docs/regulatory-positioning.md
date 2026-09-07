# Regulatory positioning / 規制との位置づけ

> **Last checked: 2026-09-07.** Every date and source below was verified on
> that day. See [Maintenance](#maintenance) for when this file must be
> re-checked. 日本語は[後半](#日本語)。

## What AxonRelay is, and is not

AxonRelay is a personal proof of concept of a durable, tamper-evident approval
ledger for mixed human + AI teams, plus a coordination board for parallel
agents.

**AxonRelay is not a compliance product and not a certification mechanism.**

- It does not make any system compliant with the EU AI Act or any other
  regulation, and it does not claim to.
- It does not certify, attest, or provide legal evidence of anything. The
  ledger's hash chain is *tamper-evident* (you can detect that a stored entry
  was altered); it is not qualified electronic signing, trusted timestamping,
  or non-repudiation. Those are explicitly out of scope
  ([delta-mvp-spec §8](./delta-mvp-spec.md)).
- Nothing in this repository is legal advice.

## Why regulation is mentioned at all

Some requirements the EU AI Act places on *high-risk* AI systems overlap in
theme with what an approval ledger records: automatic event logging
(Article 12) and human oversight (Article 14). That overlap is the only reason
the topic appears in the README. It is a design motivation, not a claim that
AxonRelay implements, satisfies, or maps to those articles.

## Timeline facts (as checked on 2026-09-07)

The AI Act is Regulation (EU) 2024/1689. Its application is phased
(Article 113). The **Digital Omnibus on AI, Regulation (EU) 2026/1744, in force
since 2026-07-27**, deferred the high-risk obligations.

| Date | What applies | Status on 2026-09-07 |
|---|---|---|
| 2025-02-02 | Prohibited practices (Chapter II) and AI literacy | applicable |
| 2025-08-02 | Governance, penalties, general-purpose AI model obligations | applicable |
| 2026-08-02 | General application date, including transparency obligations (Article 50) | applicable |
| 2027-12-02 | High-risk obligations for stand-alone systems listed in Annex III (Article 6(2)) | **deferred to this date by 2026/1744** |
| 2028-08-02 | High-risk obligations for systems embedded in products under Annex I legislation (Article 6(1)) | **deferred to this date by 2026/1744** |

Consequences for this repository's wording:

- "The high-risk obligations reach full enforcement on 2026-08-02" is **false**
  and was removed from both READMEs and the pivot specification. The
  2026-08-02 date is the general application date and the start of the
  transparency rules, not the start of high-risk enforcement.
- Transparency-rule dates and high-risk application dates are different and
  must not be merged into one milestone.
- The earlier "three pillars" shorthand (tamper-resistant action log, human
  approval gate, identity attribution) was our summary, not the Act's
  structure. The Act's high-risk chapter has many more requirements (risk
  management, data governance, technical documentation, accuracy and
  robustness, conformity assessment, registration, ...), none of which
  AxonRelay addresses.

## Primary sources

| Source | URL |
|---|---|
| Regulation (EU) 2024/1689 (AI Act), consolidated text incl. Article 113 | https://eur-lex.europa.eu/eli/reg/2024/1689/oj |
| Regulation (EU) 2026/1744 (Digital Omnibus on AI) | https://eur-lex.europa.eu/eli/reg/2026/1744/oj |
| European Commission, AI Act policy page (application timeline) | https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai |
| Council of the EU press release, 2026-06-29 (final adoption of the omnibus) | https://www.consilium.europa.eu/en/press/press-releases/2026/06/29/artificial-intelligence-council-gives-final-green-light-to-simplify-and-streamline-rules/ |
| Commission AI Act Service Desk, Article 113 | https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-113 |

Secondary commentary was used only to locate the primary sources above; do not
cite it in this repository.

## Maintenance

Time-sensitive claims rot. Whoever touches a regulatory sentence must:

1. Re-read the primary sources above, update the table, and move the
   **Last checked** date at the top of this file.
2. Re-check unconditionally when any of these dates passes: **2027-12-02**,
   **2028-08-02**, and whenever a further amendment to 2024/1689 is published.
3. Keep the English and Japanese texts (this file, `README.md`, `README.ja.md`)
   stating the same limits. A change to one is a change to both.

## Review checklist for documentation

Apply to any change under `README*.md`, `docs/`, `deploy/`:

- [ ] No sentence says or implies that AxonRelay is compliant with, certified
      under, or satisfies a regulation or standard.
- [ ] Every regulatory date is present in the table above, or the table was
      updated in the same change, with the **Last checked** date moved.
- [ ] No source is cited that is not a primary source (Official Journal,
      Commission, Council, Parliament).
- [ ] The Japanese and English texts convey the same limits.
- [ ] `python3 scripts/check_doc_claims.py` passes. It is a lightweight
      guard against phrases that reintroduce unsourced claims; it does not
      judge accuracy, only wording. The phrase list is in the script. A line
      that legitimately needs a flagged phrase (a quotation, a historical
      note) can carry `<!-- claim-check:allow -->`; a block can be wrapped in
      `<!-- claim-check:off -->` ... `<!-- claim-check:on -->`.

---

## 日本語

> **最終確認日: 2026-09-07。** 以下の日付と出典はすべてこの日に一次資料で確認した。
> 再確認の条件は[保守](#保守)を参照。

### AxonRelay が何であり、何でないか

AxonRelay は、人と AI の混成チーム向けの永続的で改ざん検知可能な承認台帳と、
並行エージェント向けの調整ボードの、個人 PoC である。

**AxonRelay はコンプライアンス製品ではなく、認証（certification）の仕組みでもない。**

- どのシステムも EU AI Act その他の規制に適合させないし、そう主張もしない。
- 何かを証明・認定・法的に立証することはない。台帳の hash chain は
  *改ざん検知*（保存済みエントリの改変を検出できる）であって、適格電子署名・
  タイムスタンプ局・否認防止ではない。それらは明示的に対象外である
  （[delta-mvp-spec §8](./delta-mvp-spec.md)）。
- このリポジトリの内容は法的助言ではない。

### なぜ規制に触れるのか

EU AI Act が*高リスク* AI システムに課す要件のうち、自動イベント記録（第 12 条）と
人間による監督（第 14 条）は、承認台帳が記録するものと主題が重なる。README で
規制に触れる理由はこの重なりだけであり、設計上の動機である。AxonRelay が
これらの条文を実装・充足・対応しているという主張ではない。

### 時系列の事実（2026-09-07 確認）

AI Act は Regulation (EU) 2024/1689 で、適用は段階的（第 113 条）。
**Digital Omnibus on AI（Regulation (EU) 2026/1744、2026-07-27 発効）** が
高リスク義務の適用を延期した。

| 日付 | 適用されるもの | 2026-09-07 時点 |
|---|---|---|
| 2025-02-02 | 禁止行為（第 II 章）と AI リテラシー | 適用中 |
| 2025-08-02 | ガバナンス、罰則、汎用 AI モデルの義務 | 適用中 |
| 2026-08-02 | 一般適用日。透明性義務（第 50 条）を含む | 適用中 |
| 2027-12-02 | Annex III 記載のスタンドアロン高リスクシステム（第 6 条 2 項）の義務 | **2026/1744 によりこの日へ延期** |
| 2028-08-02 | Annex I 法令下の製品に組み込まれる高リスクシステム（第 6 条 1 項）の義務 | **2026/1744 によりこの日へ延期** |

このリポジトリの記述への帰結:

- 「高リスク義務は 2026-08-02 に full enforcement」は**誤り**であり、両 README と
  ピボット仕様から削除した。2026-08-02 は一般適用日かつ透明性規則の開始日であって、
  高リスク義務の執行開始日ではない。
- 透明性規則の日付と高リスク義務の適用日は別物で、一つの節目にまとめてはならない。
- 以前の「3 本柱」（改ざん耐性の行動ログ・人間承認ゲート・identity 帰属）は
  こちら側の要約であって、法の構造ではない。高リスク章にはリスク管理・データガバナンス・
  技術文書・正確性と頑健性・適合性評価・登録など多数の要件があり、AxonRelay は
  そのいずれにも対応しない。

### 一次資料

英語節の [Primary sources](#primary-sources) の表と同一。

### 保守

1. 規制に関する文を触る人は、上記一次資料を読み直し、表を更新し、
   ファイル冒頭の **Last checked / 最終確認日** を進める。
2. **2027-12-02**、**2028-08-02** を過ぎたとき、および 2024/1689 の追加改正が
   公布されたときは無条件に再確認する。
3. 英語と日本語（本ファイル・`README.md`・`README.ja.md`）は同じ限定を述べ続ける。
   片方の変更はもう片方の変更である。

### ドキュメントのレビューチェックリスト

`README*.md`、`docs/`、`deploy/` 配下の変更に適用する:

- [ ] AxonRelay が規制や規格に準拠・認証取得・充足していると述べる、または
      示唆する文がない。
- [ ] 規制の日付はすべて上の表にあるか、同じ変更で表を更新し **最終確認日** を進めた。
- [ ] 一次資料（官報・欧州委員会・理事会・欧州議会）以外を出典にしていない。
- [ ] 日本語と英語が同じ限定を伝えている。
- [ ] `python3 scripts/check_doc_claims.py` が通る。これは根拠のない主張を再導入する
      言い回しへの軽量ガードで、正確さではなく文言だけを見る。語句一覧はスクリプト内。
      引用や履歴など正当に必要な行には `<!-- claim-check:allow -->` を付け、
      ブロックは `<!-- claim-check:off -->` … `<!-- claim-check:on -->` で囲める。
