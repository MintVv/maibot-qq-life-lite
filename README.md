# 白饭 QQ 生活轻扩展

MaiBot 插件：在限定 QQ 群收集经模型挑选的趣图，生成 QQ 空间说说草稿，并按明确回执发布。头像和昵称修改也必须经过外部操作审批。

## 安全默认值

- `life.mode = "draft"`：说说、相册上传和资料修改均不直接执行；通过 `/ql approve <任务ID>` 逐项确认。
- 不会回溯插件首次启动前的群图片；只读取 `source_groups` 中经允许的群聊消息。
- 不读取其他私聊或个人消息作为公开发布素材。
- QQ 空间账号需匹配 `life.bot_uin`；不会把 NapCat 登录 Cookie 写入插件数据目录。
- 不设置目标相册时，只保存本地收藏；不会把相册上传伪装成说说附图。
- 发布请求结果不明时标记 `unknown`，禁止自动重发，需人工核对。
- 成功发布后会通过登记过的 QQ 私聊会话通知主人。通知回执不明时不会重复私聊提醒或重新发布说说。

## 安装

从 MaiBot 插件市场安装，或将插件目录放入 MaiBot 实例的 `plugins/` 目录。需要适配器提供下列能力：

- `adapter.napcat.system.get_login_info`
- `adapter.napcat.account.get_cookies`
- `adapter.napcat.qzone.get_qzone_msg_list`
- `adapter.napcat.account.set_qq_profile`
- `adapter.napcat.account.set_qq_avatar`
- Qzone HTTP 登录 Cookie（有效的 `p_skey`）

插件只经过 MaiBot 1.3.17、SDK 2.19.0 的真实加载验证。只读 QQ 空间列表读取和群图片解码通过实机检查；空间写入、相册上传和资料修改依赖当前 QQ 客户端及服务端接口，仍应在自己的账号上先逐项审批验证。

## 配置

复制 `config.toml` 到 MaiBot 插件配置后，填写：

- `life.owner_ids`：主人 QQ 号。留空时没有人能运行 `/ql` 管理命令。
- `life.bot_uin`：机器人 QQ 号。必须与当前适配器登录账号一致。
- `life.source_groups`：允许从中读取图片的群号。默认为空，不读取任何群。
- `life.album_id`：实际目标空间相册 ID。留空时不上传趣图到空间。
- `life.mode`：保持 `draft`，直到空间能力逐项通过验证。

不要将包含个人账号号段或群号的服务器配置提交到公开仓库。

## 使用

- `/ql status`：主人私聊查看模式、收藏、草稿和待核对任务。
- `/ql probe`：只读检查 QQ 账号、空间 Cookie、说说/相册读取和获准群图片解码。
- `/ql albums`：列出当前 QQ 账号可读取的空间相册。
- `/ql album <ID>`：选择已核验的目标相册。
- `/ql list`：查看待审批任务。
- `/ql approve <ID>`：执行一条草稿；不会执行已确认、失败、取消或 `unknown` 任务。
- `/ql cancel <ID>`：取消尚未执行的草稿。
- `/ql resolve <ID> confirmed|failed`：外部核实后结清 `unknown` 任务；不会自动重试。
- `/ql mode draft|auto`：切换执行策略。`auto` 仍受每日上限、冷却、静默时段及明确 API 回执约束。

每次确认发布后，会向 `owner_ids` 配置的主人发私聊通知并附 QQ 空间链接。通知发送失败不会重发说说。

## 开发与测试

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
```

MIT License，详见 `LICENSE`。
