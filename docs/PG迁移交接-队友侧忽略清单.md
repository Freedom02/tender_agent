# PG 迁移交接：队友侧需要忽略 / 对齐的清单

> 用途：我方在本仓库做了一套完整的 PostgreSQL 迁移工作（schema `001`~`008`、Repository 层、本地 Docker、回填与对拍脚本）。
> **你已经自己建好库了，所以下面大部分内容你不需要采纳。**
> 本清单说明：什么可以完全忽略、什么要先和你的库对比、哪些是无关改动、哪几个文件会和你冲突。
>
> 生成时间：本次交接。若文档有更新，以仓库内最新版为准。

---

## 0. 一句话

我方 PG 工作**已全部 push**（分支 `integrate/merge-origin-main` 与 `feat/document-map-agent-clean-20260923`）。
**`main` 是纯 JSON 状态，没有任何 PG 东西**——所以不会污染你的主线。

---

## 1. ✅ 可以完全忽略（我方本地环境专用）

| 路径 | 是什么 | 为什么你可以忽略 |
|:--|:--|:--|
| `docker-compose.yml` | 我方本地 Docker PG（容器 `tenderagent-pg`，端口 5433） | 你用自己的库，不需要 |
| `scripts/db_init.ps1` | 建本地库 + 跑迁移的脚本 | 同上 |
| `_backfill/` | 本地回填产物（含 3MB 生成 SQL、2.9MB `expected.json`） | 一次性产物，**已 gitignore**，仓库里不会有 |
| `_verify006/` | 本地验证证据（视图对拍报告、schema dump、pytest 输出） | 同上 |

> 这两个目录我方已**外部备份**（仓库外的 `pg-work-backup/`），需要看的话找我要。

---

## 2. ⚠️ 需要你先和你的库对比（PG schema 设计 —— 本清单最要紧的一项）

| 路径 | 是什么 |
|:--|:--|
| `backend/migrations/001_schema_v1.sql` | 主 schema：48 表 + 4 视图（1078 行） |
| `backend/migrations/002_missing_tables.sql` | 补漏表 |
| `backend/migrations/003_merge_duplicate_tables.sql` | 合并重复表 |
| `backend/migrations/004_bid_reading_columns.sql` | 标书**读**方向补列（13 列） |
| `backend/migrations/005_comments.sql` | 全量中文注释 + 治理元数据 |
| `backend/migrations/006_bid_writing_tables.sql` | 标书 G 组补列 + 6 张新表（含 `parse_bundle` 等） |
| `backend/migrations/007_bid_views_v2.sql` | H 组视图重建 + 新增参数级偏离视图 |
| `backend/migrations/008_governance_sync.sql` | 治理元数据同步（54 表 / 1082 列） |

> **不要直接跑这些迁移。** 先确认：你的库是不是也基于这套设计？
> - 如果你的库**已经跑过 `001`~`005`** → 兼容，只补 `006`~`008` 即可
> - 如果你的库是**你自己设计的** → 这是**两套竞争设计**，先对比再决定，别硬跑

---

## 3. 与我方 schema 配套、但可独立忽略

| 路径 | 是什么 | 备注 |
|:--|:--|:--|
| `backend/app/repository/` | Repository 层（9 文件），`json_impl` 直连、**零行为变更** | 是"切 PG"的地基，跟你库设计无关，可忽略 |
| `scripts/backfill_bid_writing.py` | 把 JSON 数据回填进我方表 | 绑定我方的表结构，你的表不同就用不了 |
| `scripts/compare_bid_writing.py` | JSON vs PG 对拍 | 同上 |
| `backend/migrations/README.md` | 迁移说明 | 参考 |

---

## 4. ⚠️ 不是本次 PG 工作的改动（进行中，别当成本次交付）

| 路径 | 说明 |
|:--|:--|
| `backend/app/document_recognition/storage.py` | 与 PG **无关**的进行中改动 |
| `backend/tests/test_eligibility_knowledge.py` | 同上 |
| `scripts/gen_field_checklist.py` | 字段清单生成脚本（服务于 PG 核对，本身不影响运行） |

> 这三个是顺带提交的未完成改动，**不是 PG 迁移的组成部分**，你合并时可以直接跳过。

---

## 5. 可作参考、不必采纳（设计文档）

| 文档 | 内容 | 建议 |
|:--|:--|:--|
| `docs/标书表替换可行性核对.md` | 标书表能否替换的核对结论（写方向 §二/§三、读方向 §六、工作清单 §八） | **如果你关心标书模块，这份值得看** |
| `docs/PostgreSQL表字段总清单与补表计划.md` | 表的总体计划（批次、补表清单） | 参考 |
| `docs/Repository层设计与读写点清单.md` | Repository 层设计 + 13 处接线点 + 合并事故复盘 | 参考 |
| `docs/交接提示词-标书表006迁移.md` | 交接提示词 | 参考 |

---

## 6. ⚠️ 真正需要**对齐**的（不是忽略，是冲突点）

| 项 | 风险 |
|:--|:--|
| `backend/app/store.py` | 我方已提交 Repository 接线（commit `323c8aa`，12 处 `_*_repo` 引用）。**如果你也改了这个文件，合并必冲突** |
| `backend/migrations/` | 如果你也有同名 `001_*.sql` 之类的文件，合并冲突 |
| 分支关系 | 我方 PG 工作在 `integrate/merge-origin-main` + `feat/document-map-agent-clean-20260923`（均已 push）；`main` 无 PG |

---

## 7. 我方需要你回答的三件事（决定后面怎么走）

1. **你的库 schema 从哪来的？** 跑了我方的 `001`~`005`，还是你自己设计的？
2. **你 push 到 `main` 的内容里，有 `backend/migrations/` 吗？动过 `backend/app/store.py` 吗？**
3. **库连接信息**（或导出的 schema DDL）能否给我？我有现成脚本，能直接出一份"**你的库缺什么**"的清单。

---

## 8. 附：怎么快速审计一个库（我方已有工具链）

拿到任意一个库的连接信息后，按这个顺序就能判断"这库好不好使"：

| 工具 | 作用 |
|:--|:--|
| `information_schema.columns` dump | 拿到实际表/列清单（我方样例见外部备份 `_verify006/db_columns.txt`） |
| `_verify006/diff_checklist_vs_db.py` | **期望清单 vs 实际数据库** 差异对比 |
| `_verify006/read_direction.sql` | 读方向验证（标书侧要读的 key 能否取到） |
| `_verify006/parity.sql` | 视图对拍（SQL 视图 vs Python 派生，逐字一致） |
| `docs/PostgreSQL表字段总清单与补表计划.md` | 期望清单的来源 |

> 我方已用这套流程验过自己的库：视图对拍 **9 个 case 全 PASS**（含空值回退串、多条目排序、中文路径），数据回填 27 项目 / 18 工作区已落地并记录了数据缺口。
