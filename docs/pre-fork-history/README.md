# Pre-fork PR historical record

This folder preserves the original PR titles, descriptions, author/time metadata, head commit identifiers, and issue-level comments from `kame447/orrery-telemetry-pre-fork-history` (47 PRs) before potential deletion of the old repository. None of these records means that the changes have been newly merged.

Original GitHub approvals, full CI logs, individual event timelines and deleted attachment payloads are not reproduced. Metadata for the 39 old CI runs referenced from upstream and the migrated fork is preserved in [ci-runs.md](ci-runs.md). Original issue references inside fenced archived text refer to legacy numbering. All active work should take place in `kame447/orrery-telemetry`.

| Legacy PR | Original state | Current successor | Title |
| --- | --- | --- | --- |
| [PR 1](pr-0001.md) | MERGED | archived only | Merge Python 3.10 and baseline portability fixes |
| [PR 2](pr-0002.md) | CLOSED_UNMERGED | archived only | feat: add Google Antigravity / Gemini provider |
| [PR 3](pr-0003.md) | CLOSED_UNMERGED | archived only | chore: sync upstream master |
| [PR 4](pr-0004.md) | CLOSED_UNMERGED | archived only | fix(dashboard): Codex の zsh wrapper を正しく判定して EXIT を修正 |
| [PR 8](pr-0008.md) | MERGED | archived only | ci: deduplicate branch runs and cancel superseded builds |
| [PR 9](pr-0009.md) | MERGED | archived only | chore: sync fork master with upstream |
| [PR 10](pr-0010.md) | CLOSED_UNMERGED | archived only | chore: integrate Gemini prototype onto current upstream |
| [PR 13](pr-0013.md) | MERGED | archived only | feat: Dashboard の NEW AGENT に Antigravity / Gemini を統合 |
| [PR 14](pr-0014.md) | CLOSED_UNMERGED | archived only | feat: Dashboard に provider 使用量を統合表示する |
| [PR 17](pr-0017.md) | CLOSED_UNMERGED | archived only | chore: sync upstream master into Gemini integration branch |
| [PR 18](pr-0018.md) | CLOSED_UNMERGED | archived only | chore: rebase Gemini integration onto upstream master |
| [PR 27](pr-0027.md) | CLOSED_UNMERGED | archived only | feat: 対象directoryからproject keyを決めるresolverを分離する（upstream #33・第1段階） |
| [PR 28](pr-0028.md) | CLOSED_UNMERGED | archived only | test: bootstrap identity 検証を実 HOME / Mail から隔離する（#21） |
| [PR 29](pr-0029.md) | CLOSED_UNMERGED | archived only | fix(mail): 通知 signal に canonical project key を含める（upstream #33） |
| [PR 30](pr-0030.md) | CLOSED_UNMERGED | archived only | fix(graph): project 未解決時の別 project 表示を防ぎ、読み取り境界を分離する（upstream #33） |
| [PR 31](pr-0031.md) | CLOSED_UNMERGED | archived only | chore: sync upstream master into provider quota split branch |
| [PR 32](pr-0032.md) | MERGED | archived only | refactor: provider quota acquisition を UI から分離 |
| [PR 35](pr-0035.md) | CLOSED_UNMERGED | archived only | feat: current upstream 上で invocation project resolver を再構成する（upstream #33） |
| [PR 36](pr-0036.md) | CLOSED_UNMERGED | archived only | feat: resolve coherent top-level invocation context（upstream #33） |
| [PR 37](pr-0037.md) | CLOSED_UNMERGED | archived only | fix: top-level launcher を invocation project context に接続する（upstream #33） |
| [PR 38](pr-0038.md) | CLOSED_UNMERGED | archived only | Phase 4: bind bootstrap registration and session identity to workspace ownership |
| [PR 39](pr-0039.md) | CLOSED_UNMERGED | archived only | feat: 登録ownershipの非有効化基盤を切り出す（#33 Phase 4a） |
| [PR 40](pr-0040.md) | CLOSED_UNMERGED | archived only | fix: bootstrapとsession identityを検証済みownershipへ接続する（#33 Phase 4b） |
| [PR 41](pr-0041.md) | CLOSED_UNMERGED | archived only | fix: reservationをhook payloadのworkspace境界に結び付ける（#33 Phase 5a） |
| [PR 42](pr-0042.md) | CLOSED_UNMERGED | archived only | fix: bind reservation checks and releases to the actual workspace (#33 Phase 3) |
| [PR 43](pr-0043.md) | CLOSED_UNMERGED | archived only | fix: child cleanupをvalidated ownerに結び付ける（#33 Phase 5b） |
| [PR 44](pr-0044.md) | CLOSED_UNMERGED | archived only | fix: pre-registered child handoffをvalidated ownershipへ接続する（#33 Phase 5c） |
| [PR 45](pr-0045.md) | CLOSED_UNMERGED | archived only | fix: Gemini child lifecycleをvalidated ownershipへ接続する（#33 Phase 5d） |
| [PR 46](pr-0046.md) | CLOSED_UNMERGED | archived only | fix: bind child handoff and cleanup to the validated project (#33 Phase 4) |
| [PR 47](pr-0047.md) | CLOSED_UNMERGED | archived only | Phase 5: isolate notification state and delivery by project |
| [PR 48](pr-0048.md) | CLOSED_UNMERGED | archived only | Phase 7a: scope Dashboard graph reads to the selected project |
| [PR 49](pr-0049.md) | CLOSED_UNMERGED | archived only | Phase 7b1: add Dashboard project session attribution primitives |
| [PR 50](pr-0050.md) | CLOSED_UNMERGED | archived only | Dashboard read isolation: project-scoped reads, metadata and caches |
| [PR 51](pr-0051.md) | CLOSED_UNMERGED | archived only | Dashboard control isolation: bind mutations and spawn to project ownership |
| [PR 52](pr-0052.md) | CLOSED_UNMERGED | archived only | fix: isolate top-level project context by launch target |
| [PR 53](pr-0053.md) | CLOSED_UNMERGED | archived only | fix: validate core registration against target project context |
| [PR 55](pr-0055.md) | MERGED | archived only | chore: sync Phase 3 with current Phase 2 |
| [PR 57](pr-0057.md) | MERGED | archived only | fix(mail): SQLModel 0.0.45+ の breaking datetime default を一時回避する |
| [PR 63](pr-0063.md) | CLOSED_UNMERGED | archived only | feat: child の自動ターミナル表示を分離し、Deck の手動表示を維持する |
| [PR 64](pr-0064.md) | MERGED | archived only | feat: Claudeのモデル候補をローカルカタログから自動検出する（#59） |
| [PR 66](pr-0066.md) | CLOSED_UNMERGED | archived only | fix: 起動時の project key を env.sh と tmux の古い値から保護する |
| [PR 68](pr-0068.md) | MERGED | archived only | fix: 起動先と共有フォルダの予約保護を更新後も維持する |
| [PR 69](pr-0069.md) | CLOSED_UNMERGED | archived only | refactor: 予約保護の起動 context を必要最小限にする |
| [PR 71](pr-0071.md) | MERGED | archived only | chore: upstream/master の最新変更を fork に同期 |
| [PR 72](pr-0072.md) | MERGED | archived only | feat: Antigravity / Gemini のモデル候補を agy CLI に自動追従させる (#70) |
| [PR 75](pr-0075.md) | CLOSED_UNMERGED | archived only | fix: macOS provenance 属性での指示文移行失敗を安全に解消する |
| [PR 76](pr-0076.md) | CLOSED_UNMERGED | archived only | ci: protect Python dependency installs with Takumi Guard |
