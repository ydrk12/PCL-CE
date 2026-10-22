# PCL CE RegionFree personal fork

本仓库是 ydrk12 的第三方个人修改版，与 PCL-Community 及原作者独立。
保留上游作者署名、许可和其他功能，仅解除 Minecraft 下载、安装、修复及补全的地区限制。

## 下载

从本仓库的 Releases 下载 `PCL_CE_RegionFree_Release_x64.exe` 或 ARM64 版本。
需要与上游相同的 .NET 10 Desktop Runtime。
关闭启动器内置自动更新，后续从本 fork 的 Releases 下载，避免重新安装官方带限制版本。
本项目不改写用户现有的启动器设置或游戏文件。

## 分支和同步

- `dev`：上游 dev 的原样镜像，使用快进更新；遇到非预期分歧时停止。
- `personal`：默认分支，上游最新源码加最小地区补丁、自动化及本说明。
- `v<版本>-regionfree.1`：对应正式发布标签的源码加地区补丁，非 dev 最新代码。

每小时第 17 分钟检查上游，自 2.15.1 起处理尚未发布的正式 Release，跳过草稿及预发布。
也可以在 Actions 中手动运行 `Personal sync and stable release`，留空 tag 自动补处理，指定 tag 重试。
不在没有变化时创建空提交。GitHub 可能延迟调度，长期无仓库活动也可能停用定时工作流。

## 安装包如何产生

上游源码构建需要官方注入的登录等配置，本 fork 不持有这些配置。
因此发布程序读取官方正式版安装包，验证 GitHub 提供的 SHA256 后，只替换
`PCL.RestrictedFeaturePolicy.get_IsRestricted` 与 `Check(string, bool)` 的 IL 方法体。
其效果与 `.github/personal/region-policy.patch` 相同。

补丁程序不执行或解密原程序，不提取登录凭据或配置内容；验证所有其他字节未改变。
当前仅接受 .NET bundle 6.0、未压缩且非 ReadyToRun/非强命名的主程序集；结构变化会停止发布。
补丁不修改微软登录、游戏所有权校验、文件哈希校验和路径保护。

每个 Release 附带源码补丁、SHA256SUMS、两个架构的验证报告及 provenance.json，
记录官方输入文件、上游提交、对应源码和输出哈希。修改后的文件不再适用上游签名。
自动验证覆盖结构、修改范围和方法返回行为；不宣称完成真实账号登录或完整游戏启动测试。

## 出错处理

补丁不适用、下载校验失败、测试失败或附件不完整时不发布正式版。
已发布版本不会被覆盖；中途失败的草稿可在重试时补全。
可在 GitHub 通知设置中启用 Actions 失败邮件，然后将失败运行链接交给 Codex 排查。
本流程不需要额外 PAT、微软应用注册或上游的仓库 Secrets。
