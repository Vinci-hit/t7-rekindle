# 账户称号

称号归属与佩戴选择由认证服务保存，启动器与游戏内共用一份状态。首批复用原游戏称号，不引入规则编辑器。

| 原游戏 ID | 称号 | 固定获取方式 |
| --- | --- | --- |
| `1007` | 首测元老 | 管理员授予 |
| `1008` | 内测精英 | 北京时间 2026 年 10 月内，功能上线后成功登录并上线 |

登录奖励窗口为北京时间 `[2026-10-01 00:00, 2026-11-01 00:00)`，服务端按数据库时间判定。仅新的成功上线请求触发发放；迁移、历史登录记录、恢复凭证、读取称号和心跳均不补发。

## 使用入口

- **启动器**：右上角账户菜单 → 我的称号。查看介绍、获取方式和持有状态，佩戴或卸下称号。
- **游戏内**：沿用原客户端个人信息中的称号入口与佩戴操作。选择经启动器向认证服务提交，成功后才更新游戏状态。
- **管理后台**：称号管理维护介绍文案、排序与展示开关；账户详情 → 称号管理执行授予或撤销，写入操作日志。

两种称号均永久有效，同一账户同一称号仅保留一条持有记录。新授予且当前未佩戴时自动佩戴；重复登录、重复授予不会改变玩家已保存的选择。

撤销保留记录并卸下该称号，之后登录不自动重新授予，管理员可以显式再次授予。关闭展示不撤销持有权，但会卸下并禁止佩戴；重新开放不自动戴回。

## 同步与文案

上线后和每次成功心跳后读取称号，正常网络下约每 15 秒同步。离线、会话失效、被其他连接接管时启动器和游戏内隐藏账户称号；重新上线后从服务端恢复。请求失败不乐观修改佩戴状态，旧账户或旧在线连接的响应不覆盖当前状态。

介绍使用纯文本，最多 64 个 GBK 字节（约 32 个汉字），不接受 HTML、控制字符或 GBK 未收录的字符。名称、ID、获取规则与时间窗口不开放编辑。游戏内介绍追加固定获取规则，在原资源的 128 字节说明字段内以 GBK 展示。

游戏内使用原有 `USER_INFO`（命令 `31`）协议：基础信息、统计信息中的称号列表和当前称号，以及佩戴请求／响应。只更新客户端内存中的两条称号说明并调用原界面刷新函数，不改写、复制或分发客户端资源文件；原始函数签名不匹配时停止适配。

## 部署

1. 通过服务端现有 `--migrate` 流程执行 `V006__account_titles.sql`，新增目录、持有记录和佩戴选择表。迁移仅初始化目录，不发放奖励。
2. 部署服务端，再更新启动器、NativeBridge 和 Business 整套客户端组件；新增原生导出不改变既有 ABI 1 结构布局。
3. 在后台核对文案与展示开关。自动奖励从新版本服务处理的成功上线请求开始，不扫描旧记录。

接口为 `GET /api/v1/auth/online/{connectionId}/titles`、`PUT .../titles/equipped`，后者传入 `{"titleId":1008}` 或 `{"titleId":null}`。均沿用 Bearer、机器绑定和在线归属校验，详见 [OpenAPI](../server/contracts/openapi-auth-v1.yaml)。旧服务缺少接口时保留本地游戏功能，不伪造称号归属。

## 专项验证

在仓库根目录执行，构建与测试要求见[开发说明](development.md)：

```powershell
mvn -f server/pom.xml '-Dtest=TitleServiceTest,PresenceServiceTest,ServerIntegrationTest#title*+startupMigratesBeforeInitializingAdmin' test
python -m pytest -q tests/python/test_titles.py
python scripts/build.py --project managed
& ./artifacts/bin/T7R.ManagedHarness/x64/Release/net48/T7R.ManagedHarness.exe --title-tests ./artifacts/test-results/titles
```

原生测试工程编译后，可执行 `T7R.RuntimeTests.exe --title-tests` 验证 x86 帧线程说明更新、原刷新调用及寄存器保持；`--title-runtime PACKAGE_ROOT` 验证原生／Python 请求、确认、超时与账户代次隔离，`PACKAGE_ROOT` 使用包含 Business 和 Python 运行时的合成测试目录。实机验收应在游戏大厅和个人信息页检查登录奖励、佩戴／卸下、后台撤销／暂停展示，以及断网后恢复。
