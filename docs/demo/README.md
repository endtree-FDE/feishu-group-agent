# 演示图

`switch-list.svg`、`switch-confirm.svg` 和 `switch-mobile.svg` 是「在飞书里开关功能」的示意图，不是飞书客户端的截图，也不是真实会话。图里的中文指定 Noto Sans CJK SC。

两份 JSON 是程序 `card()` 用一份假配置生成的卡片正文，给主人先看结构：

- [switch-card.example.json](switch-card.example.json)：刚发出来的清单。浅色状态条是「已开 6/9」。每一组一块浅灰底。一行是标准图标、名字、开或关、浅灰说明和「详情」。底部主按钮是停用的「保存 0 项改动」，次按钮是「全部推荐设置」。
- [switch-card-confirm.example.json](switch-card-confirm.example.json)：勾了「知识检索」和「早间待办」之后的同一张卡片。主按钮是「保存 2 项改动」，下面仍是「全部推荐设置」和「取消」，并写着早间待办会发到群里。配置还没写。
- [switch-mobile.svg](switch-mobile.svg)：同一张清单的窄屏。说明在名字下面，开关留在右侧，两个按钮各占一行。

示例里的确认记号是 `example-token-not-real`，校验值是 `example-hash-not-real`。文件名写成「示例配置.json」。折叠的「配置名字」里故意有 `report.enabled` 这种名字，展开才看得到。没有密钥、群号、open_id 或人名。

飞书 7.20 及以上按 JSON 2.0 画这张卡。更早的客户端只能看到标题，并提示升级。本仓库用长连接收点击，再用更新消息换上新卡片，不另发一条。官方回调响应是下面这种形状，`data` 就是上面的卡片 JSON：

```json
{
  "toast": {"type": "info", "content": "先确认才会改"},
  "card": {"type": "raw", "data": "见 switch-card-confirm.example.json"}
}
```

换上真实截图时请遮住这些内容：

- 群名、人名和头像
- 群号、open_id、文档编号
- 客户名、手机号、邮箱和任何密钥

不要提交一张未打码的真实会话截图。

## English

`switch-list.svg`, `switch-confirm.svg`, and `switch-mobile.svg` are diagrams of the settings card, not a real Feishu chat. Chinese text in the diagrams uses Noto Sans CJK SC. The two JSON files are sample card bodies from a fake config. The confirm token and hash are placeholders. The collapsed panel lists config key names on purpose. Do not commit an unredacted screenshot.
