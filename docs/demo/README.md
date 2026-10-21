# 演示图

这些图是「在飞书里开关功能」的示意图，不是飞书客户端的截图，也不是真实会话。图按同一份卡片 JSON 绘制，中文指定 Noto Sans CJK SC。图里没有 JSON 写不出来的渐变头图或插画。

程序发出去的是白底方案。彩色分组是 `card(..., style="color")` 生成的另一份 JSON，结构相同，只是每一组多了一层淡底色。

- [switch-card.example.json](switch-card.example.json)：白底清单。已开 6/9，大号数字、绿点和九格进度。图标各自一块浅底。底部主按钮是停用的「保存 0 项改动」。
- [switch-list.svg](switch-list.svg)、[switch-mobile.svg](switch-mobile.svg)：上面这份 JSON 的宽屏和窄屏。
- [switch-card-confirm.example.json](switch-card-confirm.example.json)：白底上勾了「查资料再回答」和「早上发待办」。主按钮是「保存 2 项改动」，并写着早上发待办会发到群里。配置还没写。图是 [switch-confirm.svg](switch-confirm.svg)。
- [switch-card-b.example.json](switch-card-b.example.json)：彩色分组的同一张清单。[switch-list-b.svg](switch-list-b.svg) 和 [switch-mobile-b.svg](switch-mobile-b.svg) 是它的宽屏和窄屏。

示例里的确认记号是 `example-token-not-real`，校验值是 `example-hash-not-real`。文件名写成「示例配置.json」。折叠的「高级」里故意有 `report.enabled` 这种名字，展开才看得到。没有密钥、群号、open_id 或人名。

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

The SVG files are diagrams drawn from the sample card JSON, not a real Feishu chat. Chinese text uses Noto Sans CJK SC. The plain scheme is what the bot sends. The color scheme is a second JSON file with soft group fills. The confirm token and hash are placeholders. The collapsed panel lists config key names on purpose. Do not commit an unredacted screenshot.
