# 预览渠道离线授权

预览渠道使用管理员签发的离线授权码，不是 6 位 TOTP。授权绑定当前电脑和 Windows 用户，自签发起 **7 天**有效，不需要 QQ 登录、网络验权或管理员权限。

## 玩家操作

1. 在“设置 → 启动器设置 → 更新渠道”选择预览版。
2. 复制弹窗中的申请码，线下联系管理员。
3. 粘贴管理员返回的完整授权码，阅读风险提示，点击“验证并切换”。

有效期内再次进入预览只需确认风险。重启、更新和重复导入同一授权码不会延长有效期。切回正式渠道不清除有效授权；到期后重新选择预览并申请新码。

授权只影响渠道准入，不改变已安装构建身份，不停止游戏，也不强制安装正式版。新门禁版本首次运行会重置已有预览订阅；后续正常升级不重复重置有效授权。

## 管理员初始化

在管理员自己的 Windows 账户下运行。脚本支持 Windows PowerShell 5.1 和 PowerShell 7，仅使用系统加密组件。

```powershell
$authority = Join-Path $env:LOCALAPPDATA 'T7-Rekindle-Authority'
New-Item -ItemType Directory -Force -Path $authority | Out-Null
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/preview-access.ps1 -Action Init `
  -PrivateKeyPath "$authority\preview.private.json" `
  -PublicKeyPath "$authority\preview.public.txt"
```

- 初始化只创建新文件，已有密钥不覆盖。私钥应放在仓库外；仓库内仅接受已忽略的 `.local/`。
- 私钥使用 DPAPI `CurrentUser` 保护，仅在管理员原有 Windows 账户和保护环境中读取。文件复制不是跨设备密钥迁移方案；丢失保护环境需要重新建立签发密钥并更新启动器公钥。
- 公钥文件是可公开的单行 Base64 CNG `EccPublicBlob`。私钥文件和真实授权码不进入仓库、Actions 配置或发布包。
- 首次初始化后妥善保存私钥；如公钥文件写入失败，私钥封装中的 `publicKey` 字段可用于恢复公钥，无需重新生成私钥。

## 配置发布公钥

将公钥文件的内容设置为仓库 Actions **variable** `T7_PREVIEW_ACCESS_PUBLIC_KEY`。正式和预览渠道使用同一变量；私钥不上传至 CI。

本地构建使用同名环境变量：

```powershell
$publicPath = Join-Path $env:LOCALAPPDATA 'T7-Rekindle-Authority\preview.public.txt'
$env:T7_PREVIEW_ACCESS_PUBLIC_KEY = [System.IO.File]::ReadAllText($publicPath, [System.Text.Encoding]::UTF8).Trim()
python scripts/build.py --project managed
& '.\artifacts\bin\T7.ManagedHarness\x64\Release\net48\T7.ManagedHarness.exe' --preview-key-check
```

公钥通过程序集元数据内置，运行时不读取环境变量或外部公钥文件。修改公钥后必须重新构建。默认分支发布及 tag 发布会在打包之前检查实际编译进启动器的公钥；缺失或无效时停止发布。普通本地／PR 构建允许未配置公钥，此时正式渠道正常使用，预览授权保持关闭。

轮换公钥需重新构建并发布两个渠道；旧私钥签发的授权不被新公钥接受。离线客户端没有即时远程吊销机制。

## 签发 7 天授权

先在线下确认申请人及其申请码，再在持有私钥的 Windows 账户下运行：

```powershell
$privatePath = Join-Path $env:LOCALAPPDATA 'T7-Rekindle-Authority\preview.private.json'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/preview-access.ps1 -Action Issue `
  -PrivateKeyPath $privatePath -RequestCode 'T7PR1.SUBJECT_HASH'
```

用收到的完整申请码替换 `T7PR1.SUBJECT_HASH`。命令只输出一行 `T7PA1.…` 授权码，将整行发给申请人。固定签发 7 天，没有自定义天数参数；延迟导入会减少剩余有效时间。

## 存储和协议

- 申请码：`T7PR1.<subjectHash>`。摘要为 `Base64Url(SHA256(UTF8("T7-Rekindle/preview/v1\n" + MachineGuid + "\n" + WindowsSID)))`；GUID 使用小写 `D` 格式，SID 使用 Windows 标准格式。不显示原始机器或用户标识。
- 授权码：`T7PA1.<payloadBase64Url>.<signatureBase64Url>`，Base64Url 无填充。签名为 ECDSA P-256／SHA-256，64 bytes 的 `r || s` 格式，签名输入为前两段及其中的句点。
- UTF-8 JSON 载荷包含 `policyVersion: 1`、`keyId`、`subjectHash`、`issuedAt`、`expiresAt`。`keyId` 为公钥 blob 的 SHA-256 Base64Url 摘要；时间为 Unix 秒，`expiresAt = issuedAt + 604800`。验证原始签名输入，不重新序列化。
- 本机授权与策略迁移标记独立保存在 `%LOCALAPPDATA%\T7-Rekindle\preview-access.json`，使用 DPAPI `CurrentUser` 和同目录原子替换。现有 `settings.json` schema 保持 1；单独修改渠道字段不构成授权。
- 校验使用本机 UTC，最多容忍签发时间提前 5 分钟，到期不宽限。系统时间异常时应先校正时间；该离线门禁不构成可信时间或抗本地管理员篡改机制。
- 授权只约束包含门禁的新启动器；公开预览安装包、仍在运行的旧版启动器及程序修改不由此机制封锁。

## 到期和故障

到期立即停止新的预览检查、历史查询、下载和安装。已有检查、下载和安装允许结束；暂停下载仍可继续或取消。操作结束后回到正式渠道，清除旧预览操作入口并检查正式更新。已下载但尚未发起安装的预览包不再通过启动器安装。

首次策略迁移覆盖主配置、备份和旧渠道文件的读取路径，只重置渠道，保留其他设置。迁移保存失败时当前仍使用正式渠道，下次启动重试；两份设置均损坏时保留原文件。授权保存成功但渠道保存失败时，保留原渠道，重试可复用已保存的授权。

授权文件损坏、复制到不同电脑／用户、签名不匹配或到期时，重新申请或导入有效授权。日志不记录授权码、原始机器标识或私钥。

定向测试：

```powershell
& '.\artifacts\bin\T7.ManagedHarness\x64\Release\net48\T7.ManagedHarness.exe' --preview-access-tests
python -m pytest -q tests/python/test_preview_access.py
```
