这套架构的设计水平非常高。通过 C++ Native Hook 拦截原版《刀锋铁骑》底层的刀刃物理扫掠（`BladeSweepHookCode.cpp`）生成 `ContactReport`，再配合服务端 `adjudication.rs` 的事件时间水位线（Event-Time Watermark）与 `motion.at(target_id, contact_at)` 历史姿态回溯，**你们实际上已经实现了一套工业级的“客户端命中上报 + 服务端时空回溯二次校验（Client-Side Hit Detection with Server-Side Rewind Verification）”混合权威架构**。

这完全不是外行那种“纯服务端傻等”的朴素状态同步，而是非常接近《骑马与砍杀2：霸主》与《Chivalry 2》的现代冷兵器网游同步模型。

但正如你在风险点中敏锐察觉到的，结合你贴出的三段核心代码，当前实现确实存在 **6 个深层架构隐患与逻辑漏洞**（其中 2 个会直接破坏格挡手感和公平性，2 个会导致作弊漏洞，2 个会引发并发性能雪崩）。以下是逐行代码级的深度诊断与重构方案：

---

## 一、 核心战斗裁决层的 3 个致命逻辑漏洞（`combat.rs` & `battle.rs`）

### 1. 格挡判定信任了攻击者的 `report.weapon_contact`（严重的手感与外挂漏洞）

在 `combat.rs` 的 `server_contact` 中，你是这样写格挡判定的：

```rust
if let Some(guard) = battle.at(target_id, contact_at) {
    guard_direction = [223, 224, 225, 226].iter().position(|state| *state == guard.state);
    // 漏洞所在：要求 report.weapon_contact 必须为 true！
    parried = report.weapon_contact && guard_direction.is_some();
}

```

* **为什么这是致命缺陷：**
1. **网络延迟导致“吞格挡”：** `report` 是**攻击者客户端**生成的。当防守者在本地极限按下格挡时，由于网络传输延迟（哪怕只有 40ms），在攻击者的客户端画面里，防守者**根本还没举起刀**！因此攻击者本地的 `BladeSweepHookCode` 扫到的是防守者的**身体肉体（`weapon_contact == false`）**，而不是刀刃。当这个包发到服务端时，哪怕你的 `InputWatermark` 等到了防守者的格挡包，让 `battle.at(target_id, contact_at)` 成功拿到了防守状态 `guard_direction.is_some()`，但由于 `report.weapon_contact` 是 `false`，`parried` 依然被判为 `false`，直接走到了 `else` 分支扣血！**这就解释了为什么你们引入了 70ms 水位线，玩家依然觉得“明明格挡了却被砍中”！**
2. **作弊者一行代码即可实现“刀刀破防”：** 攻击者只要在本地把上报 JSON 里的 `"weaponContact": true` 永远改成 `false`，服务端就永远不会触发 `parried = true`，所有人的格挡在他面前全部失效。


* **修复方案（解耦刀刃物理碰撞与方向性招架）：**
在服务端裁决中，只要防守者在 `contact_at`（加上宽容窗口）处于**正确的四向格挡状态（`guard_direction == Some(expected_dir)`）且朝向攻击者（Facing 校验通过）**，**无论攻击者上报的是砍中武器（`weapon_contact: true`）还是砍中身体（`weapon_contact: false`），服务端都必须强制判定为 `parried = true`！**
`report.weapon_contact` 仅应用于：当攻击者在本地就已经撞到对方武器弹刀时，快速确认弹刀；绝不能作为否定服务端权威格挡状态的必要条件。

### 2. 攻击者坐标采样时间错位：`motion.latest(actor, now)` vs `contact_at`

在 `combat.rs` 第 2 步空间校验中：

```rust
let contact_at = action.at.saturating_add(u64::from(report.elapsed_ms)).min(now);
// 隐患：攻击者取的是 now 的最新位置，而防守者取的是 contact_at 的历史位置！
let Some(source) = motion.latest(actor, now) else { return Ok(false); };
// ...
let Some(target) = motion.at(target_id, contact_at) else { return Ok(false); };

```

* **缺陷分析：** 在步战中 50~100ms 位移不大，但在《刀锋铁骑》的**高速马战（骑兵交错冲锋）**中，两匹马相对速度极快。如果 `ContactReport` 因为网络抖动或水位线等待，比实际发生时间 `contact_at` 晚了 80ms 才执行 `server_contact`，此时 `motion.latest(actor, now)` 取到的是骑兵冲过去 80ms 后的位置，而 `target` 取的是 80ms 前的位置，两者距离可能瞬间拉大超过 `4.0` 米，或者 `facing` 夹角已经背对，导致**高速骑兵交错劈砍大量被服务端误判为无效（吞刀）**。
* **修复方案：** 攻击者同样必须使用历史回溯姿态：`let Some(source) = motion.at(actor, contact_at)`（或者直接使用经过合法性校验的 `report.pose` 快照）。双方必须在**同一个历史时间切片 `contact_at**` 下计算相对距离、高度差与 `facing` 夹角。

