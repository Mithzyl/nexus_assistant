# 通知与天气供应商验证状态

更新：2026-10-24。当前用户没有 Apple Developer Program / APNs / WeatherKit / Maps
服务端凭证。通知只走 Bark；APNs 代码保留但 outbox 路径不可达。

## 1. 当前已实现

| 项目 | 状态 | 说明 |
|---|---|---|
| Bark 单账号配置 | 已实现 | 私有 env；owner 来自 `/auth/me` 并人工确认 |
| Bark 显式测试发送 | 已实现（MockTransport） | accepted/failed/unknown 区分 |
| Bark 自动 outbox | 已实现（Mock） | accepted_by_bark；失败不转 APNs；unknown 不重试 |
| Bark 未配置/非 owner/关闭 | 已实现 | cancelled / bark_not_enabled，不发送任何渠道 |
| APNs | 代码保留，当前不可达 | 无 APNs 凭证要求 |
| WeatherKit / Maps | 代码保留，当前不作为启动前置 | 未配置时 provider unavailable，不伪造数据 |
| Open-Meteo | 尚未实现 | 下一步按既定方案接入 |
| 真实 Bark 发送/手机收到 | 未验证 | 用户在 App 绑定 Key 并主动测试后确认 |
| App Bark 设置/深链/实时活动 | 未开始 | 下一阶段的 iOS 工作 |

启动服务不再要求 APNs、WeatherKit、Maps 或 Apple `.p8`。生产环境仍要求
`ASSISTANT_COMMUTE_PROVIDER_MODE=live`，但缺失 Apple provider 只导致对应能力
`unavailable`，不会回退 Mock。

## 2. 必须由用户完成的真实 Bark 验证

1. 在 App 登录并进入“我的 → 通知设置”；
2. 输入 Bark Device Key，确认后端保存成功；
3. 杀掉 App 重开，确认状态从后端恢复；
4. 保持自动提醒关闭，主动点击一次测试；
5. 分别记录后端 `accepted/failed/unknown` 和 iPhone 是否收到。

Key 明文保存在后端数据库的开发期方案，不允许出现在页面、日志、异常或 Git。
未收到手机通知时，不能把 `accepted` 写成“已送达”。

## 3. 仍未验证

- 真实 Bark 服务接受与 iPhone 通知显示；
- 点击通知进入 PA 通勤详情（自定义 URL Scheme 尚未实现）；
- Open-Meteo 真实天气、归属和预报时段；
- App 原生 MapKit 导航与实时活动；
- 真机冷启动/热启动、未登录、记录不存在/他人记录/过期建议；
- 真机通知被 Focus/系统延迟的实际情况。

## 4. 判定边界

`accepted_by_bark` 只证明 Bark HTTP 接口接受请求，不证明手机收到。
`unknown` 不自动重试；`failed` 不切换 APNs。当前没有 APNs 送达证据，也不声称有。
