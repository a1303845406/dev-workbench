# AOCI 源码认知维护

## 资产与边界

- `aoci.txt`：根索引，声明正式卷；`aoci.meta.txt`：标签/FRAS 合同；`aoci.code.txt`：当前受管源文件的语义条目。
- `.aoci/config.json`：版本化的范围与预算策略；`.aoci` 其他本地基线、审计、候选与恢复状态按其自身 gitignore 管理，不手造收据。
- 当前 AOCI CLI/MCP：0.1.0-rc21。通过 `aoci mcp --repo <仓库绝对路径>` 接入；二进制应由开发者安装，不把本机磁盘路径写死成团队配置。
- 真实运行数据库不属于源码索引输入；schema.sql 作为源代码描述存储结构，不声称已经建立生产数据库实时索引。

## 范围

当前应用、测试、工程配置与当前规范均纳入；legacy、docs/archive、workspace、backups、node_modules、构建产物排除。冻结验收记录观察而不当成可执行源码。修改范围必须通过 `aoci scope` 的 plan/apply 治理，不为减少批次而缩减覆盖。

## 每次功能变更

1. 遵循 AGENTS 中当前 AOCI 运行合同；需要完整认知时用 MCP rules/overview，不用旧摘要冒充当前索引。
2. 源码达到本次最终稳定状态并跑通测试后，运行 `aoci index agent guide --agent codex --json`，按实时 Guide 继续。
3. Volumes v1 使用无参数 MCP `aoci_maintain` 领取完整机器批次。读实际源码和必要关系后，由模型独立创作标签与 F/R/A/S；脚本可以传输字节，不能根据文件名/AST自动填语义。
4. 用 MCP `aoci_update_entry` 提交完整批次，原样携带 source_sha256、candidate_id 与 code_plan.batch_id。remaining 非零继续下一批；修复/恢复/审批门禁不能跳过。
5. 最后一批成功后依次运行 `aoci verify --json`、`aoci check --json`、Guide，记录真实覆盖/漂移/预算结果。不能把有空条目的骨架称为完整索引。

不要在当前 Volumes v1 布局使用仅兼容旧布局的写入命令；不要执行会额外计费的 index build 来替代已授权的本地宿主模型创作。CI 先验证代码质量；AOCI 完整语义维护由可使用当前版本 MCP 的 Agent 执行，不将缺少 AOCI 二进制的 CI 声称为已验证认知。