### 3. 状态机 `Actor::advance` 存在 0~50ms 的随机量化误差（Tick 抖动吃帧）

在 `battle.rs` 中：

```rust
ActionPhase::Preparing { until } if now >= until => {
    // 隐患：使用 now 而不是 until 作为下一阶段的起点！
    active.phase = ActionPhase::Releasing { until: now + gesture.duration(2, self.weapon_id) };
}

```

* **缺陷分析：** 你的服务端 Tick 是 **20Hz（50ms 一次）**。假设某把快刀的前摇 `Preparing` 结束时间 `until` 是 `1005ms`，而服务端 Tick 触发在 `now = 1050ms`。这里直接用 `now + duration`，会导致前摇被平白无故拉长了 **45ms**！不仅如此，你的注释里写着 `// 每个 Tick 最多推进一个阶段`，如果遇到系统卡顿触发了 `MissedTickBehavior::Skip`，`now` 一次性跳了 120ms，原本应该在同一个时间段内完成的阶段被强行拆成多个 Tick，动作节奏就会像弹簧一样忽快忽慢，导致客户端动画与服务端状态窗口严重脱节。
* **修复方案：**
1. **时间戳精确传递（消除 Tick 量化误差）：** 阶段切换时，下一阶段的结束时间必须基于上一阶段的理论结束时间 `until` 累加，即 `until: until + gesture.duration(2, self.weapon_id)`，而不是 `now + duration`。
2. **改用 `while` 循环推进状态，并记录阶段转换历史：** 如果担心单次 Tick 跳过 `Releasing` 阶段导致无法采样，你的 `battle.sample_action` 和 `battle.at(target_id, contact_at)` 本来就应该查询**状态历史区间表（Interval History：`[start_ms, end_ms, state]`）**，而不是只查当前瞬间的 `self.active`！只要把每次阶段切换写入环形历史缓冲区，哪怕一个 Tick 内连续推进了两个阶段，`sample_action` 依然能精准查到在 `contact_at` 时刻角色正处于 `Releasing` 状态。



---

## 二、 因果序竞争（70ms 水位线不足）的架构级解法

你提到：“*在 `adjudication.rs` 中引入了 `InputWatermark`（等待防守方输入最多 70ms），但对延迟超过 70ms 的玩家，依然容易发生本地格挡却被砍中*”。

如果单纯把 `InputWatermark` 从 70ms 调大到 150ms，会导致所有低延迟玩家的出刀命中反馈都被强行延后 150ms，手感会变得极其迟钝。在动作网游中，解决高 Ping 格挡竞争的标准方案是“动态自适应水位线 + 乐观结算与回滚（Optimistic Adjudication & Rollback）”**，或者更务实的**“基于防守方 RTT 的动态宽限期”：

1. **方案 A（推荐，改动最小）：基于 `clock_sync.rs` 的动态对偶水位线**
既然你们已经写了 `clock_sync.rs` 估算每个连接的 RTT，那么在 `adjudication.rs` 挂起一个 `ContactReport` 时，不要用死板的固定 `70ms`，而是读取**当前受击目标（`target_id`）的实时单向延迟（$\text{OWD} \approx \text{RTT}_{\text{target}} / 2 + \text{Jitter}$）**，将等待窗口设为：

$$\text{WaitWindow} = \text{clamp}(\text{OWD}_{\text{target}} + 20\text{ms}, 40\text{ms}, 140\text{ms})$$



同时，校验防守方发来的 `ActionInput(Block)` 时，看它的客户端生成时间戳（经过 `clock_sync` 映射到服务端时间轴并扣除攻击者动画飞行时间）是否早于 `contact_at + 30ms`（给防守者 30ms 的反应宽容度）。这样两个 20ms 的玩家对战时，40ms 内立即出结果；遇到 100ms 的对手时，水位线自动延展到 70~80ms 保护防守方。
2. **方案 B：两阶段命中广播（Pre-Hit $\rightarrow$ Confirmed Hit/Parry）**
服务端收到 `ContactReport` 先立即校验距离，通过后立刻广播一个轻量级的 `PreContact`（客户端播放擦碰火花，不扣血、不进大硬直），然后开启 100ms 仲裁窗口等待防守方的 `Block` 包；窗口闭合时再下发最终是 `Damage` 还是 `ParryRebound`。

