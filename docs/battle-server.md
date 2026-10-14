# 战斗运行时与独立宿主

`T7.BattleServer` 是 Windows x64 / .NET Framework 4.8 控制台宿主，复用启动器的
`Server`、嵌入式 CPython 和 `Business`，不创建游戏进程，不依赖 WPF 或 Java 登录。

当前实现是联机计划的宿主与传输基础，**尚不是可注册、可匹配的多人战斗节点**。
Python 战斗状态仍采用现有单客户端模型。多人共享状态、身份映射、远端世界采样、
LAN 房间和 Java 调度按[三模式实施计划](planning/battle-network-v1.md)继续接入。

## 构建与模块验证

使用[开发工具链](development.md#1-准备工具链)，在仓库根目录执行：

```powershell
python scripts/build.py --project battle
python scripts/build.py --project battle-tests
```

- `battle` 构建 NativeBridge、Core、Battle、BattleServer 与 BattleHarness，不运行测试。
- `battle-tests` 另外构建并运行战斗宿主的 C++ 测试和托管 harness；不运行项目级全量测试。
- 两个入口均支持 `--configuration Debug`，不需要构建 WPF。
- 测试使用 `artifacts/tmp/` 下生成的合成资源与独立状态目录，不启动游戏客户端。

## 运行

```powershell
& .\artifacts\bin\T7.BattleServer\x64\Release\net48\T7.BattleServer.exe `
  --package-root 'PACKAGE_ROOT' `
  --client-directory 'TARGET\bin' `
  --state-root 'STATE_ROOT'
```

将占位符替换为实际目录：

| 参数 | 内容 |
| --- | --- |
| `--package-root` | 同版本 `T7.NativeBridge.dll`、CPython 3.14.4 AMD64 运行时及完整 `Business` 的运行目录 |
| `--client-directory` | 客户端 `bin` 目录；其上级目录提供 `vfs` 等原始资源，不启动其中的程序 |
| `--state-root` | 可写状态目录，保存本宿主的资源 revision、业务 profile 和诊断 |

运行目录的 Python 布局与启动器一致：根目录包含 `python314.dll` 及所需运行时 DLL，
`python/` 包含标准库（`python314.zip` 或 `Lib/`）与扩展，`Business/` 包含
`runtime/`、`scripts/` 和 `vendor/`。控制台自己的输出目录包含 `T7.Core.dll`、
`T7.Battle.dll` 及 `.exe.config`。原始客户端和提取资源不随项目分发。

启动成功后输出三个动态回环端口。`Ctrl+C` 停止宿主；参数、资源或运行时错误返回非零退出码。
进程内最多一个活动 Python owner；多个独立宿主使用不同进程和状态目录。

### 生命周期接口

`T7BattleHost.h` 新增独立句柄，不修改已有 `T7NativeSession` ABI 或启动行为。

| 接口 | 语义 |
| --- | --- |
| `t7_battle_host_create` | 校验并复制绝对 UTF-8 路径，不启动运行时 |
| `t7_battle_host_start` | 等待 Python 资源预检和原生监听就绪；重复启动返回 `Busy` |
| `t7_battle_host_snapshot` | 返回状态、私有端口、连接数、计时器数和业务阶段 |
| `t7_battle_host_error` | 按 UTF-8 字节数读取错误，短缓冲区不截断 |
| `t7_battle_host_stop` | 幂等停止，关闭监听、等待工作线程退出 |
| `t7_battle_host_release` | 同步清理并销毁句柄；返回后才可卸载 DLL |

`NativeBattleHost` 封装上述接口，用 SafeHandle 保持 DLL 到原生工作线程退出为止。
这些调用是同步的，UI 集成应在工作线程执行。旧客户端 AUTH 只属于原协议握手，
不代表平台登录或房间准入；三个原协议端口固定绑定 `127.0.0.1`，不直接对外发布。

## BattleProtocol v1 帧基础

`BattleFrame` 表达与实现无关的消息，`BattleConnection` 负责有界可靠传输。
当前已有原协议三通道中继组件，尚未接入准入处理器、启动器和共享比赛状态；
`Admit`、`WorldSample` 等消息体将在接入阶段定义。

所有整数使用大端序。长度前缀为 4 字节，值为其后内容长度，不含前缀自身。

| 帧体偏移 | 大小 | 字段 |
| --- | --- | --- |
| 0 | 1 | 协议版本，当前为 1 |
| 1 | 1 | `BattleMessageKind` |
| 2 | 1 | `BattleChannel`：0 无角色，1 login，2 logic，3 instance |
| 3 | 1 | 保留，必须为 0 |
| 4 | 8 | roomId |
| 12 | 8 | matchId |
| 20 | 8 | playerId |
| 28 | 8 | sequence |
| 36 | 可变 | payload |

消息编号：1 Admit、2 Admitted、3 OpenChannel、4 GameData、5 CloseChannel、
6 WorldSample、7 MatchState、8 MatchEnded、9 Ping、10 Pong。
仅 OpenChannel、GameData、CloseChannel 携带非零原协议角色。

- 帧体范围为 36 字节至 4 MiB，先检查长度再分配接收缓冲区。
- 单连接一个读者；写入串行，最多 16 个待写帧、8 MiB 待写字节（包含长度前缀）。
- 截断、非法帧或进行中的 I/O 取消关闭连接；纯排队阶段的写取消不破坏连接。
- `ForLan` 接收局域网双向流；`ForOnline` 仅接收已经完成 TLS 握手的 `SslStream`。
  握手调用方必须使用证书链和主机名校验，不配置跳过验证的回调。
- 传输层不把帧中的 playerId 当作已认证身份；准入、房间绑定、序号去重由接入层负责。

模块测试覆盖 ABI、原协议 AUTH、Python owner 冲突、重启、资源错误、分片与连续帧、
发送背压、取消、非法长度及 TCP 回环收发；真实双客户端对战是后续独立验收门槛。

### 原协议三通道中继

`BattleGameRelay` 接收一个已经准入的 `BattleConnection`、固定的 roomId/matchId/playerId，
以及按 login/logic/instance 打开私有双向流的工厂。身份由房间分配提供，不从首帧或旧 AUTH 推断。

| 消息 | payload | 处理 |
| --- | --- | --- |
| OpenChannel | 空 | 打开对应原协议通道；每个角色在本次连接中最多打开一次 |
| GameData | 1–65536 字节 | 原样转发字节流；不假设 TCP 分段等于游戏消息边界 |
| CloseChannel | 空 | 关闭对应通道；原协议流 EOF 也会生成该消息 |

中继阶段双向序号各自从 1 开始并严格递增。错房间、错比赛、错玩家、重复序号、
未打开的通道和非法消息会结束中继；它不接受再次准入或世界采样消息。
关闭通道后已经在途的数据丢弃，重复关闭幂等，重新打开仍拒绝。
三个回传读取器各保留至多一个 64 KiB 数据块，序号分配和网络写入保持相同顺序。
停止后等待 `RunAsync` 完成，再停止原生宿主，以保证所有读取器退出。

模块测试使用真实 TCP，覆盖三通道字节回传、身份隔离、重放、EOF、取消、后端失败，
以及经 BattleProtocol 到原生宿主三个端口的旧 AUTH 握手。
中继目前仅是节点侧组件；启动器本地监听/地址桥接、原生连接与玩家关联、角色消息转换和
共享 MatchState 尚未接入。仅把多个原游戏连接转发给宿主不等于同局多人战斗。

## 角色视图与比赛计时器

`BattleActorMap` 将比赛角色映射为接收方视图：本机人类 RID 1、敌方 RID 2–11、
友方 RID 12–20，0 保留为场景/无目标。映射按全局 actorId 排序，具有双向唯一性，
不受名单顺序影响；同一 playerId 只对应一个人类角色，AI 的 playerId 为 0。
该工具尚未替换原协议消息中的角色字段，接入时必须同时处理目标引用和结算。

原有连接计时器保持不变。共享比赛逻辑可使用以下 transition：

```python
{
    "id": "match:7:round-end",
    "delayMs": 1000,
    "event": {
        "type": "timer", "connection": 0, "scope": "match",
        "matchId": 7, "name": "round-end"
    }
}
```

新增计时器的 matchId 必须等于提交后状态根节点的 `matchId`；名称必须匹配 ID。
`delayMs=-1` 可取消旧局计时器。比赛计时器不随单个连接断开而消失，比赛结束应显式取消；
已经入队的旧局事件在调用业务 handler 前丢弃。动作计时器仍从提交时点计算持续时间，
并保持既有输入/世界事件的因果顺序。停止宿主会清空全部计时器。

对应验证为 `test_match_timers.py` 和 `T7.RuntimeTests.exe --server-context PACKAGE_ROOT`。
现有单机业务仍发出连接计时器；共用 MatchState 和采样汇总尚待接入。
