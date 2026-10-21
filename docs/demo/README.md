# 演示图

`switch-list.svg` 和 `switch-confirm.svg` 是「在飞书里开关功能」的示意图，不是飞书客户端的截图，也不是真实会话。

两份 JSON 是程序 `card()` 用一份假配置生成的卡片正文，给主人先看结构：

- [switch-card.example.json](switch-card.example.json)：刚发出来的清单。顶部是「已开 6/9」，下面是「全部推荐设置」，再按日常、记忆、技能、高级分行。
- [switch-card-confirm.example.json](switch-card-confirm.example.json)：勾了「知识检索」和「早间待办」之后的同一张卡片。出现「确认这 2 项」和「取消」，并写着早间待办会发到群里。配置还没写。

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

`switch-list.svg` and `switch-confirm.svg` are diagrams of the settings card, not a real Feishu chat. The two JSON files are sample card bodies from a fake config. The confirm token and hash are placeholders. The collapsed panel lists config key names on purpose. Do not commit an unredacted screenshot.