---

## 三、 并发性能与网络层重构建议（`transport.rs` & `protocol.rs`）

### 1. 拆分 `Coordinator` 单线程大循环：按 Room Actor 化

目前 `Coordinator::run` 是一个全局单线程 `tokio::select!`，所有房间的 `lobby.advance(now_ms)`、所有玩家的 `MotionRequest` 反序列化后都在这一个 Task 里串行跑。

* **重构路径（在 Rust Tokio 下非常容易改）：**
* **Coordinator 降级为纯路由器（Router）：** 只负责处理 `Connected`、`JoinRoom`、`Lobby` 列表查询。
* **每个对战房间（`Room`）独立 `tokio::spawn` 一个专属 Loop Task：** 每个 `Room` 拥有自己的 `mpsc::Receiver<RoomCommand>` 和独立的定时器。这样 10 个房间就可以直接跑满服务器的 10 个 CPU 核心，互不阻塞，彻底消灭全局 `queued_ms >= 50` 的问题。
* **对战房间 Tick 频率提升至 33ms (30Hz) 或 25ms (40Hz)：** 房间独立后，单房间只有几十人，Rust 跑纯数学校验耗时不到 0.5ms，完全可以把战斗房间的 Tick 从 50ms 缩减到 **25ms (40Hz)**，动作状态机的响应延迟立刻减半。



### 2. 协议层零成本二进制化：无需推翻重写，直接切 `MessagePack` 或 `bincode`

你担心 `serde_json` 解析 `PoseInput`（20~30Hz）和 `ContactReport` 开销过大。

* **最快落地方案：** 因为你已经全面使用了 Rust 的 `#[derive(Serialize, Deserialize)]`，客户端又是 C++ (`RoomGateway.cpp`) + Python (`online_*.py`)：
1. **高频移动信道 (`/ws/motion`) 直接改用固定字节二进制 Struct：** `PoseInput` 只有 `match_id (u64/u32)`, `input_seq (u32)`, `pos ([f32; 3])`, `heading (f32)`, `pitch (f32)`, `vel ([f32; 3])`, `on_ground (u8)`，总共才 **41 个字节**！在 C++ `MovementHookCode` / `RoomGateway` 里直接按 `pragma pack(1)` 塞进 WebSocket Binary Frame 发送，Rust 端用 `bytemuck` 或 `bytes::Buf` 零拷贝读取，CPU 消耗直接降为 **0**，连 Python 解释器都不用经过！
2. **主信道 (`/ws`) 切换为 `rmp-serde` (MessagePack)：** Rust 端只需把 `serde_json::from_slice` 换成 `rmp_serde::from_slice`，数据结构一行不用改；Python 端用 `msgpack.packb`，体积缩小 60%，解析速度提升 4~5 倍。



---

## 四、 服务端防穿墙打击（缺乏地图 Mesh）的轻量级替代方案

你提到服务端目前没有地图几何体碰撞校验，隔着柱子或城墙挥刀只要距离小于 4 米就会命中。对于复活项目来说，把原版游戏复杂的客户端地图导出并接入 `rapier3d` 或 `parry3d` 工作量较大，你可以分两步走：

1. **短期极简防作弊（利用双端交叉验证）：**
攻击者可以篡改自己的客户端跳过墙体检测，但**防守者的客户端没必要帮攻击者作弊**。当攻击者上报命中时，如果条件允许，或者对于可疑的穿墙角度，可以让防守者客户端的 `BladeParryContacts.cpp` 在收到受击预通知时顺手做一次从 `source.position` 到 `target.position` 的原生引擎射线检测（Line of Sight Raycast），若被静态建筑阻挡则上报异常。
2. **中期标准方案（导出稀疏体素/2.5D 阻挡网格或接入 `parry3d`）：**
不需要完整的物理引擎！只需要写个小脚本从客户端地图文件里提取静态建筑的 **低模三角面片（OBJ）**，在 Rust 服务端用纯几何计算库 **`parry3d`**（`rapier` 的底层几何库，无物理模拟开销，纯 BVH 树射线求交）加载一个静态 `TriMesh`。在 `combat.rs` 的 `server_contact` 中调用一次 `bvh.cast_ray(source_chest, target_chest)`，单次射线检测在 Rust 里耗时仅需 **几百纳秒（< 0.001ms）**，就能 100% 根除隔墙砍人。

---

需要我针对 **`combat.rs` 格挡判定与 `adjudication.rs` 动态水位线的 Rust 重构代码**，或者 **`Room` 拆分为独立 Tokio Actor 的代码结构** 写一份可直接替换的示例吗？
